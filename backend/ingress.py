"""Source-scoped HTTP ingestion and explicitly requested synthetic demo data."""
import hashlib,json,secrets,time
from datetime import datetime,timezone
from typing import Literal
from fastapi import Depends,HTTPException,Request
from pydantic import BaseModel,Field,ConfigDict
import forwarding

class TokenInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
class Event(BaseModel):
    model_config=ConfigDict(extra='forbid')
    event_id:str=Field(min_length=1,max_length=128)
    kind:Literal['message','call']='message'
    sender:str=Field(min_length=1,max_length=128)
    body:str=Field(default='',max_length=16000)
    received_at:str|None=Field(default=None,max_length=64)
    call_id:str|None=Field(default=None,max_length=128)
    event:Literal['incoming','answered','ended','missed']='incoming'

def register(app,database,cipher,key_hash,current_user,require_number,utc_text):
    def init(db):
        db.executescript('''CREATE TABLE IF NOT EXISTS ingress_tokens(id TEXT PRIMARY KEY,number_id TEXT NOT NULL REFERENCES managed_numbers(id),user_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,enabled INTEGER NOT NULL,created_at INTEGER NOT NULL,last_used INTEGER);
        CREATE TABLE IF NOT EXISTS ingress_receipts(token_id TEXT NOT NULL,event_id TEXT NOT NULL,kind TEXT NOT NULL,record_id INTEGER NOT NULL,digest TEXT NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(token_id,event_id));
        CREATE INDEX IF NOT EXISTS ingress_receipt_time ON ingress_receipts(token_id,created_at);''')
    app.state.init_ingress=init
    def audit(db,uid,action):db.execute('INSERT INTO audit(user_id,action,created_at) VALUES(?,?,?)',(uid,action,int(time.time())))
    @app.get('/api/numbers/{ident}/ingress-tokens')
    def list_tokens(ident:str,user=Depends(current_user)):
        with database() as db:
            require_number(db,ident,user)
            rows=db.execute('SELECT id,name,enabled,created_at,last_used FROM ingress_tokens WHERE number_id=? ORDER BY created_at DESC',(ident,)).fetchall()
        return {'items':[dict(r) for r in rows]}
    @app.post('/api/numbers/{ident}/ingress-tokens')
    def create_token(ident:str,data:TokenInput,user=Depends(current_user)):
        if not data.name.strip():raise HTTPException(422,'密钥名称不能为空')
        value='xin_'+secrets.token_urlsafe(32);tid=secrets.token_hex(16)
        with database() as db:
            db.execute('BEGIN IMMEDIATE');row=require_number(db,ident,user)
            if db.execute('SELECT COUNT(*) FROM ingress_tokens WHERE number_id=? AND enabled=1',(ident,)).fetchone()[0]>=10:raise HTTPException(409,'每个来源最多10个有效密钥，请先撤销旧密钥')
            db.execute('INSERT INTO ingress_tokens VALUES(?,?,?,?,?,1,?,NULL)',(tid,ident,row['owner_id'],data.name.strip(),hashlib.sha256(value.encode()).hexdigest(),int(time.time())))
            audit(db,user['user_id'],'ingress_token_create')
        return {'id':tid,'token':value,'endpoint':'/api/ingest/events','notice':'密钥只显示一次；数据库只保存哈希，请妥善保存。'}
    @app.delete('/api/numbers/{ident}/ingress-tokens/{tid}')
    def revoke(ident:str,tid:str,user=Depends(current_user)):
        with database() as db:
            db.execute('BEGIN IMMEDIATE');require_number(db,ident,user)
            db.execute('UPDATE ingress_tokens SET enabled=0 WHERE id=? AND number_id=?',(tid,ident));audit(db,user['user_id'],'ingress_token_revoke')
        return {'ok':True}
    @app.post('/api/ingest/events')
    def ingest(data:Event,request:Request):
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer ') or len(auth)>256:raise HTTPException(401,'需要有效的来源接入密钥')
        digest=hashlib.sha256(data.model_dump_json().encode()).hexdigest();now=int(time.time());nowtext=datetime.now(timezone.utc).isoformat()
        received=utc_text(data.received_at) if data.received_at else nowtext
        if not received:raise HTTPException(422,'received_at须为有效ISO8601时间或时间戳')
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            token=db.execute('SELECT t.*,n.number_key,n.encrypted_profile FROM ingress_tokens t JOIN managed_numbers n ON n.id=t.number_id JOIN number_owners o ON o.number_key=n.number_key JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.enabled=1 AND o.deleted=0 AND o.user_id=t.user_id AND u.disabled=0',(hashlib.sha256(auth[7:].encode()).hexdigest(),)).fetchone()
            if not token:raise HTTPException(401,'接入密钥无效、已撤销或来源不可用')
            previous=db.execute('SELECT * FROM ingress_receipts WHERE token_id=? AND event_id=?',(token['id'],data.event_id)).fetchone()
            if previous:
                if previous['digest']!=digest:raise HTTPException(409,'同一个event_id已提交不同内容，请使用新event_id')
                return {'id':previous['record_id'],'kind':previous['kind'],'duplicate':True}
            if db.execute('SELECT COUNT(*) FROM ingress_receipts WHERE token_id=? AND created_at>?',(token['id'],now-60)).fetchone()[0]>=120:raise HTTPException(429,'每个接入密钥每分钟最多120条新事件')
            profile=json.loads(cipher().decrypt(token['encrypted_profile']));account=profile['number']
            if data.kind=='message':
                if not data.body.strip():raise HTTPException(422,'消息正文不能为空')
                item={'account':account,'sender':data.sender,'body':data.body,'time':received,'source_time_parsed':True,'source_name':profile.get('label') or account,'offline':False}
                source='http:'+token['id'];seq=db.execute('SELECT COALESCE(MAX(source_id),0)+1 FROM messages WHERE source=?',(source,)).fetchone()[0]
                result=db.execute('INSERT INTO messages(source,source_id,account_key,encrypted_record,received_at,ingested_at) VALUES(?,?,?,?,?,?)',(source,seq,token['number_key'],cipher().encrypt(json.dumps(item,ensure_ascii=False).encode()),received,nowtext))
                rid=result.lastrowid;forwarding.enqueue(db,rid)
            else:
                group=key_hash(json.dumps([account,token['id'],data.call_id or data.event_id]))
                item={'account':account,'sender':data.sender,'event':data.event,'state':3 if data.event=='answered' else None,'call_id':data.call_id or data.event_id,'source_name':profile.get('label') or account}
                result=db.execute('INSERT INTO call_events(fingerprint,account_key,encrypted_record,received_at,call_key) VALUES(?,?,?,?,?)',(key_hash(token['id']+':'+data.event_id),token['number_key'],cipher().encrypt(json.dumps(item).encode()),received,group));rid=result.lastrowid
                if data.event in ('incoming','missed','ended'):
                    for c in db.execute("SELECT c.id,c.rules FROM forward_channels c JOIN channel_owners o ON o.channel_id=c.id WHERE c.enabled=1 AND o.user_id=?",(token['user_id'],)).fetchall():
                        rules=json.loads(c['rules'])
                        if 'call' not in rules.get('events',[]) or rid<=rules.get('after_call_id',0):continue
                        seen=db.execute('INSERT OR IGNORE INTO forward_call_seen VALUES(?,?)',(c['id'],group))
                        if seen.rowcount:db.execute("INSERT OR IGNORE INTO forward_jobs(id,channel_id,message_id,state,next_at,created_at,updated_at,source_kind) VALUES(?,?,?,'pending',?,?,?,'call')",(secrets.token_hex(16),c['id'],-rid,now,now,now))
            db.execute('INSERT INTO ingress_receipts VALUES(?,?,?,?,?,?)',(token['id'],data.event_id,data.kind,rid,digest,now))
            db.execute('UPDATE ingress_tokens SET last_used=? WHERE id=?',(now,token['id']))
            profile.update(last_event_at=now,status_label='已接入HTTP事件')
            db.execute('UPDATE managed_numbers SET encrypted_profile=? WHERE id=?',(cipher().encrypt(json.dumps(profile).encode()),token['number_id']))
        return {'id':rid,'kind':data.kind,'duplicate':False}
    @app.post('/api/demo')
    def demo(user=Depends(current_user)):
        now=int(time.time());at=datetime.now(timezone.utc).isoformat();address='demo:'+str(user['user_id']);nk=key_hash(address)
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM managed_numbers WHERE number_key=?',(nk,)).fetchone():raise HTTPException(409,'该账号已创建演示来源，可在来源管理中恢复查看')
            ident=secrets.token_hex(16);profile={'number':address,'label':'演示来源（合成数据）','archived':False,'state':'api','status_label':'演示数据，不连接外部服务','demo':True,'last_event_at':now}
            db.execute('INSERT INTO managed_numbers VALUES(?,?,?,?)',(ident,nk,cipher().encrypt(json.dumps(profile).encode()),now));db.execute('INSERT INTO number_owners VALUES(?,?,0)',(nk,user['user_id']))
            for i,body in enumerate(['[演示] 欢迎使用信匣，所有示例均为合成数据。','[演示] 您的验证码为000000，用于展示敏感正文隐藏功能。','[演示] HTTP接入支持event_id去重，来源密钥可撤销。'],1):
                item={'account':address,'sender':'Demo Service','body':body,'time':at,'source_time_parsed':True,'source_name':'演示数据','offline':False,'demo':True}
                db.execute('INSERT INTO messages(source,source_id,account_key,encrypted_record,received_at,ingested_at) VALUES(?,?,?,?,?,?)',('demo:'+str(user['user_id']),i,nk,cipher().encrypt(json.dumps(item,ensure_ascii=False).encode()),at,at))
            item={'account':address,'sender':'Demo Caller','event':'missed','state':None,'source_name':'演示数据','demo':True}
            db.execute('INSERT INTO call_events(fingerprint,account_key,encrypted_record,received_at,call_key) VALUES(?,?,?,?,?)',(key_hash(address+':call'),nk,cipher().encrypt(json.dumps(item).encode()),at,key_hash(address+':call')))
            audit(db,user['user_id'],'demo_created')
        return {'ok':True,'message':'已创建3条演示消息和1通演示来电，不会触发外部转发。'}
