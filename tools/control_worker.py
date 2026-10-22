"""Private host worker for authorized account actions. Never logs credentials."""
import base64
import hashlib
import hmac
import json
import os
import platform
import secrets
import sqlite3
import ssl
import subprocess
import time
import uuid
import urllib.request
from pathlib import Path
from urllib.parse import urlencode
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

os.umask(0o077)
ROOT = Path('/opt/jego-sms-web')
PRIVATE = Path('/opt/jego-sms-control')
RESEARCH = Path('/opt/jego-research')
DB = ROOT / 'data/platform.sqlite3'
CONFIG = PRIVATE / 'protocol.json'


def crypto():
    return Fernet((ROOT / 'data/messages.key').read_bytes())


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def update_profile(ident, **changes):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT encrypted_profile FROM managed_numbers WHERE id=?', (ident,)).fetchone()
        value = json.loads(crypto().decrypt(row[0]))
        value.update(changes)
        db.execute('UPDATE managed_numbers SET encrypted_profile=? WHERE id=?', (crypto().encrypt(json.dumps(value).encode()), ident))


def session_path(ident):
    if len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
        raise ValueError('Invalid account identifier')
    path = PRIVATE / 'accounts' / ident
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def load_session(ident):
    return json.loads(crypto().decrypt((session_path(ident) / 'session.enc').read_bytes()))


def save_session(ident, value):
    path = session_path(ident) / 'session.enc'
    temp = path.with_suffix('.tmp')
    temp.write_bytes(crypto().encrypt(json.dumps(value).encode()))
    temp.replace(path)
    publish_credentials(ident,value)


def publish_credentials(ident,value):
    fields={k:value[k] for k in ('token','device_id','hosting_device_id','phone_token','protocol_profile') if value.get(k)}
    if value.get('enrollment',{}).get('enrollId'):fields['enrollId']=value['enrollment']['enrollId']
    with connect() as db:
        old=db.execute('SELECT encrypted_record FROM number_credentials WHERE number_id=?',(ident,)).fetchone()
        if old and json.loads(crypto().decrypt(old[0]))==fields:return
        db.execute('INSERT OR REPLACE INTO number_credentials VALUES(?,?,?)',(ident,crypto().encrypt(json.dumps(fields).encode()),int(time.time())))


class BusinessError(Exception):
    def __init__(self, code, message=''):
        self.code = str(code)
        self.message = str(message)[:500] if isinstance(message,str) else ''


IOS_898 = 'ios-8.9.8'
# Static evidence: 8.9.8 build 2026091501; transport flags from original methods.
IOS_898_PLAIN_PATHS = {'/api/service/voip/v1/setVoip','/api/service/voip/v1/getCallStatus','/api/service/voip/v1/securityVerify'}


def post(path, body, token=None, encrypted=True, client_profile='legacy'):
    if client_profile == IOS_898 and path in IOS_898_PLAIN_PATHS:
        encrypted = False
    config = json.loads(CONFIG.read_text())
    stamp = int(time.time()*1000)
    salt = bytes((b+i)&255 for i,b in enumerate(b'jt97&^%!'))
    sign = hashlib.sha1(salt + stamp.to_bytes(8, 'big')).hexdigest()
    query = {'token' if token else 'n_token': token or config['n_token'], 'timestamp': stamp, 'sign': sign, 'lang': 'zh_CN'}
    payload = json.dumps(body, ensure_ascii=False).encode()
    if encrypted:
        nonce = str(stamp) + str(100+secrets.randbelow(100))
        key = hashlib.md5((config['SECRET_VAL_ONLINE']+nonce).encode()).hexdigest()[8:24].encode()
        pad = padding.PKCS7(128).padder()
        clear = pad.update(payload) + pad.finalize()
        enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        payload = json.dumps({'sec': base64.b64encode((config['SECRET_KEY_ONLINE']+';'+nonce+';01').encode()).decode(), 'body': base64.b64encode(enc.update(clear)+enc.finalize()).decode()}).encode()
    ctx = ssl.create_default_context(cafile=str(PRIVATE / 'trusted.pem'))
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
    request = urllib.request.Request('https://app3.jegotrip.com.cn'+path+'?'+urlencode(query), data=payload, headers={'Content-Type':'application/json',**({'User-Agent':'Roam/2026091501'} if client_profile==IOS_898 else {})}, method='POST')
    with opener.open(request, timeout=25) as response:
        obj = json.loads(response.read())
    if obj.get('sec') and isinstance(obj.get('body'), str) and obj['body']:
        nonce = base64.b64decode(obj['sec']).decode().split(';')[1]
        key = hashlib.md5((config['SECRET_VAL_ONLINE']+nonce).encode()).hexdigest()[8:24].encode()
        dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        clear = dec.update(base64.b64decode(obj['body'])) + dec.finalize()
        unpad = padding.PKCS7(128).unpadder()
        text = (unpad.update(clear)+unpad.finalize()).decode()
        obj['body'] = json.loads(text) if text.startswith(('{','[')) else text
    if str(obj.get('code')) != '0':
        raise BusinessError(obj.get('code', 'unknown'), obj.get('msg') or obj.get('message') or obj.get('errorMsg') or '')
    return obj.get('body')


def service(ident, action):
    session = load_session(ident)
    unit = session.get('unit', 'jego-number@'+ident+'.service')
    allowed = unit == 'jego-sms-receiver.service' or unit == 'jego-number@'+ident+'.service'
    if not allowed:
        raise ValueError('Invalid service')
    result = subprocess.run(['systemctl', action, unit], capture_output=True, timeout=35)
    if result.returncode:
        raise RuntimeError('Service operation failed')


def prepare(ident, profile, session):
    user = post('/api/service/user/v1/getUser', {}, session['token'], client_profile=session.get('protocol_profile','legacy'))
    if str(user.get('mobile')) != profile['mobile'] or str(user.get('country_code','')).lstrip('+') != profile['country_code']:
        raise ValueError('Login session belongs to another number')
    voip = post('/api/service/voip/v1/openVoipStatus', {}, session['token'], False, client_profile=session.get('protocol_profile','legacy'))
    phone = post('/api/service/voip/v1/csb/token', {'accountId': profile['number']}, session['token'], False, client_profile=session.get('protocol_profile','legacy'))
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:
        TripleDES = algorithms.TripleDES
    dec = Cipher(TripleDES(b'AE6Jy6OrfB7ZkZg6WD/rQIZf'), modes.ECB()).decryptor()
    clear = dec.update(base64.b64decode(voip['sip_password'])) + dec.finalize()
    unpad = padding.PKCS7(64).unpadder()
    password = (unpad.update(clear)+unpad.finalize()).decode()
    config = json.loads(CONFIG.read_text())
    lines = [profile['number'], phone['token'], config['APP_KEY_ONLINE'], config['SERVER_ADDRESS_ONLINE_TLS'], config['TLS_CERTIFICATE'], password]
    if any('\n' in value or '\r' in value or '\x00' in value for value in lines):
        raise ValueError('Invalid credential format')
    path = session_path(ident)
    for folder in ('info','log'):
        (path/folder).mkdir(mode=0o700,exist_ok=True)
    contents = '\n'.join(lines)+'\n'
    target = path / 'sdk-login-input.txt'
    temp = target.with_suffix('.tmp');temp.write_text(contents);temp.replace(target)
    if session.get('unit') == 'jego-sms-receiver.service':
        target = RESEARCH / 'sdk-login-input.txt'
        temp = target.with_suffix('.tmp');temp.write_text(contents);temp.replace(target)
    session['phone_token'] = phone['token']
    session['prepared'] = int(time.time())
    save_session(ident, session)


def execute(ident, action, data, profile):
    if action == 'delete':
        session=load_session(ident) if (session_path(ident)/'session.enc').exists() else {}
        unit=session.get('unit','jego-number@'+ident+'.service')
        if unit not in ('jego-sms-receiver.service','jego-number@'+ident+'.service'):raise ValueError('Invalid service')
        result=subprocess.run(['systemctl','disable','--now',unit],capture_output=True,timeout=35)
        active=subprocess.run(['systemctl','is-active',unit],capture_output=True,timeout=5).returncode==0
        if active:raise RuntimeError('Receiver did not stop')
        update_profile(ident,state='stopped',status_label='号码已删除，接收已停止')
        return {'message':'号码已移入回收站，接收已停止；历史记录及加密会话保留，可由管理员恢复。不修改无忧行托管开关。'}
    if action == 'save_device_id':
        device=data['device_id'].strip()
        if not device or len(device)>4096 or any(ord(c)<32 or 127<=ord(c)<=159 for c in data['device_id']):
            raise ValueError('Invalid device identifier text')
        session=load_session(ident) if (session_path(ident)/'session.enc').exists() else {}
        session['hosting_device_id']=device
        save_session(ident,session)
        update_profile(ident,hosting_device_hint=device[:8]+'…'+device[-4:],hosting_device_updated_at=int(time.time()),hosting=None,hosting_checked_at=None)
        return {'message':'托管设备标识已保存；未重新登录、未更改托管开关、未重连接收。可单独查询或开启托管。'}
    if action == 'send_code':
        config = json.loads(CONFIG.read_text())
        session = load_session(ident) if (session_path(ident)/'session.enc').exists() else {}
        # The original 8.9.8 App uses the same persisted UUID for enrollment and hosting.
        device = session.get('hosting_device_id') or session.get('device_id') or str(uuid.uuid4()).upper()
        enrollment = post('/api/device/sec/v1/enroll', {'deviceId':device}, client_profile=IOS_898)
        body = {'country_code':profile['country_code'], 'mobile':profile['mobile'], 'type':'3', 'timeStamp':str(int(time.time()*1000)), 'imgCodeToken':data['captcha_token']}
        signed = {**body, 'sign_key':str(config['sign_key'])+'&'}
        text = '&'.join(k+'='+str(signed[k]) for k in sorted(signed, key=str.casefold))
        body['sign'] = hashlib.sha1(text.encode()).hexdigest().upper()
        body.update(third_type='', device_id='', sceneId='vq25q9sr', checkImgType='0')
        post('/api/user/v1/getCodeByImgCode', body, client_profile=IOS_898)
        session = load_session(ident) if (session_path(ident)/'session.enc').exists() else {}
        if session.get('device_id'):
            session.setdefault('hosting_device_id',session['device_id'])
        session.update(pending_device_id=device, pending_protocol_profile=IOS_898, enrollment=enrollment, sent_at=int(time.time()))
        save_session(ident, session)
        update_profile(ident, state='awaiting_code', status_label='验证码已发送，等待登录')
        return {'message':'验证码已发送', 'code':'0'}
    if action == 'login':
        session = load_session(ident)
        if time.time()-session.get('sent_at',0) > 300:
            raise ValueError('Verification code expired')
        result = post('/api/userCenter/v3/signInByCode', {'site':1,'verifyCode':data['code'],'countryCode':profile['country_code'],'mobile':profile['mobile'],'sendType':1,'countryName':'','enrollId':session['enrollment']['enrollId'],**({'activeList':session.get('active_list',[])} if session.get('pending_protocol_profile')==IOS_898 else {})}, client_profile=session.get('pending_protocol_profile','legacy'))
        if session.get('device_id'):
            session.setdefault('hosting_device_id',session['device_id'])
        session['token'] = result['token']
        session['device_id'] = session.pop('pending_device_id')
        session['protocol_profile'] = session.pop('pending_protocol_profile','legacy')
        save_session(ident, session)
        prepare(ident, profile, session)
        update_profile(ident, state='ready', status_label='已登录，尚未启动接收')
        return {'message':'登录成功，可查询托管状态并启动接收', 'code':'0'}
    if action == 'activate':
        previous = load_session(ident) if (session_path(ident)/'session.enc').exists() else {}
        session = {**previous,'token':data['token'], 'device_id':previous.get('device_id') or str(uuid.uuid4())}
        if previous.get('unit') == 'jego-sms-receiver.service':
            session['unit'] = previous['unit']
        # Verify ownership before persisting the supplied session or starting SDK.
        user = post('/api/service/user/v1/getUser', {}, session['token'], client_profile=session.get('protocol_profile','legacy'))
        if str(user.get('mobile')) != profile['mobile'] or str(user.get('country_code','')).lstrip('+') != profile['country_code']:
            raise ValueError('Login session belongs to another number')
        save_session(ident, session)
        prepare(ident, profile, session)
        update_profile(ident, state='ready', status_label='会话验证成功，尚未启动接收')
        return {'message':'会话已接入，可启动接收', 'code':'0'}
    if action in ('start','stop','restart'):
        if action != 'stop' and not (session_path(ident) / 'sdk-login-input.txt').is_file() and load_session(ident).get('unit') != 'jego-sms-receiver.service':
            raise ValueError('Account login is not prepared')
        if action != 'stop':
            service(ident, 'enable')
        service(ident, action)
        update_profile(ident, state='starting' if action != 'stop' else 'stopped', status_label='已启动，等待连接状态' if action != 'stop' else '已停止接收（不修改托管开关）')
        return {'message':'操作已完成；启动不等于登录或收信成功'}
    session = load_session(ident)
    device=session.get('hosting_device_id') or session.get('device_id')
    if not session.get('token') or not device:
        raise ValueError('Login session or hosting device is missing')
    if action == 'enable_hosting':
        if session.get('protocol_profile') == IOS_898:
            # iOS-compatible schema; actual host metadata remains explicitly Linux.
            metadata = {'app_version':'V8.9.8','app_version_code':'898','os_type':'1',
                'os_version':platform.system()+' '+platform.release(),'device_id':device,
                'device_type':'Independent Linux protocol client','macAddress':device,
                'terminalBrand':'Independent client','terminalModel':platform.machine(),
                'resolution':'','idfa':'','userId':'','device_token':''}
            post('/api/device/v1/sync',metadata,session['token'],False,client_profile=IOS_898)
            update_profile(ident,version_sync={'state':'accepted','at':int(time.time()),'profile':IOS_898})
        post('/api/service/voip/v1/setVoip', {'device_id':device,'cs_forward':'2'}, session['token'], client_profile=session.get('protocol_profile','legacy'))
    result = post('/api/service/voip/v1/getCallStatus', {'device_id':device}, session['token'], False, client_profile=session.get('protocol_profile','legacy'))
    state = {k: result.get(k) for k in ('cs_forward','localCsForward','authentication_code')}
    update_profile(ident, hosting=state, hosting_checked_at=int(time.time()))
    return {'message':'托管已开启' if str(state['cs_forward'])=='2' else '托管未开启', 'hosting':state, 'code':'0'}


def import_calls():
    source_db = RESEARCH / 'research-sms.sqlite3'
    source = sqlite3.connect(source_db, timeout=3)
    try:
        if not source.execute("SELECT 1 FROM sqlite_master WHERE name='received_calls'").fetchone():
            return
        source_crypto = Fernet((RESEARCH / 'sms-storage.key').read_bytes())
        cursor_path = PRIVATE/'call-cursor.txt'
        cursor = int(cursor_path.read_text()) if cursor_path.exists() else 0
        latest = cursor
        with connect() as db:
            # Source fingerprints are stable; ignore existing events without decrypting.
            for rid, fingerprint, blob, at in source.execute('SELECT id,fingerprint,encrypted_record,ingested_at FROM received_calls WHERE id>? ORDER BY id LIMIT 500',(cursor,)):
                latest = rid
                if db.execute('SELECT 1 FROM call_events WHERE fingerprint=?',(fingerprint,)).fetchone():
                    continue
                item = json.loads(source_crypto.decrypt(blob))
                key = hmac.new((ROOT/'data/messages.key').read_bytes(),item['account'].encode(),hashlib.sha256).hexdigest()
                group = hmac.new((ROOT/'data/messages.key').read_bytes(),json.dumps([item.get('account'),item['call_id']],ensure_ascii=False).encode(),hashlib.sha256).hexdigest() if item.get('call_id') else fingerprint
                inserted=db.execute('INSERT OR IGNORE INTO call_events(fingerprint,account_key,encrypted_record,received_at,call_key) VALUES(?,?,?,?,?)',(fingerprint,key,crypto().encrypt(json.dumps(item).encode()),at.replace(' ','T')+'Z',group))
                if inserted.rowcount and item.get('event') in ('incoming','missed','ended'):
                    event_id=inserted.lastrowid
                    now=int(time.time())
                    # Queue the first observed event once per channel/call, in
                    # the same transaction as the encrypted call event import.
                    for channel in db.execute('SELECT id,rules FROM forward_channels WHERE enabled=1').fetchall():
                        rules=json.loads(channel['rules'])
                        if 'call' not in rules.get('events',[]) or event_id<=rules.get('after_call_id',0):
                            continue
                        seen=db.execute('INSERT OR IGNORE INTO forward_call_seen(channel_id,call_key) VALUES(?,?)',(channel['id'],group))
                        if seen.rowcount:
                            db.execute("INSERT OR IGNORE INTO forward_jobs(id,channel_id,message_id,state,next_at,created_at,updated_at,source_kind) VALUES(?,?,?,'pending',?,?,?,'call')",(uuid.uuid4().hex,channel['id'],-event_id,now,now,now))
        temp=cursor_path.with_suffix('.tmp');temp.write_text(str(latest));temp.replace(cursor_path)
    finally:
        source.close()


def poll():
    with connect() as db:
        row = db.execute("SELECT j.*,n.encrypted_profile FROM control_jobs j JOIN managed_numbers n ON n.id=j.number_id WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return
        db.execute("UPDATE control_jobs SET state='running',updated_at=? WHERE id=?", (int(time.time()),row['id']))
    try:
        if time.time()-row['created_at'] > 300:
            raise ValueError('Queued operation expired; not executed')
        payload = json.loads(crypto().decrypt(row['encrypted_payload']))
        profile = json.loads(crypto().decrypt(row['encrypted_profile']))
        result = execute(row['number_id'],row['action'],payload,profile)
        state = 'done'
    except BusinessError as exc:
        state, result = 'failed', {'message':('无忧行返回：'+exc.message) if exc.message else '无忧行拒绝本次操作，未返回错误说明', 'code':exc.code}
    except Exception as exc:
        state, result = 'failed', {'message':'操作未完成，请检查会话、号码匹配和连接条件', 'error_type':type(exc).__name__}
    with connect() as db:
        db.execute('UPDATE control_jobs SET state=?,result=?,encrypted_payload=NULL,updated_at=? WHERE id=?', (state,json.dumps(result),int(time.time()),row['id']))


def receiver_statuses():
    with connect() as db:
        rows = db.execute('SELECT id,encrypted_profile FROM managed_numbers').fetchall()
    default_status = {'state':'unknown','label':'尚未取得接收器状态','heartbeat':int(time.time())}
    for row in rows:
        try:
            session = load_session(row['id'])
            publish_credentials(row['id'],session)
            legacy = session.get('unit') == 'jego-sms-receiver.service'
            path = RESEARCH/'receiver-health.json' if legacy else session_path(row['id'])/'receiver-health.json'
            try:
                value = json.loads(path.read_text())
            except FileNotFoundError:
                value = {'heartbeat':0,'client_status':None,'calls_initialized':False}
            unit = 'jego-sms-receiver.service' if legacy else 'jego-number@'+row['id']+'.service'
            active = subprocess.run(['systemctl','is-active',unit],capture_output=True,timeout=5).returncode==0
            live = active and time.time()-value.get('heartbeat',0)<90
            online = live and value.get('client_status') in (3,5)
            value.update(state='online' if online else ('connecting' if active else 'offline'), label='SDK 保活正常（不代表运营商投递保证）' if online else ('接收进程运行，等待 SDK 连接' if active else '接收进程未运行'))
            update_profile(row['id'],receiver=value)
            if legacy:
                default_status=value
        except (OSError,ValueError,KeyError):
            continue
    path=Path('/opt/jego-sms-source/receiver-status.json')
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(default_status));temp.replace(path)


def main():
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    with connect() as db:
        db.execute("UPDATE control_jobs SET state='failed',encrypted_payload=NULL,result=?,updated_at=? WHERE state='running'", (json.dumps({'message':'服务重启，未重复执行；请核对状态后重试'}),int(time.time())))
    last_status = 0
    while True:
        try:
            poll()
            import_calls()
            if time.time()-last_status > 15:
                receiver_statuses()
                last_status=time.time()
        except Exception as exc:
            print('control_worker_error='+type(exc).__name__, flush=True)
        time.sleep(2)


if __name__ == '__main__':
    main()
