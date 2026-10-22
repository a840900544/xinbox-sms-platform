"""Authenticated SMS console. The collector database is a read-only source."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import forwarding
import wecom_commands

os.umask(0o077)
DATA = Path(os.getenv("SMS_DATA_DIR", "/data"))
SOURCE = Path(os.getenv("SMS_SOURCE_DB", "/source/research-sms.sqlite3"))
SOURCE_KEY = Path(os.getenv("SMS_SOURCE_KEY", "/source/sms-storage.key"))
STATIC = Path(os.getenv("SMS_STATIC_DIR", "/app/static"))
DB = DATA / "platform.sqlite3"
COOKIE = "sms_console_session"
TTL = 8 * 3600
SECURE_COOKIE = os.getenv("SMS_SECURE_COOKIE", "false").lower() == "true"
SEARCH_LIMIT = 5000
sync_state = {"state": "waiting", "last_sync": None, "imported": 0, "error": None}
sync_lock = asyncio.Lock()


@contextmanager
def database():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return "pbkdf2_sha256$310000$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(value).decode()


def password_matches(password, saved):
    try:
        _, iterations, salt, expected = saved.split("$")
        value = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(value, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def cipher():
    return Fernet((DATA / "messages.key").read_bytes())


def key_hash(value):
    return hmac.new((DATA / "messages.key").read_bytes(), value.encode(), hashlib.sha256).hexdigest()


def call_key(item, fallback):
    ident = item.get('call_id')
    return key_hash(json.dumps([item.get('account'), ident], ensure_ascii=False)) if ident else fallback


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    if not (DATA / "messages.key").exists():
        (DATA / "messages.key").write_bytes(Fernet.generate_key())
    with database() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',disabled INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS login_failures (ip_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS failures_time ON login_failures(created_at);
        CREATE TABLE IF NOT EXISTS registration_attempts(ip_hash TEXT NOT NULL,created_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS registration_time ON registration_attempts(created_at);
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id INTEGER NOT NULL,
            account_key TEXT NOT NULL, encrypted_record BLOB NOT NULL,
            received_at TEXT NOT NULL, ingested_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0,
            UNIQUE(source, source_id));
        CREATE INDEX IF NOT EXISTS messages_account ON messages(account_key, id DESC);
        CREATE INDEX IF NOT EXISTS messages_time ON messages(received_at DESC);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL,
            message_id INTEGER, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS managed_numbers (
            id TEXT PRIMARY KEY, number_key TEXT UNIQUE NOT NULL,
            encrypted_profile BLOB NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS control_jobs (
            id TEXT PRIMARY KEY, number_id TEXT NOT NULL REFERENCES managed_numbers(id),
            action TEXT NOT NULL, encrypted_payload BLOB, state TEXT NOT NULL,
            result TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS call_events (
            id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
            account_key TEXT NOT NULL, encrypted_record BLOB NOT NULL,
            received_at TEXT NOT NULL);
        """)
        db.executescript("CREATE TABLE IF NOT EXISTS site_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS announcements(id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,published INTEGER NOT NULL,popup INTEGER NOT NULL,once_only INTEGER NOT NULL,revision INTEGER NOT NULL,updated_at INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS announcement_reads(user_id INTEGER NOT NULL,announcement_id TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(user_id,announcement_id));")
        db.execute("INSERT OR IGNORE INTO site_settings VALUES('registration_open','true')")
        db.execute('CREATE TABLE IF NOT EXISTS number_credentials(number_id TEXT PRIMARY KEY,encrypted_record BLOB NOT NULL,updated_at INTEGER NOT NULL)')
        columns={r[1] for r in db.execute('PRAGMA table_info(users)')}
        if 'role' not in columns: db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
        if 'disabled' not in columns: db.execute('ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0')
        db.execute('CREATE TABLE IF NOT EXISTS number_owners (number_key TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),deleted INTEGER NOT NULL DEFAULT 0)')
        db.execute('CREATE TABLE IF NOT EXISTS channel_owners (channel_id TEXT PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id))')
        owner=db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if owner:
            db.execute('INSERT OR IGNORE INTO number_owners(number_key,user_id) SELECT number_key,? FROM managed_numbers',(owner[0],))
            db.execute('INSERT OR IGNORE INTO number_owners(number_key,user_id) SELECT DISTINCT account_key,? FROM messages',(owner[0],))
            db.execute('INSERT OR IGNORE INTO number_owners(number_key,user_id) SELECT DISTINCT account_key,? FROM call_events',(owner[0],))
        if 'call_key' not in {r[1] for r in db.execute('PRAGMA table_info(call_events)')}:
            db.execute('ALTER TABLE call_events ADD COLUMN call_key TEXT')
        for row in db.execute('SELECT id,fingerprint,encrypted_record FROM call_events WHERE call_key IS NULL').fetchall():
            item = json.loads(cipher().decrypt(row['encrypted_record']))
            db.execute('UPDATE call_events SET call_key=? WHERE id=?', (call_key(item,row['fingerprint']),row['id']))
        db.execute('CREATE INDEX IF NOT EXISTS call_events_group_idx ON call_events(call_key,id)')
        db.execute('CREATE INDEX IF NOT EXISTS call_events_account_idx ON call_events(account_key,id)')
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            path = Path(os.getenv("SMS_ADMIN_PASSWORD_FILE", "/run/secrets/admin_password"))
            if not path.is_file():
                raise RuntimeError("Initial admin password file is required")
            password = path.read_text().strip()
            if len(password) < 6:
                raise RuntimeError("Initial password must be at least 6 characters")
            db.execute("INSERT INTO users(username,password_hash,created_at,role) VALUES(?,?,?,'admin')",
                       ("admin", password_hash(password), int(time.time())))


def utc_text(value):
    text = str(value or "").strip()
    try:
        if re.fullmatch(r"\d{10,13}(\.\d+)?", text):
            stamp = float(text)
            if stamp > 1e11:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def import_source():
    if not SOURCE.is_file() or not SOURCE_KEY.is_file():
        sync_state.update(state="unavailable", error="短信数据源尚未连接")
        return
    source = sqlite3.connect(SOURCE.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    source.row_factory = sqlite3.Row
    try:
        source.execute("PRAGMA query_only=ON")
        with database() as db:
            cursor = db.execute("SELECT COALESCE(MAX(source_id),0) FROM messages WHERE source='jegotrip'").fetchone()[0]
        rows = source.execute("SELECT id,encrypted_record,ingested_at FROM received WHERE id>? ORDER BY id LIMIT 500", (cursor,)).fetchall()
    finally:
        source.close()
    source_crypto, target_crypto = Fernet(SOURCE_KEY.read_bytes()), cipher()
    imported = 0
    with database() as db:
        for row in rows:
            item = json.loads(source_crypto.decrypt(row["encrypted_record"]))
            if not all(isinstance(item.get(k), str) for k in ("account", "sender", "body", "time")):
                raise ValueError("Invalid collector record")
            ingested = utc_text(row["ingested_at"])
            if ingested is None:
                raise ValueError("Invalid ingestion timestamp")
            received = utc_text(item["time"])
            item["source_time_parsed"] = received is not None
            blob = target_crypto.encrypt(json.dumps(item, ensure_ascii=False).encode())
            result = db.execute("INSERT OR IGNORE INTO messages(source,source_id,account_key,encrypted_record,received_at,ingested_at) VALUES('jegotrip',?,?,?,?,?)",
                                (row["id"], key_hash(item["account"]), blob, received or ingested, ingested))
            imported += result.rowcount
            if result.rowcount:
                forwarding.enqueue(db,result.lastrowid)
        db.execute("DELETE FROM sessions WHERE expires_at<?", (int(time.time()),))
        db.execute("DELETE FROM login_failures WHERE created_at<?", (int(time.time()) - 900,))
    sync_state.update(state="connected", error=None, last_sync=int(time.time()), imported=imported)
    bridge_path = SOURCE.parent / "bridge-status.json"
    if bridge_path.is_file():
        bridge = json.loads(bridge_path.read_text())
        if bridge.get("state") != "connected" or time.time() - bridge.get("heartbeat", 0) > 30:
            sync_state.update(state="error", error="数据源更新已中断，当前显示的是已有记录")


async def synchronize():
    async with sync_lock:
        try:
            await asyncio.to_thread(import_source)
        except (sqlite3.Error, OSError, ValueError, InvalidToken):
            # Never put message content, credentials or filesystem paths in logs/API errors.
            sync_state.update(state="error", error="短信同步失败，请检查数据源连接和密钥")


async def sync_loop():
    while True:
        await synchronize()
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    initialize()
    with database() as db: app.state.init_wecom_commands(db)
    forwarding.initialize()
    await synchronize()
    task = asyncio.create_task(sync_loop())
    outbound = asyncio.create_task(forwarding.loop())
    yield
    task.cancel()
    outbound.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="信匣 SMS Console", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def protections(request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.netloc != request.headers.get("host"):
            return JSONResponse({"detail": "请求来源不允许"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN" if request.url.path == '/login-captcha' else "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path == '/login-captcha':
        response.headers['Content-Security-Policy'] = "default-src 'none'; script-src 'unsafe-inline' https://*.aliyuncs.com https://*.alicdn.com; style-src 'unsafe-inline'; img-src https: data: blob:; connect-src https://*.aliyuncs.com https://*.alicdn.com; worker-src blob:; frame-src https:; frame-ancestors 'self'; base-uri 'none'; form-action 'none'; sandbox allow-scripts"
        response.headers['Cache-Control'] = 'no-store'
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def current_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 200:
        raise HTTPException(401, "请先登录")
    with database() as db:
        row = db.execute("SELECT s.*,u.username,u.role,u.disabled FROM sessions s JOIN users u ON s.user_id=u.id WHERE token_hash=? AND expires_at>?",
                         (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    if not row or row["disabled"]:
        raise HTTPException(401, "登录已过期，请重新登录")
    if request.method not in ("GET", "HEAD"):
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"]):
            raise HTTPException(403, "请求校验失败，请刷新页面")
    return {**dict(row),'admin_scope':row['role']=='admin' and request.headers.get('x-console-scope')=='admin'}


def admin_user(user=Depends(current_user)):
    if not user.get('admin_scope'): raise HTTPException(403,'请从管理后台执行此操作')
    return user


def account_scope(user,column='account_key'):
    if user.get('admin_scope'):
        return column+' NOT IN (SELECT number_key FROM number_owners WHERE deleted=1)',[]
    return column+' IN (SELECT number_key FROM number_owners WHERE user_id=? AND deleted=0)',[user['user_id']]


def require_number(db,ident,user,deleted=False):
    row=db.execute('SELECT n.*,o.user_id AS owner_id,o.deleted FROM managed_numbers n LEFT JOIN number_owners o ON o.number_key=n.number_key WHERE n.id=?',(ident,)).fetchone()
    if not row or (row['deleted'] and not deleted) or (not user.get('admin_scope') and row['owner_id']!=user['user_id']):
        raise HTTPException(404,'号码不存在')
    return row


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class Register(BaseModel):
    username:str=Field(min_length=2,max_length=64)
    password:str=Field(min_length=6,max_length=128)


@app.post('/api/auth/register')
async def register_user(data:Register,request:Request):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,64}',data.username):raise HTTPException(422,'用户名使用2至64位字母、数字、点、横线或下划线')
    now=int(time.time())
    ip_hash=key_hash(request.headers.get('x-real-ip') or (request.client.host if request.client else 'unknown'))
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT value FROM site_settings WHERE key='registration_open'").fetchone()[0]!='true':raise HTTPException(403,'管理员已关闭公开注册')
        db.execute('DELETE FROM registration_attempts WHERE created_at<?',(now-86400,))
        count=db.execute('SELECT COUNT(*) FROM registration_attempts WHERE ip_hash=? AND created_at>?',(ip_hash,now-3600)).fetchone()[0]
        total=db.execute('SELECT COUNT(*) FROM registration_attempts WHERE created_at>?',(now-3600,)).fetchone()[0]
        if count>=5 or total>=100:raise HTTPException(429,'注册操作频繁，请稍后再试')
        db.execute('INSERT INTO registration_attempts VALUES(?,?)',(ip_hash,now))
    value=await asyncio.to_thread(password_hash,data.password)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT value FROM site_settings WHERE key='registration_open'").fetchone()[0]!='true':raise HTTPException(403,'管理员已关闭公开注册')
        try:result=db.execute("INSERT INTO users(username,password_hash,created_at,role,disabled) VALUES(?,?,?,'user',0)",(data.username,value,now))
        except sqlite3.IntegrityError:raise HTTPException(409,'用户名已存在，请更换用户名')
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'user_register',?)",(result.lastrowid,now))
    return {'ok':True,'message':'注册成功，请登录'}


@app.post("/api/auth/login")
async def login(data: Login, request: Request, response: Response):
    now = int(time.time())
    ip_hash = key_hash(request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown"))
    with database() as db:
        attempts = db.execute("SELECT COUNT(*) FROM login_failures WHERE ip_hash=? AND created_at>?", (ip_hash, now - 900)).fetchone()[0]
        if attempts >= 8:
            raise HTTPException(429, "尝试次数过多，请稍后再试")
        user = db.execute("SELECT * FROM users WHERE username=?", (data.username,)).fetchone()
    valid = await asyncio.to_thread(password_matches, data.password, user["password_hash"] if user else password_hash("unavailable-account"))
    if not user or not valid or user["disabled"]:
        with database() as db:
            db.execute("INSERT INTO login_failures VALUES(?,?)", (ip_hash, now))
        raise HTTPException(401, "账号或密码不正确")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with database() as db:
        db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf, now + TTL))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'login',?)", (user["id"], now))
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, secure=SECURE_COOKIE, samesite="strict", path="/")
    return {"username": user["username"], "csrf": csrf, "role": user["role"]}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return {"username": user["username"], "csrf": user["csrf"], "role": user["role"]}


@app.post("/api/auth/logout")
def logout(response: Response, user=Depends(current_user)):
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (user["token_hash"],))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


SENSITIVE = re.compile(r"验证码|校验码|动态码|登录码|安全码|认证码|口令|verification\s*code|security\s*code|\botp\b|passcode|one.time.password", re.I)


def serialize_message(row, reveal=False):
    item = json.loads(cipher().decrypt(row["encrypted_record"]))
    body = item["body"]
    sensitive = bool(SENSITIVE.search(body))
    # Hide the complete sensitive body, including alphabetic/long codes that a
    # digits-only replacement would miss. Explicit reveal remains audited.
    masked = "敏感短信内容已隐藏，进入详情可按需查看原文。" if sensitive else body
    return {"id": row["id"], "account_key": row["account_key"], "recipient": item["account"],
            "sender": item["sender"], "body": body if reveal else masked,
            "sensitive": sensitive, "redacted": sensitive and not reveal,
            "received_at": row["received_at"], "ingested_at": row["ingested_at"],
            "source_time_raw": item["time"], "source_time_parsed": item.get("source_time_parsed", False),
            "source": "无忧行", "offline": bool(item.get("offline")),
            "is_read": bool(row["is_read"]), "archived": bool(row["archived"]), "sync_status": "synced"}


@app.get("/api/overview")
def overview(user=Depends(current_user)):
    where,args=account_scope(user)
    with database() as db:
        counts=db.execute("SELECT COUNT(*) total,COALESCE(SUM(is_read=0 AND archived=0),0) unread,COALESCE(SUM(archived),0) archived,COUNT(DISTINCT account_key) accounts FROM messages WHERE "+where,args).fetchone()
        day_count=db.execute("SELECT COUNT(*) FROM messages WHERE "+where+" AND date(received_at,'+8 hours')=date('now','+8 hours')",args).fetchone()[0]
        accounts=[]
        for row in db.execute('SELECT account_key,COUNT(*) total,MAX(id) latest FROM messages WHERE '+where+' GROUP BY account_key',args):
            msg=db.execute('SELECT encrypted_record FROM messages WHERE id=?',(row['latest'],)).fetchone()
            accounts.append({'id':row['account_key'],'number':json.loads(cipher().decrypt(msg[0]))['account'],'total':row['total']})
    return {**dict(counts),'today':day_count,'numbers':accounts,'source':dict(sync_state) if user.get('admin_scope') else {'state':'connected','last_sync':sync_state.get('last_sync')},'collector':collector_status() if user.get('admin_scope') else {}}



@app.get("/api/messages")
def messages(q: str = "", account: str = "", status: str = "all", start: str = "", end: str = "", page: int = 1, page_size: int = 20, user=Depends(current_user)):
    if len(q) > 200 or status not in ("all", "unread", "read", "archived") or not 1 <= page_size <= 100 or not 1 <= page <= 100000:
        raise HTTPException(422, "筛选参数不正确")
    scope, params = account_scope(user)
    clauses = [scope]
    clauses.append("archived=?")
    params.append(1 if status == "archived" else 0)
    if status in ("unread", "read"):
        clauses.append("is_read=?")
        params.append(1 if status == "read" else 0)
    if account:
        clauses.append("account_key=?")
        params.append(account)
    for value, op in ((start, ">="), (end, "<=")):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(422, "日期格式不正确")
            clauses.append("date(received_at,'+8 hours')" + op + "?")
            params.append(value)
    where = " AND ".join(clauses)
    with database() as db:
        if not q.strip():
            total = db.execute("SELECT COUNT(*) FROM messages WHERE " + where, params).fetchone()[0]
            rows = db.execute("SELECT * FROM messages WHERE " + where + " ORDER BY received_at DESC,id DESC LIMIT ? OFFSET ?", [*params, page_size, (page - 1) * page_size]).fetchall()
            result = [serialize_message(r) for r in rows]
            limited = False
        else:
            rows = db.execute("SELECT * FROM messages WHERE " + where + " ORDER BY received_at DESC,id DESC LIMIT ?", [*params, SEARCH_LIMIT + 1]).fetchall()
            limited = len(rows) > SEARCH_LIMIT
            matches = []
            needle = q.strip().casefold()
            for row in rows[:SEARCH_LIMIT]:
                item = json.loads(cipher().decrypt(row["encrypted_record"]))
                # Search the same redacted text shown to the user; OTP values
                # cannot be extracted by repeatedly querying candidate digits.
                public = serialize_message(row)
                if needle in " ".join((public["body"], item["sender"], item["account"])).casefold():
                    matches.append(public)
            total = len(matches)
            result = matches[(page - 1) * page_size:page * page_size]
    return {"items": result, "total": total, "page": page, "page_size": page_size, "search_limited": limited}


@app.get("/api/messages/{message_id}")
def message_detail(message_id: int, user=Depends(current_user)):
    with database() as db:
        scope,args=account_scope(user)
        row = db.execute("SELECT * FROM messages WHERE id=? AND "+scope, [message_id,*args]).fetchone()
    if row is None:
        raise HTTPException(404, "短信不存在")
    return serialize_message(row)


@app.post("/api/messages/{message_id}/reveal")
def reveal_message(message_id: int, user=Depends(current_user)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        scope,args=account_scope(user)
        row = db.execute("SELECT * FROM messages WHERE id=? AND "+scope, [message_id,*args]).fetchone()
        if row is None:
            raise HTTPException(404, "短信不存在")
        db.execute("INSERT INTO audit(user_id,action,message_id,created_at) VALUES(?,'reveal',?,?)", (user["user_id"], message_id, int(time.time())))
    return serialize_message(row, reveal=True)


class Batch(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)
    action: str


@app.post("/api/messages/batch")
def batch(data: Batch, user=Depends(current_user)):
    options = {"read": ("is_read", 1), "unread": ("is_read", 0), "archive": ("archived", 1), "restore": ("archived", 0)}
    if data.action not in options:
        raise HTTPException(422, "不支持的操作")
    column, value = options[data.action]
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        scope,args=account_scope(user)
        result = db.execute(f"UPDATE messages SET {column}=? WHERE id IN ({','.join('?' for _ in data.ids)}) AND "+scope, [value, *data.ids,*args])
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,?,?)", (user["user_id"], data.action, int(time.time())))
    return {"changed": result.rowcount}


@app.post("/api/sync")
async def manual_sync(user=Depends(current_user)):
    if user.get('admin_scope'): await synchronize()
    return {'state':'connected','last_sync':sync_state.get('last_sync')}


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


@app.post("/api/auth/password")
async def change_password(data: PasswordChange, response: Response, user=Depends(current_user)):
    with database() as db:
        saved = db.execute("SELECT password_hash FROM users WHERE id=?", (user["user_id"],)).fetchone()[0]
    if not await asyncio.to_thread(password_matches, data.current_password, saved):
        raise HTTPException(400, "当前密码不正确")
    value = await asyncio.to_thread(password_hash, data.new_password)
    with database() as db:
        db.execute("UPDATE users SET password_hash=? WHERE id=?", (value, user["user_id"]))
        db.execute("DELETE FROM sessions WHERE user_id=?", (user["user_id"],))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'password_change',?)", (user["user_id"], int(time.time())))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@app.get("/api/audit")
def audit_log(user=Depends(current_user)):
    with database() as db:
        rows = db.execute("SELECT a.id,u.username,a.action,a.message_id,a.created_at FROM audit a LEFT JOIN users u ON a.user_id=u.id"+("" if user.get("admin_scope") else " WHERE a.user_id=?")+" ORDER BY a.id DESC LIMIT 100",[] if user.get("admin_scope") else [user["user_id"]]).fetchall()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/health")
def health():
    return {"status": "ok"}


if STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/")
@app.get("/app")
@app.get("/admin")
def index():
    if not (STATIC / "index.html").is_file():
        raise HTTPException(503, "前端尚未构建")
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def collector_status():
    try:
        value = json.loads((SOURCE.parent / "receiver-status.json").read_text())
        if time.time() - value.get("heartbeat", 0) > 90:
            value.update(state="stale", label="接收器状态已过期")
        return value
    except (OSError, ValueError):
        return {"state": "unknown", "label": "尚未取得接收器状态"}


class NumberInput(BaseModel):
    mobile: str = Field(min_length=5, max_length=20)
    country_code: str = Field(default="86", max_length=5)
    label: str = Field(default="", max_length=80)
    authorized: bool = False


class NumberEdit(BaseModel):
    label: str = Field(max_length=80)
    archived: bool = False


class NumberAction(BaseModel):
    action: str
    token: str = Field(default="", max_length=8192)
    device_id: str = Field(default="", max_length=4096)
    captcha_token: str = Field(default="", max_length=32768)
    code: str = Field(default="", max_length=8)


def number_profile(row):
    return json.loads(cipher().decrypt(row["encrypted_profile"]))


@app.get("/api/numbers")
def numbers_list(user=Depends(current_user)):
    with database() as db:
        result = []
        scope,args=account_scope(user,"number_key")
        for row in db.execute("SELECT * FROM managed_numbers WHERE "+scope+" ORDER BY created_at,id",args):
            profile = number_profile(row)
            total = db.execute("SELECT COUNT(*) FROM messages WHERE account_key=?", (key_hash(profile["number"]),)).fetchone()[0]
            result.append({"id": row["id"], "number_key": row["number_key"], **profile, "total": total})
    return {"items": result, "collector": collector_status() if user.get("admin_scope") else {}}


@app.post("/api/numbers")
def number_create(data: NumberInput, user=Depends(current_user)):
    cc, mobile = data.country_code.lstrip("+"), data.mobile.strip()
    if not data.authorized or not re.fullmatch(r"[1-9]\d{0,3}", cc) or not re.fullmatch(r"\d{5,15}", mobile) or len(cc + mobile) > 15:
        raise HTTPException(400, "请填写有效号码并确认管理授权")
    number = "+" + cc + mobile
    profile = {"number": number, "mobile": mobile, "country_code": cc, "label": data.label.strip(), "archived": False, "state": "pending", "status_label": "待登录接入"}
    ident = secrets.token_hex(16)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT n.*,o.user_id AS owner_id,o.deleted FROM managed_numbers n LEFT JOIN number_owners o ON o.number_key=n.number_key WHERE n.number_key=?',(key_hash(number),)).fetchone()
        if old:
            if old['owner_id']!=user['user_id'] and not user.get('admin_scope'):raise HTTPException(409,'此号码已绑定其他账号，请联系管理员')
            if not old['deleted']:raise HTTPException(409,'此号码已经添加，请直接使用验证码登录')
            if db.execute("SELECT 1 FROM control_jobs WHERE number_id=? AND state IN ('queued','running')",(old['id'],)).fetchone():raise HTTPException(409,'号码正在停止接收，请稍后重新添加')
            job=db.execute("SELECT state FROM control_jobs WHERE number_id=? AND action='delete' ORDER BY created_at DESC LIMIT 1",(old['id'],)).fetchone()
            if job and job[0]!='done':raise HTTPException(409,'删除尚未完成，请在回收站重试停止接收')
            db.execute('UPDATE number_owners SET deleted=0 WHERE number_key=?',(old['number_key'],))
            saved=number_profile(old);saved.update(archived=False,label=data.label.strip() or saved.get('label',''))
            db.execute('UPDATE managed_numbers SET encrypted_profile=? WHERE id=?',(cipher().encrypt(json.dumps(saved).encode()),old['id']))
            db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'number_restore',?)",(user['user_id'],int(time.time())))
            return {'id':old['id'],'restored':True}
        db.execute("INSERT INTO managed_numbers VALUES(?,?,?,?)", (ident, key_hash(number), cipher().encrypt(json.dumps(profile).encode()), int(time.time())))
        db.execute("INSERT INTO number_owners(number_key,user_id) VALUES(?,?)",(key_hash(number),user['user_id']))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'number_add',?)", (user['user_id'], int(time.time())))
    return {'id':ident}



@app.patch("/api/numbers/{ident}")
def number_edit(ident: str, data: NumberEdit, user=Depends(current_user)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = require_number(db,ident,user)
        if not row:
            raise HTTPException(404, "号码不存在")
        profile = number_profile(row)
        profile.update(label=data.label.strip(), archived=data.archived)
        db.execute("UPDATE managed_numbers SET encrypted_profile=? WHERE id=?", (cipher().encrypt(json.dumps(profile).encode()), ident))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'number_edit',?)", (user["user_id"], int(time.time())))
    return {"ok": True}


@app.post("/api/numbers/{ident}/actions")
def number_action(ident: str, data: NumberAction, user=Depends(current_user)):
    if data.action not in ("activate", "status", "enable_hosting", "start", "stop", "restart", "send_code", "login", "save_device_id"):
        raise HTTPException(400, "不支持的号码操作")
    if data.action == "activate" and not data.token:
        raise HTTPException(400, "需要本号码的有效登录 token")
    if data.action == 'save_device_id':
        if not data.device_id.strip() or any(ord(c)<32 or 127<=ord(c)<=159 for c in data.device_id):
            raise HTTPException(400,'设备标识不能为空或包含控制字符')
    if data.action == "send_code" and not data.captcha_token:
        raise HTTPException(400, "请先完成官方人机验证")
    if data.action == "login" and not re.fullmatch(r"\d{4,8}", data.code):
        raise HTTPException(400, "请输入本次登录验证码")
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        require_number(db,ident,user)
        now = int(time.time())
        if db.execute("SELECT 1 FROM control_jobs WHERE number_id=? AND state IN ('queued','running')", (ident,)).fetchone():
            raise HTTPException(409, "此号码还有操作正在执行")
        if data.action in ("send_code", "login") and db.execute("SELECT 1 FROM control_jobs WHERE number_id=? AND action=? AND created_at>?", (ident, data.action, now-60)).fetchone():
            raise HTTPException(429, "请至少间隔 60 秒再操作")
        jid = secrets.token_hex(16)
        payload = cipher().encrypt(data.model_dump_json().encode())
        db.execute("INSERT INTO control_jobs VALUES(?,?,?,?,?,?,?,?)", (jid, ident, data.action, payload, "queued", None, now, now))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,?,?)", (user["user_id"], "number_" + data.action, now))
    return {"job_id": jid}


@app.get("/api/jobs/{ident}")
def number_job(ident: str, user=Depends(current_user)):
    with database() as db:
        job=db.execute("SELECT number_id FROM control_jobs WHERE id=?",(ident,)).fetchone()
        if job: require_number(db,job[0],user,deleted=True)
        row = db.execute("SELECT state,result,created_at,updated_at FROM control_jobs WHERE id=?", (ident,)).fetchone()
    if not row:
        raise HTTPException(404, "操作不存在")
    return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}


@app.get("/api/calls")
def calls_list(account: str = "", page: int = 1, user=Depends(current_user)):
    if not 1 <= page <= 100000:
        raise HTTPException(400, "页码无效")
    scope,args=account_scope(user)
    where=" WHERE "+scope
    if account: where+=" AND account_key=?";args.append(account)
    with database() as db:
        total = db.execute("SELECT COUNT(DISTINCT COALESCE(call_key,fingerprint)) FROM call_events" + where, args).fetchone()[0]
        groups = db.execute("SELECT COALESCE(call_key,fingerprint) AS group_id,MAX(id) AS latest FROM call_events" + where + " GROUP BY group_id ORDER BY latest DESC LIMIT 30 OFFSET ?",args+[(page-1)*30]).fetchall()
        items=[]
        for group in groups:
            rows=db.execute('SELECT * FROM call_events WHERE COALESCE(call_key,fingerprint)=? ORDER BY id',(group['group_id'],)).fetchall()
            events=[{'id':r['id'],'received_at':r['received_at'],**json.loads(cipher().decrypt(r['encrypted_record']))} for r in rows]
            first,last=events[0],events[-1]
            answered=any(e.get('state')==3 for e in events)
            ended=any(e.get('event') in ('ended','missed') for e in events)
            state='ended' if answered and ended else 'answered' if answered else 'missed' if any(e.get('event')=='missed' for e in events) else 'ended_unconfirmed' if ended else 'ringing'
            items.append({**last,'id':group['group_id'],'started_at':first['received_at'],
                'ended_at':last['received_at'] if ended else None,'status':state,'event_count':len(events),
                'events':[{'event':e.get('event'),'state':e.get('state'),'received_at':e['received_at']} for e in events]})
    return {"total":total,"items":items,"scope":"按通话标识合并实际采集事件；结束但未观察到接通的来电单独标注，不代表完整历史清单"}


@app.delete('/api/numbers/{ident}')
def delete_number(ident:str,user=Depends(current_user)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row=require_number(db,ident,user,deleted=True)
        if db.execute("SELECT 1 FROM control_jobs WHERE number_id=? AND state IN ('queued','running')",(ident,)).fetchone():raise HTTPException(409,'号码仍有操作执行中，请稍后删除')
        now=int(time.time());jid=secrets.token_hex(16)
        db.execute('UPDATE number_owners SET deleted=1 WHERE number_key=?',(row['number_key'],))
        db.execute("INSERT INTO control_jobs VALUES(?,?,?,?,'queued',NULL,?,?)",(jid,ident,'delete',cipher().encrypt(b'{}'),now,now))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'number_delete',?)",(user['user_id'],now))
    return {'job_id':jid,'message':'号码已移入回收站，停止接收任务已提交；历史记录保留，不修改无忧行托管开关'}


class UserCreate(BaseModel):
    username:str=Field(min_length=2,max_length=64)
    password:str=Field(min_length=6,max_length=128)


class UserUpdate(BaseModel):
    disabled:bool


class OwnerChange(BaseModel):
    user_id:int


@app.get('/api/admin/overview')
def admin_overview(user=Depends(admin_user)):
    with database() as db:
        users=[dict(r) for r in db.execute('SELECT u.id,u.username,u.role,u.disabled,u.created_at,(SELECT COUNT(*) FROM number_owners o WHERE o.user_id=u.id AND deleted=0) AS numbers FROM users u ORDER BY u.id')]
        numbers=[]
        for r in db.execute('SELECT n.*,o.user_id AS owner_id,o.deleted,u.username FROM managed_numbers n LEFT JOIN number_owners o ON o.number_key=n.number_key LEFT JOIN users u ON u.id=o.user_id ORDER BY n.created_at DESC'):
            job=db.execute("SELECT state,result FROM control_jobs WHERE number_id=? AND action='delete' ORDER BY created_at DESC LIMIT 1",(r['id'],)).fetchone()
            numbers.append({'id':r['id'],'owner_id':r['owner_id'],'username':r['username'],'deleted':bool(r['deleted']),**number_profile(r),'delete_state':job['state'] if job else None})
    return {'users':users,'numbers':numbers,'collector':collector_status()}


@app.post('/api/admin/users')
async def create_user(data:UserCreate,user=Depends(admin_user)):
    if not re.fullmatch(r'[A-Za-z0-9_.-]{2,64}',data.username):raise HTTPException(422,'用户名使用2至64位字母、数字、点、横线或下划线')
    value=await asyncio.to_thread(password_hash,data.password)
    with database() as db:
        try:db.execute("INSERT INTO users(username,password_hash,created_at,role,disabled) VALUES(?,?,?,'user',0)",(data.username,value,int(time.time())))
        except sqlite3.IntegrityError:raise HTTPException(409,'用户名已存在')
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'user_create',?)",(user['user_id'],int(time.time())))
    return {'ok':True}


@app.patch('/api/admin/users/{ident}')
def update_user(ident:int,data:UserUpdate,user=Depends(admin_user)):
    with database() as db:
        row=db.execute('SELECT role FROM users WHERE id=?',(ident,)).fetchone()
        if not row:raise HTTPException(404,'用户不存在')
        if row['role']=='admin':raise HTTPException(400,'不能停用管理员')
        db.execute('UPDATE users SET disabled=? WHERE id=?',(int(data.disabled),ident))
        db.execute('DELETE FROM sessions WHERE user_id=?',(ident,))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'user_status_change',?)",(user['user_id'],int(time.time())))
    return {'ok':True}


@app.put('/api/admin/numbers/{ident}/owner')
def assign_owner(ident:str,data:OwnerChange,user=Depends(admin_user)):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row=require_number(db,ident,user)
        if not db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0',(data.user_id,)).fetchone():raise HTTPException(400,'请选择有效用户')
        if db.execute("SELECT 1 FROM control_jobs WHERE number_id=? AND state IN ('queued','running')",(ident,)).fetchone():raise HTTPException(409,'号码还有操作正在执行')
        db.execute('UPDATE number_owners SET user_id=? WHERE number_key=?',(data.user_id,row['number_key']))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'number_assign',?)",(user['user_id'],int(time.time())))
    return {'ok':True}


@app.post('/api/admin/numbers/{ident}/restore')
def restore_number(ident:str,user=Depends(admin_user)):
    with database() as db:
        row=require_number(db,ident,user,deleted=True)
        if db.execute("SELECT 1 FROM control_jobs WHERE number_id=? AND state IN ('queued','running')",(ident,)).fetchone():raise HTTPException(409,'请等待删除任务结束')
        db.execute('UPDATE number_owners SET deleted=0 WHERE number_key=?',(row['number_key'],))
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'number_restore',?)",(user['user_id'],int(time.time())))
    return {'ok':True}


@app.get('/api/site')
def site_public():
    with database() as db:
        value=db.execute("SELECT value FROM site_settings WHERE key='registration_open'").fetchone()
    return {'registration_open':bool(value and value[0]=='true')}


class SiteSettings(BaseModel):
    registration_open:bool


@app.put('/api/admin/site')
def site_update(data:SiteSettings,user=Depends(admin_user)):
    with database() as db:
        db.execute("INSERT OR REPLACE INTO site_settings VALUES('registration_open',?)",('true' if data.registration_open else 'false',))
    return data.model_dump()


class Announcement(BaseModel):
    title:str=Field(min_length=1,max_length=120)
    body:str=Field(min_length=1,max_length=10000)
    published:bool=False
    popup:bool=True
    once_only:bool=True


@app.get('/api/admin/announcements')
def announcement_list(user=Depends(admin_user)):
    with database() as db: rows=db.execute('SELECT * FROM announcements ORDER BY updated_at DESC').fetchall()
    return {'items':[dict(r) for r in rows]}


@app.put('/api/admin/announcements/{ident}')
def announcement_save(ident:str,data:Announcement,user=Depends(admin_user)):
    if not re.fullmatch('[a-f0-9]{32}',ident):raise HTTPException(422,'公告编号无效')
    with database() as db:
        old=db.execute('SELECT revision FROM announcements WHERE id=?',(ident,)).fetchone()
        db.execute('INSERT OR REPLACE INTO announcements VALUES(?,?,?,?,?,?,?,?)',(ident,data.title.strip(),data.body.strip(),int(data.published),int(data.popup),int(data.once_only),(old[0]+1) if old else 1,int(time.time())))
    return {'ok':True}


class AnnouncementPublication(BaseModel):
    published:bool


@app.patch('/api/admin/announcements/{ident}/publication')
def announcement_publication(ident:str,data:AnnouncementPublication,user=Depends(admin_user)):
    with database() as db:
        changed=db.execute('UPDATE announcements SET published=?,updated_at=? WHERE id=?',(int(data.published),int(time.time()),ident)).rowcount
        if not changed:raise HTTPException(404,'公告不存在')
    return {'ok':True,'published':data.published}


@app.get('/api/announcements')
def user_announcements(user=Depends(current_user)):
    with database() as db:
        rows=db.execute('SELECT a.*,COALESCE(r.revision,0)>=a.revision AS is_read FROM announcements a LEFT JOIN announcement_reads r ON r.announcement_id=a.id AND r.user_id=? WHERE a.published=1 ORDER BY a.updated_at DESC',(user['user_id'],)).fetchall()
    return {'items':[dict(r) for r in rows]}


@app.post('/api/announcements/{ident}/read')
def announcement_read(ident:str,user=Depends(current_user)):
    with database() as db:
        row=db.execute('SELECT revision FROM announcements WHERE id=? AND published=1',(ident,)).fetchone()
        if not row:raise HTTPException(404,'公告已撤下')
        db.execute('INSERT OR REPLACE INTO announcement_reads VALUES(?,?,?)',(user['user_id'],ident,row[0]))
    return {'ok':True}


@app.post('/api/numbers/{ident}/credentials')
def number_credentials(ident:str,user=Depends(current_user)):
    with database() as db:
        require_number(db,ident,user)
        r=db.execute('SELECT encrypted_record,updated_at FROM number_credentials WHERE number_id=?',(ident,)).fetchone()
        db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'credentials_view',?)",(user['user_id'],int(time.time())))
    return {'values':json.loads(cipher().decrypt(r[0])) if r else {},'updated_at':r[1] if r else None}


@app.get('/login-captcha', response_class=HTMLResponse)
def login_captcha(nonce: str, user=Depends(current_user)):
    if not re.fullmatch(r'[a-f0-9]{32}',nonce):
        raise HTTPException(400,'验证窗口参数无效')
    return HTMLResponse((STATIC/'login-captcha.html').read_text().replace('__WINDOW_NONCE__',nonce))


forwarding.register(app,database,cipher,serialize_message,current_user)

wecom_commands.register(app,database,cipher,current_user)
