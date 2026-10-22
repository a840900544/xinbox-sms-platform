"""Per-channel authenticated WeCom read-only commands."""
import base64,hashlib,hmac,json,re,secrets,struct,time
import xml.etree.ElementTree as ET
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from datetime import datetime,timezone,timedelta
from pathlib import Path
from fastapi import HTTPException,Request,Response


HELP='信匣查询指令\n帮助\n来源列表\n状态 来源标识\n消息 来源标识（最近5条）\n来电 来源标识（最近5通）\n仅查询网站账号归属来源；时间为北京时间，敏感正文遵循渠道设置。\n在对应企业微信应用渠道中开启指令查询并指定成员。查询范围和敏感正文权限继承该渠道。'

def config():
    return None # Standalone edition uses per-channel encrypted callbacks.

def stamp(value):
    try:
        d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return d.replace(tzinfo=d.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:return '时间未知'

def register(app,database,cipher,current_user):
    def init(db):
        db.execute('CREATE TABLE IF NOT EXISTS wecom_command_requests(request_id TEXT PRIMARY KEY,identity TEXT NOT NULL,encrypted_reply BLOB NOT NULL,created_at INTEGER NOT NULL)')
    app.state.init_wecom_commands=init
    def audit(db,uid,action):db.execute('INSERT INTO audit(user_id,action,created_at) VALUES(?,?,?)',(uid,action,int(time.time())))
    def resolve(db,member,corp,agent,channel_id=None):
        matched=[]
        for row in db.execute("SELECT c.*,o.user_id,u.disabled FROM forward_channels c JOIN channel_owners o ON o.channel_id=c.id JOIN users u ON u.id=o.user_id WHERE c.provider='wecom_app' AND c.enabled=1 AND u.disabled=0"):
            rules=json.loads(row['rules'])
            if channel_id and row['id']!=channel_id:continue
            if not rules.get('commands_enabled') or member not in rules.get('command_members',[]):continue
            conf=json.loads(cipher().decrypt(row['encrypted_config']))
            if conf.get('corp_id')==corp and str(conf.get('agent_id'))==str(agent):matched.append((row,rules,conf))
        return matched
    def command(db,member,content,channel,rules):
        uid=channel['user_id']
        if content in ('帮助','短信帮助','信匣帮助','/sms','/sms help'):return HELP
        rows=db.execute('SELECT n.* FROM managed_numbers n JOIN number_owners o ON o.number_key=n.number_key WHERE o.user_id=? AND o.deleted=0 ORDER BY n.created_at',(uid,)).fetchall()
        if rules.get('accounts'):rows=[r for r in rows if r['number_key'] in rules['accounts']]
        entries=[(r,json.loads(cipher().decrypt(r['encrypted_profile']))) for r in rows]
        if content in ('来源列表','号码列表','我的号码','短信号码'):
            audit(db,uid,'wecom_numbers')
            text='你的来源（HTTP接入）：\n'+'\n'.join(f"{p['number']} · {p.get('label') or '无备注'} · "+('已提交事件' if p.get('last_event_at') else '等待提交') for _,p in entries[:15]) if entries else '你的账号暂无来源。'
            return text+ ('\n更多号码请在网站查看。' if len(entries)>15 else '')
        match=re.fullmatch(r'(状态|消息|短信|来电)(?:\s+(.{1,128}))?',content)
        if not match:return HELP
        kind,number=match.groups();kind='短信' if kind=='消息' else kind;found=[(r,p) for r,p in entries if not number or p['number'].endswith(number.lstrip('+'))]
        if not found:return '未找到归属于你的号码，请发送「号码列表」。'
        if len(found)!=1:return '匹配到多个号码，请使用完整号码或更长尾号。'
        r,p=found[0];audit(db,uid,{'状态':'wecom_status','短信':'wecom_sms','来电':'wecom_calls'}[kind])
        if kind=='状态':
            return f"{p['number']} · {p.get('label') or '无备注'}\n来源状态："+p.get('status_label','HTTP 接入就绪')+'\n最近事件时间：'+(datetime.fromtimestamp(p.get('last_event_at',0),timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if p.get('last_event_at') else '尚未提交')+'\n此版本仅接收HTTP提交的数据，不连接运营商。'
        if kind=='短信' and 'sms' not in rules.get('events',['sms']):return '此渠道未开放短信查询。'
        if kind=='来电' and 'call' not in rules.get('events',[]):return '此渠道未开放来电查询。'
        if kind=='短信':
            data=db.execute('SELECT * FROM messages WHERE account_key=? ORDER BY received_at DESC,id DESC LIMIT 5',(r['number_key'],)).fetchall();parts=[]
            for m in data:
                item=json.loads(cipher().decrypt(m['encrypted_record']))
                # Same conservative sensitive-content classification as the web view.
                from app import SENSITIVE
                body='[验证码等敏感正文已隐藏，请在网站查看]' if not rules.get('allow_sensitive') and SENSITIVE.search(item['body']) else item['body'][:180]
                parts.append(stamp(m['received_at'])+'\n发送方：'+item['sender']+'\n'+body)
            return p['number']+' 最近短信（北京时间）\n'+ ('\n\n'.join(parts) if parts else '暂无记录')
        data=db.execute('SELECT MAX(id) AS latest FROM call_events WHERE account_key=? GROUP BY COALESCE(call_key,fingerprint) ORDER BY latest DESC LIMIT 5',(r['number_key'],)).fetchall();parts=[]
        for g in data:
            event=db.execute('SELECT * FROM call_events WHERE id=?',(g['latest'],)).fetchone();item=json.loads(cipher().decrypt(event['encrypted_record']))
            label={'incoming':'来电','missed':'未接来电','ended':'来电结束（不代表已接听）'}.get(item.get('event'),'来电事件')
            parts.append(stamp(event['received_at'])+' · '+str(item.get('sender') or '未知主叫')+' · '+label)
        return p['number']+' 最近来电（北京时间）\n'+('\n'.join(parts) if parts else '暂无记录')
    def run(member,content,corp,agent,message_id,channel_id=None):
        identity=hashlib.sha256(json.dumps([corp,str(agent),member,channel_id]).encode()).hexdigest()
        reqid=hashlib.sha256((identity+':'+message_id).encode()).hexdigest();now=int(time.time())
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            matched=resolve(db,member,corp,agent,channel_id)
            if not matched:return {'reply':'此应用尚未向你开放查询。请在对应企业微信应用转发渠道中启用指令，并将你的成员UserID加入允许查询成员。'}
            if len(matched)!=1:return {'reply':'同一成员匹配到多个渠道，请管理员调整渠道指令成员范围。'}
            channel,rules,_=matched[0]
            # Configuration and ownership are part of the cache key: revocation takes effect immediately.
            reqid=hashlib.sha256((reqid+channel['rules']+str(channel['user_id'])+str(db.execute('SELECT GROUP_CONCAT(number_key) FROM number_owners WHERE user_id=? AND deleted=0',(channel['user_id'],)).fetchone()[0])).encode()).hexdigest()
            prior=db.execute('SELECT encrypted_reply FROM wecom_command_requests WHERE request_id=?',(reqid,)).fetchone()
            if prior:return {'reply':cipher().decrypt(prior[0]).decode()}
            count=db.execute('SELECT COUNT(*) FROM wecom_command_requests WHERE identity=? AND created_at>?',(identity,now-60)).fetchone()[0]
            reply='查询过于频繁，请一分钟后再试。' if count>=20 else command(db,member,content,channel,rules)
            if rules.get('mask_numbers'):reply=re.sub(r'(?<![0-9])((?:\+86)?[0-9]{3})[0-9]{4}([0-9]{4})(?![0-9])',r'\1****\2',reply)
            # Keep response below provider UTF-8 text limit.
            if len(reply.encode())>1900:reply=reply.encode()[:1800].decode('utf-8','ignore')+'\n（内容已截断，完整记录请在网站查看）'
            db.execute('DELETE FROM wecom_command_requests WHERE created_at<?',(now-86400,))
            db.execute('INSERT INTO wecom_command_requests VALUES(?,?,?,?)',(reqid,identity,cipher().encrypt(reply.encode()),now))
        return {'reply':reply}

    def transport(db,ident):
        row=db.execute("SELECT encrypted_config FROM forward_channels WHERE id=? AND provider='wecom_app'",(ident,)).fetchone()
        if not row:raise HTTPException(404,'渠道不存在')
        conf=json.loads(cipher().decrypt(row[0]))
        if not conf.get('command_token') or not conf.get('command_aes_key'):raise HTTPException(404,'渠道回调未配置')
        return conf
    @app.api_route('/wecom/channels/{ident}',methods=['GET','POST'])
    async def channel_callback(ident:str,request:Request):
        with database() as db:conf=transport(db,ident)
        q=request.query_params
        timestamp,nonce,signature=q.get('timestamp',''),q.get('nonce',''),q.get('msg_signature','')
        if len(timestamp)>20 or len(nonce)>128:raise HTTPException(400,'参数无效')
        try:
            if abs(time.time()-int(timestamp))>600:raise ValueError()
            if request.method=='GET':encrypted=q.get('echostr','')
            else:
                body=await request.body()
                if len(body)>65536 or b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():raise ValueError()
                encrypted=ET.fromstring(body).findtext('Encrypt') or ''
            if len(encrypted)>65536:raise ValueError()
            if not hmac.compare_digest(hashlib.sha1(''.join(sorted([conf['command_token'],timestamp,nonce,encrypted])).encode()).hexdigest(),signature):raise ValueError()
            key=base64.b64decode(conf['command_aes_key']+'=',validate=True)
            dec=Cipher(algorithms.AES(key),modes.CBC(key[:16])).decryptor();plain=dec.update(base64.b64decode(encrypted,validate=True))+dec.finalize()
            pad=plain[-1]
            if not 1<=pad<=32 or plain[-pad:]!=bytes([pad])*pad:raise ValueError()
            plain=plain[:-pad];size=struct.unpack('!I',plain[16:20])[0]
            if plain[20+size:]!=conf['corp_id'].encode():raise ValueError()
            payload=plain[20:20+size]
            if request.method=='GET':return Response(payload,media_type='text/plain')
            if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():raise ValueError()
            msg=ET.fromstring(payload)
            if msg.findtext('ToUserName')!=conf['corp_id'] or str(msg.findtext('AgentID'))!=str(conf['agent_id']):raise ValueError()
            if msg.findtext('MsgType')!='text':return Response('success')
            member=msg.findtext('FromUserName') or '';content=msg.findtext('Content') or '';mid=msg.findtext('MsgId') or ''
            if not member or not mid or len(member)>128 or len(content)>2048:raise ValueError()
        except (ValueError,TypeError,IndexError,KeyError,struct.error,ET.ParseError):raise HTTPException(403,'无效回调')
        reply=run(member,content.strip(),conf['corp_id'],conf['agent_id'],mid,ident)['reply']
        xml=ET.Element('xml')
        for k,v in [('ToUserName',member),('FromUserName',conf['corp_id']),('CreateTime',str(int(time.time()))),('MsgType','text'),('Content',reply)]:ET.SubElement(xml,k).text=v
        out=ET.tostring(xml,encoding='utf-8');data=secrets.token_bytes(16)+struct.pack('!I',len(out))+out+conf['corp_id'].encode();pad=32-len(data)%32
        enc=Cipher(algorithms.AES(key),modes.CBC(key[:16])).encryptor();value=base64.b64encode(enc.update(data+bytes([pad])*pad)+enc.finalize()).decode()
        now=str(int(time.time()));n=secrets.token_hex(8);sig=hashlib.sha1(''.join(sorted([conf['command_token'],now,n,value])).encode()).hexdigest()
        wrapper=ET.Element('xml')
        for k,v in [('Encrypt',value),('MsgSignature',sig),('TimeStamp',now),('Nonce',n)]:ET.SubElement(wrapper,k).text=v
        return Response(ET.tostring(wrapper,encoding='utf-8'),media_type='application/xml')
