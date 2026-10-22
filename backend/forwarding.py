"""Encrypted outbound channels, transactional outbox, and provider adapters."""
import asyncio, base64, hashlib, hmac, http.client, ipaddress, json, re
import smtplib, socket, ssl, string, time, uuid
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlencode, quote
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

CATALOG = {
 'telegram': ('Telegram Bot', [('token','Bot Token'),('chat_id','Chat ID')]),
 'wecom': ('企业微信 Webhook（群机器人）', [('url','群机器人 Webhook URL')]),
 'wecom_app': ('企业微信自建应用', [('corp_id','企业 ID（CorpID）'),('agent_id','应用 AgentId'),('secret','应用 Secret'),('to_user','成员 UserID（多个用 | 分隔，可选）'),('to_party','部门 ID（多个用 | 分隔，可选）'),('to_tag','标签 ID（多个用 | 分隔，可选）')]),
 'dingtalk': ('钉钉机器人', [('url','机器人 Webhook URL'),('secret','签名密钥（可选）')]),
 'feishu': ('飞书机器人', [('url','机器人 Webhook URL'),('secret','签名密钥（可选）')]),
 'bark': ('Bark', [('url','推送地址（例如 https://api.day.app/设备Key）')]),
 'serverchan': ('Server酱 Turbo / 3', [('sendkey','SendKey（SCT 或 sctp 开头）')]),
 'pushplus': ('PushPlus', [('token','Token'),('topic','群组编码（可选）')]),
 'ntfy': ('ntfy', [('url','完整 Topic 地址'),('token','访问令牌（可选）')]),
 'gotify': ('Gotify', [('url','服务地址（不含 /message）'),('token','Application Token')]),
 'smtp': ('SMTP 邮件', [('host','SMTP 主机'),('port','端口（465 或 587）'),('username','登录用户名（可选）'),('password','密码或授权码（可选）'),('from','发件邮箱'),('to','收件邮箱（逗号分隔）')]),
 'webhook': ('自定义 HTTP Webhook', [('url','HTTPS 地址'),('token','Bearer Token（可选）')]),
}
DEFAULT_TEMPLATE='收到短信\n接收：{recipient}\n发送：{sender}\n时间：{received_at}\n{body}'
DEFAULT_CALL_TEMPLATE='{event_label}\n接收：{recipient}\n主叫：{sender}\n时间：{received_at}'
dbctx=crypto=serialize=None
wecom_tokens={}

class DeliveryError(Exception):
 def __init__(self, code, permanent=False): self.code,self.permanent=code,permanent

def public_address(host, port):
 addresses=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
 ips=list(dict.fromkeys(a[4][0] for a in addresses))
 if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
  raise DeliveryError('destination_not_public',True)
 return ips[0]

class PinnedHTTPS(http.client.HTTPSConnection):
 def connect(self):
  ip=public_address(self.host,self.port)
  sock=socket.create_connection((ip,self.port),self.timeout)
  try:self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
  except Exception:sock.close();raise

def http_post(url, payload, headers=None, method='POST'):
 u=urlsplit(url)
 if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment:
  raise DeliveryError('https_url_required',True)
 conn=PinnedHTTPS(u.hostname,u.port or 443,timeout=12,context=ssl.create_default_context())
 try:
  body=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
  conn.request(method,(u.path or '/')+('?' + u.query if u.query else ''),body,
    {'Content-Type':'application/json; charset=utf-8',**(headers or {})})
  response=conn.getresponse();raw=response.read(131073)
  if not 200<=response.status<300:
   raise DeliveryError('http_'+str(response.status),400<=response.status<500 and response.status not in (408,429))
  if len(raw)>131072:raise DeliveryError('response_too_large',True)
  if not raw:return {}
  try:return json.loads(raw)
  except (ValueError,UnicodeDecodeError):return {}
 finally:conn.close()

def wecom_app_token(c, refresh=False):
 key=hashlib.sha256(json.dumps([c['corp_id'],c['secret']]).encode()).hexdigest()
 cached=wecom_tokens.get(key)
 if not refresh and cached and cached[1]>time.time()+120:return cached[0]
 response=http_post('https://qyapi.weixin.qq.com/cgi-bin/gettoken?'+urlencode({'corpid':c['corp_id'],'corpsecret':c['secret']}),None,method='GET')
 if not isinstance(response,dict) or response.get('errcode')!=0 or not response.get('access_token'):
  code=response.get('errcode','invalid_response') if isinstance(response,dict) else 'invalid_response'
  raise DeliveryError('wecom_token_'+str(code),code not in (-1,45009))
 token=response['access_token'];expires=int(response.get('expires_in',7200))
 # Only memory cache; secrets and upstream error text never enter logs.
 if len(wecom_tokens)>100:wecom_tokens.clear()
 wecom_tokens[key]=(token,time.time()+max(0,min(7200,expires)))
 return token

def send_wecom_app(c,text,jobid):
 payload={'agentid':int(c['agent_id']),'msgtype':'text','text':{'content':text},'safe':0}
 for source,target in [('to_user','touser'),('to_party','toparty'),('to_tag','totag')]:
  if c.get(source):payload[target]=c[source]
 for attempt in range(2):
  token=wecom_app_token(c,refresh=bool(attempt))
  response=http_post('https://qyapi.weixin.qq.com/cgi-bin/message/send?'+urlencode({'access_token':token}),payload,{'Idempotency-Key':jobid})
  if not isinstance(response,dict):raise DeliveryError('wecom_invalid_response')
  code=response.get('errcode')
  if code in (40014,42001) and attempt==0:continue
  if code!=0:raise DeliveryError('wecom_app_'+str(code),code not in (-1,45009,45011))
  if any(response.get(k) for k in ('invaliduser','invalidparty','invalidtag','unlicenseduser')):
   # Some recipients may already have received it; do not automatically resend.
   raise DeliveryError('wecom_partial_recipient_rejection',True)
  return

def send_mail(c,text,title,jobid):
 port=int(c.get('port') or 465)
 if port not in (465,587):raise DeliveryError('smtp_tls_port_required',True)
 host=c['host'];ip=public_address(host,port);context=ssl.create_default_context()
 msg=EmailMessage();msg['Subject']=title;msg['From']=c['from'];msg['To']=c['to']
 msg['Message-ID']='<'+jobid+'@sms-console.local>';msg.set_content(text)
 smtp=smtplib.SMTP_SSL(timeout=12,context=context) if port==465 else smtplib.SMTP(timeout=12)
 try:
  smtp._host=host;smtp.connect(ip,port)
  if port==587:smtp.ehlo();smtp.starttls(context=context);smtp.ehlo()
  if c.get('username'):smtp.login(c['username'],c.get('password',''))
  if smtp.send_message(msg):raise DeliveryError('smtp_partial_refusal',True)
 finally:
  try:smtp.quit()
  except Exception:smtp.close()

def deliver(provider,c,text,jobid,title='短信通知'):
 headers={'Idempotency-Key':jobid}
 limit=1800 if provider in ('wecom','wecom_app','dingtalk','feishu') else 3500
 if len(text.encode())>limit:
  text=text.encode()[:limit-120].decode('utf-8','ignore')+'\n…（内容过长，完整内容请到后台查看）'
 if provider=='smtp':return send_mail(c,text,title,jobid)
 if provider=='wecom_app':return send_wecom_app(c,text,jobid)
 if provider=='telegram':
  url='https://api.telegram.org/bot'+c['token']+'/sendMessage';payload={'chat_id':c['chat_id'],'text':text[:4000]}
 elif provider in ('wecom','dingtalk'):
  url=c['url'];payload={'msgtype':'text','text':{'content':text[:1800]}}
  if provider=='dingtalk' and c.get('secret'):
   stamp=str(int(time.time()*1000));secret=c['secret']
   sig=base64.b64encode(hmac.new(secret.encode(),(stamp+'\n'+secret).encode(),hashlib.sha256).digest()).decode()
   url+=('&' if '?' in url else '?')+urlencode({'timestamp':stamp,'sign':sig})
 elif provider=='feishu':
  url=c['url'];payload={'msg_type':'text','content':{'text':text[:1800]}}
  if c.get('secret'):
   stamp=str(int(time.time()));payload.update(timestamp=stamp,sign=base64.b64encode(hmac.new((stamp+'\n'+c['secret']).encode(),b'',hashlib.sha256).digest()).decode())
 elif provider=='bark':url=c['url'];payload={'title':title,'body':text[:3500]}
 elif provider=='serverchan':
  key=c['sendkey'];match=re.match(r'sctp(\d+)t',key)
  url=('https://'+match.group(1)+'.push.ft07.com/send/' if match else 'https://sctapi.ftqq.com/')+quote(key,safe='')+'.send'
  payload={'title':title,'desp':text[:4000]}
 elif provider=='pushplus':url='https://www.pushplus.plus/send';payload={'token':c['token'],'title':title,'content':text[:4000],'template':'txt','topic':c.get('topic','')}
 elif provider=='ntfy':
  u=urlsplit(c['url']);topic=u.path.strip('/')
  url=u.scheme+'://'+u.netloc;payload={'topic':topic,'title':title,'message':text[:4000]}
  if c.get('token'):headers['Authorization']='Bearer '+c['token']
 elif provider=='gotify':url=c['url'].rstrip('/')+'/message';payload={'title':title,'message':text[:4000],'priority':5};headers['X-Gotify-Key']=c['token']
 elif provider=='webhook':
  url=c['url'];payload={'id':jobid,'title':title,'text':text}
  if c.get('token'):headers['Authorization']='Bearer '+c['token']
 else:raise DeliveryError('unsupported_provider',True)
 result=http_post(url,payload,headers)
 if not isinstance(result,dict):raise DeliveryError('invalid_response')
 if provider=='telegram' and result.get('ok') is not True:raise DeliveryError('telegram_rejected')
 if provider in ('wecom','dingtalk') and result.get('errcode')!=0:raise DeliveryError('robot_rejected')
 if provider=='feishu' and result.get('code',result.get('StatusCode'))!=0:raise DeliveryError('robot_rejected')
 if provider=='bark' and result.get('code')!=200:raise DeliveryError('bark_rejected')
 if provider=='pushplus' and result.get('code')!=200:raise DeliveryError('pushplus_rejected')
 if provider=='serverchan' and result.get('code') not in (0,200):raise DeliveryError('serverchan_rejected')
 if provider=='gotify' and not result.get('id'):raise DeliveryError('gotify_rejected')

def initialize():
 with dbctx() as db:
  db.executescript('''CREATE TABLE IF NOT EXISTS forward_channels(
   id TEXT PRIMARY KEY,name TEXT NOT NULL,provider TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,
   encrypted_config BLOB NOT NULL,rules TEXT NOT NULL,created_after_id INTEGER NOT NULL);
   CREATE TABLE IF NOT EXISTS forward_jobs(
   id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,message_id INTEGER NOT NULL,
   state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at INTEGER NOT NULL,
   result TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,
   UNIQUE(channel_id,message_id));
   CREATE INDEX IF NOT EXISTS forward_due ON forward_jobs(state,next_at);
   CREATE TABLE IF NOT EXISTS forward_call_seen(channel_id TEXT NOT NULL,call_key TEXT NOT NULL,PRIMARY KEY(channel_id,call_key));
  ''')
  if 'source_kind' not in {r[1] for r in db.execute('PRAGMA table_info(forward_jobs)')}:
   db.execute("ALTER TABLE forward_jobs ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'sms'")
  admin=db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
  if admin:db.execute('INSERT OR IGNORE INTO channel_owners(channel_id,user_id) SELECT id,? FROM forward_channels',(admin[0],))
  db.execute("UPDATE forward_jobs SET state='retry',result='interrupted_delivery_unknown' WHERE state='sending'")

def enqueue(db, message_id):
 # Same transaction as message insert: a crash cannot lose the outbox entry.
 now=int(time.time())
 for c in db.execute('SELECT id,rules FROM forward_channels WHERE enabled=1 AND created_after_id<?',(message_id,)).fetchall():
  if 'sms' not in json.loads(c['rules']).get('events',['sms']):continue
  db.execute("INSERT OR IGNORE INTO forward_jobs(id,channel_id,message_id,state,next_at,created_at,updated_at) VALUES(?,?,?,'pending',?,?,?)",(uuid.uuid4().hex,c['id'],message_id,now,now,now))

def process_one():
 now=int(time.time())
 with dbctx() as db:
  row=db.execute("SELECT j.*,c.provider,c.enabled,c.encrypted_config,c.rules FROM forward_jobs j JOIN forward_channels c ON c.id=j.channel_id WHERE j.state IN ('pending','retry') AND j.next_at<=? AND c.enabled=1 ORDER BY j.next_at LIMIT 1",(now,)).fetchone()
  if not row:return
  kind=row['source_kind'];rules=json.loads(row['rules'])
  if kind not in rules.get('events',['sms']):
   db.execute("UPDATE forward_jobs SET state='cancelled',result='event_type_disabled' WHERE id=?",(row['id'],));return
  msg=db.execute('SELECT * FROM '+('call_events' if kind=='call' else 'messages')+' WHERE id=?',(abs(row['message_id']),)).fetchone()
  if not msg:db.execute("UPDATE forward_jobs SET state='cancelled',result='message_removed' WHERE id=?",(row['id'],));return
  permitted=db.execute('SELECT 1 FROM channel_owners c JOIN number_owners n ON n.user_id=c.user_id JOIN users u ON u.id=c.user_id WHERE c.channel_id=? AND n.number_key=? AND n.deleted=0 AND u.disabled=0',(row['channel_id'],msg['account_key'])).fetchone()
  if not permitted:
   db.execute("UPDATE forward_jobs SET state='cancelled',result='ownership_changed',updated_at=? WHERE id=?",(now,row['id']));return
  if kind=='call':
   record=json.loads(crypto().decrypt(msg['encrypted_record']))
   label={'incoming':'收到来电','missed':'未接来电','ended':'收到来电结束记录'}.get(record.get('event'),'来电状态通知')
   item={'id':msg['id'],'recipient':record['account'],'sender':record.get('sender',''),
     'received_at':msg['received_at'],'source':'无忧行','event_label':label,'kind':'call','body':label}
   clear=label+' '+item['sender']+' '+item['recipient']
  else:
   item=serialize(msg,reveal=rules.get('allow_sensitive',False));item.update(kind='sms',event_label='收到短信')
   clear=json.loads(crypto().decrypt(msg['encrypted_record']))['body']
  match=(not rules['accounts'] or msg['account_key'] in rules['accounts']) and (not rules['include'] or any(k in clear for k in rules['include'])) and not any(k in clear for k in rules['exclude'])
  if not match:db.execute("UPDATE forward_jobs SET state='skipped',result='rule_filtered',updated_at=? WHERE id=?",(now,row['id']));return
  stamp=datetime.fromisoformat(str(item['received_at']).replace('Z','+00:00'))
  item['received_at']=stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
  text=(rules.get('call_template',DEFAULT_CALL_TEMPLATE) if kind=='call' else rules['template']).format_map(item)
  if rules.get('mask_numbers'):
   for k in ('recipient','sender'):
    v=item[k];text=text.replace(v,re.sub(r'(\d{3})\d{4}(\d{4})$',r'\1****\2',v)) if v else text
  db.execute("UPDATE forward_jobs SET state='sending',attempts=attempts+1,updated_at=? WHERE id=?",(now,row['id']))
 try:
  deliver(row['provider'],json.loads(crypto().decrypt(row['encrypted_config'])),text,row['id'],'来电通知' if kind=='call' else '短信通知');state,result='sent','accepted_by_provider'
 except DeliveryError as exc:state,result=('failed' if exc.permanent or row['attempts']>=7 else 'retry'),exc.code
 except (ValueError,KeyError):state,result='failed','invalid_configuration'
 except Exception:state,result=('failed' if row['attempts']>=7 else 'retry'),'connection_or_delivery_error'
 with dbctx() as db:
  db.execute('UPDATE forward_jobs SET state=?,result=?,next_at=?,updated_at=? WHERE id=? AND state=\'sending\'',(state,result,int(time.time())+min(3600,30*2**row['attempts']),int(time.time()),row['id']))

async def loop():
 while True:
  try:await asyncio.to_thread(process_one)
  except Exception:pass # No upstream response bodies or credentials enter logs.
  await asyncio.sleep(2)

class Channel(BaseModel):
 name:str=Field(min_length=1,max_length=80)
 provider:str
 enabled:bool=False
 config:dict[str,str]=Field(default_factory=dict)
 accounts:list[str]=Field(default_factory=list,max_length=100)
 include:list[str]=Field(default_factory=list,max_length=30)
 exclude:list[str]=Field(default_factory=list,max_length=30)
 template:str=Field(default=DEFAULT_TEMPLATE,min_length=1,max_length=1500)
 allow_sensitive:bool=False
 mask_numbers:bool=False
 events:list[str]=Field(default_factory=lambda:['sms'],min_length=1,max_length=2)
 call_template:str=Field(default=DEFAULT_CALL_TEMPLATE,min_length=1,max_length=1500)
 commands_enabled:bool=False
 command_members:list[str]=Field(default_factory=list,max_length=100)

def register(app,database,cipher,serialize_message,current_user):
 global dbctx,crypto,serialize
 dbctx,crypto,serialize=database,cipher,serialize_message
 @app.get('/api/forwarding')
 def listing(user=Depends(current_user)):
  with dbctx() as db:
   visible=[r[0] for r in db.execute('SELECT id FROM forward_channels')] if user.get('admin_scope') else [r[0] for r in db.execute('SELECT channel_id FROM channel_owners WHERE user_id=?',(user['user_id'],))]
   channels=[{'id':r['id'],'name':r['name'],'provider':r['provider'],'enabled':bool(r['enabled']),'events':['sms'],'call_template':DEFAULT_CALL_TEMPLATE,**json.loads(r['rules'])} for r in db.execute('SELECT * FROM forward_channels')]
   channels=[c for c in channels if c['id'] in visible]
   visible_keys={'chat_id','corp_id','agent_id','to_user','to_party','to_tag','topic','host','port','username','from','to'}
   for channel in channels:
    owner=db.execute('SELECT user_id FROM channel_owners WHERE channel_id=?',(channel['id'],)).fetchone()
    channel['owner_id']=owner[0] if owner else user['user_id']
    row=db.execute('SELECT encrypted_config FROM forward_channels WHERE id=?',(channel['id'],)).fetchone()
    config=json.loads(crypto().decrypt(row['encrypted_config']))
    channel['config']={k:v for k,v in config.items() if k in visible_keys}
    channel['configured_fields']=[k for k,v in config.items() if v]
    from wecom_commands import config as bridge_config
    bridge=bridge_config() or {}
    channel['command_existing_callback']=bool(config.get('corp_id')==bridge.get('corp_id') and str(config.get('agent_id'))==str(bridge.get('agent_id')))
    channel['command_callback_ready']=bool(channel['command_existing_callback'] or (config.get('command_token') and config.get('command_aes_key')))
    channel.setdefault('commands_enabled',False);channel.setdefault('command_members',[])
   logs=[{**dict(r),'message_id':abs(r['message_id'])} for r in db.execute('SELECT j.id,j.message_id,j.source_kind,j.state,j.attempts,j.next_at,j.result,j.created_at,j.updated_at,c.name FROM forward_jobs j JOIN forward_channels c ON c.id=j.channel_id WHERE c.id IN (SELECT channel_id FROM channel_owners WHERE user_id=?) OR ?=1 ORDER BY j.created_at DESC,j.id DESC LIMIT 100',(user['user_id'],int(user.get('admin_scope'))))]
   logs=[l for l in logs if db.execute('SELECT 1 FROM channel_owners WHERE channel_id=(SELECT channel_id FROM forward_jobs WHERE id=?) AND user_id=?',(l['id'],user['user_id'])).fetchone()] if not user.get('admin_scope') else logs
   numbers=[]
   rows=db.execute('SELECT n.number_key,n.encrypted_profile,o.user_id FROM managed_numbers n JOIN number_owners o ON o.number_key=n.number_key WHERE o.deleted=0'+('' if user.get('admin_scope') else ' AND o.user_id=?'),[] if user.get('admin_scope') else [user['user_id']]).fetchall()
   for row in rows:
    profile=json.loads(crypto().decrypt(row['encrypted_profile']))
    numbers.append({'id':row['number_key'],'number':profile['number'],'label':profile.get('label',''),'owner_id':row['user_id']})
  return {'numbers':numbers,'current_user_id':user['user_id'],'channels':channels,'logs':logs,'providers':[{'id':k,'name':v[0],'fields':[{'key':f[0],'label':f[1]} for f in v[1]]} for k,v in CATALOG.items()]}
 def save(data,ident,user):
  if data.provider not in CATALOG:raise HTTPException(422,'渠道类型不支持')
  try:
   allowed={'recipient','sender','body','received_at','source','id','kind','event_label'}
   for template in (data.template,data.call_template):
    for _,field,spec,conversion in string.Formatter().parse(template):
     if field is not None and (field not in allowed or spec or conversion):raise ValueError()
   if any(v not in ('sms','call') for v in data.events) or len(set(data.events))!=len(data.events):raise ValueError()
   if any(not k or len(k)>100 for k in data.include+data.exclude):raise ValueError()
   if any(not re.fullmatch('[a-f0-9]{64}',k) for k in data.accounts):raise ValueError()
  except ValueError:raise HTTPException(422,'模板或筛选规则格式无效')
  with dbctx() as db:
   db.execute('BEGIN IMMEDIATE')
   old=db.execute('SELECT * FROM forward_channels WHERE id=?',(ident,)).fetchone()
   if old and not user.get('admin_scope') and not db.execute('SELECT 1 FROM channel_owners WHERE channel_id=? AND user_id=?',(ident,user['user_id'])).fetchone():raise HTTPException(404,'渠道不存在')
   owner=db.execute('SELECT user_id FROM channel_owners WHERE channel_id=?',(ident,)).fetchone()
   owner_id=owner[0] if owner else user['user_id']
   allowed_accounts={r[0] for r in db.execute('SELECT number_key FROM number_owners WHERE user_id=? AND deleted=0',(owner_id,))}
   if any(k not in allowed_accounts for k in data.accounts):raise HTTPException(422,'只能选择渠道所属用户的号码')
   if old and old['provider']!=data.provider:raise HTTPException(422,'修改渠道类型请新建渠道')
   config=json.loads(crypto().decrypt(old['encrypted_config'])) if old else {}
   keys={f[0] for f in CATALOG[data.provider][1]} | ({'command_token','command_aes_key'} if data.provider=='wecom_app' else set())
   if any(k not in keys or len(v)>4096 or '\r' in v or '\n' in v for k,v in data.config.items()):raise HTTPException(422,'渠道配置格式无效')
   config.update({k:v.strip() for k,v in data.config.items() if v.strip()})
   optional={'secret','topic','username','password','to_user','to_party','to_tag','command_token','command_aes_key'} | ({'token'} if data.provider in ('ntfy','webhook') else set())
   if data.provider=='wecom_app':optional.discard('secret')
   if not all(config.get(k) for k in keys-optional):raise HTTPException(422,'请填写渠道必需参数')
   if config.get('url'):
    try:u=urlsplit(config['url']);valid=u.scheme=='https' and u.hostname and not u.username and not u.password and not u.fragment;u.port
    except ValueError:valid=False
    if not valid:raise HTTPException(422,'推送地址必须为有效 HTTPS URL')
   if data.provider=='telegram' and not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',config['token']):raise HTTPException(422,'Bot Token 格式无效')
   if data.provider=='wecom_app':
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',config['corp_id']) or not re.fullmatch(r'[1-9]\d{0,12}',config['agent_id']):raise HTTPException(422,'企业 ID 或 AgentId 格式无效')
    if not any(config.get(k) for k in ('to_user','to_party','to_tag')):raise HTTPException(422,'请至少填写一种接收成员、部门或标签')
    for k in ('to_party','to_tag'):
     if config.get(k) and not re.fullmatch(r'\d+(\|\d+)*',config[k]):raise HTTPException(422,'部门和标签请填写数字ID，多个用 | 分隔')
    if config.get('to_user') and not re.fullmatch(r'(@all|[A-Za-z0-9_.@=-]+(\|[A-Za-z0-9_.@=-]+)*)',config['to_user']):raise HTTPException(422,'成员请填写 UserID，多个用 | 分隔')
   if data.provider=='serverchan' and not re.fullmatch(r'(SCT[A-Za-z0-9]+|sctp\d+t[A-Za-z0-9]+)',config['sendkey']):raise HTTPException(422,'SendKey 格式无效，支持 Turbo 和 Server酱3')
   if data.provider=='smtp':
    if config.get('port','465') not in ('465','587'):raise HTTPException(422,'SMTP 使用465或587端口加密传输')
    if any(not re.fullmatch(r'[^\s@,]+@[^\s@,]+\.[^\s@,]+',v.strip()) for v in [config['from']]+config['to'].split(',')):raise HTTPException(422,'邮箱格式无效')
   if data.commands_enabled and data.provider!='wecom_app':raise HTTPException(422,'仅企业微信应用支持指令查询')
   if any(not re.fullmatch(r'[A-Za-z0-9_.@=-]{1,128}',m) or m=='@all' for m in data.command_members) or len(set(data.command_members))!=len(data.command_members):raise HTTPException(422,'查询成员填写明确的成员UserID，不支持@all或重复成员')
   if data.provider=='wecom_app':
    from wecom_commands import config as bridge_config
    bridge=bridge_config() or {}
    existing=config.get('corp_id')==bridge.get('corp_id') and str(config.get('agent_id'))==str(bridge.get('agent_id'))
    if config.get('command_token') and not re.fullmatch('[A-Za-z0-9]{3,32}',config['command_token']):raise HTTPException(422,'回调Token为3至32位字母或数字')
    if config.get('command_aes_key'):
     try:
      if len(config['command_aes_key'])!=43 or len(base64.b64decode(config['command_aes_key']+'=',validate=True))!=32:raise ValueError()
     except Exception:raise HTTPException(422,'EncodingAESKey须为43位有效编码')
    if data.commands_enabled:
     if not data.command_members:raise HTTPException(422,'请至少填写一个允许查询的成员UserID')
     if not existing and not (config.get('command_token') and config.get('command_aes_key')):raise HTTPException(422,'请配置该应用的回调Token和EncodingAESKey')
     if data.enabled:
      for c in db.execute("SELECT id,rules,encrypted_config FROM forward_channels WHERE provider='wecom_app' AND enabled=1 AND id!=?",(ident,)):
       other=json.loads(c['rules']);cfg=json.loads(crypto().decrypt(c['encrypted_config']))
       if other.get('commands_enabled') and cfg.get('corp_id')==config.get('corp_id') and str(cfg.get('agent_id'))==str(config.get('agent_id')) and set(other.get('command_members',[]))&set(data.command_members):raise HTTPException(409,'此应用的部分查询成员已用于其他渠道，请避免权限范围冲突')
   rules={k:getattr(data,k) for k in ('accounts','include','exclude','template','allow_sensitive','mask_numbers','events','call_template','commands_enabled','command_members')}
   previous=json.loads(old['rules']) if old else {}
   rules['after_call_id']=previous.get('after_call_id',0) if 'call' in previous.get('events',[]) else db.execute('SELECT COALESCE(MAX(id),0) FROM call_events').fetchone()[0]
   cutoff=old['created_after_id'] if old else db.execute('SELECT COALESCE(MAX(id),0) FROM messages').fetchone()[0]
   if old:
    db.execute("UPDATE forward_jobs SET state='cancelled',result='channel_updated',updated_at=? WHERE channel_id=? AND state IN ('pending','retry')",(int(time.time()),ident))
   db.execute('INSERT OR REPLACE INTO forward_channels VALUES(?,?,?,?,?,?,?)',(ident,data.name,data.provider,int(data.enabled),crypto().encrypt(json.dumps(config).encode()),json.dumps(rules),cutoff))
   db.execute('INSERT OR IGNORE INTO channel_owners(channel_id,user_id) VALUES(?,?)',(ident,user['user_id']))
   db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'forward_channel_updated',?)",(user['user_id'],int(time.time())))
  return {'id':ident}
 @app.post('/api/forwarding')
 def create(data:Channel,user=Depends(current_user)):return save(data,uuid.uuid4().hex,user)
 @app.put('/api/forwarding/{ident}')
 def update(ident:str,data:Channel,user=Depends(current_user)):
  with dbctx() as db:
   if not db.execute('SELECT 1 FROM forward_channels WHERE id=?',(ident,)).fetchone():raise HTTPException(404,'渠道不存在')
  return save(data,ident,user)
 @app.post('/api/forwarding/jobs/{ident}/retry')
 def retry(ident:str,user=Depends(current_user)):
  with dbctx() as db:
   if not user.get('admin_scope') and not db.execute('SELECT 1 FROM forward_jobs j JOIN channel_owners o ON o.channel_id=j.channel_id WHERE j.id=? AND o.user_id=?',(ident,user['user_id'])).fetchone():raise HTTPException(404,'任务不存在')
   row=db.execute('SELECT j.state,c.enabled FROM forward_jobs j JOIN forward_channels c ON c.id=j.channel_id WHERE j.id=?',(ident,)).fetchone()
   if not row or row['state']!='failed' or not row['enabled']:raise HTTPException(409,'仅可重试已启用渠道的失败任务')
   db.execute("UPDATE forward_jobs SET state='retry',attempts=0,next_at=?,updated_at=?,result='manual_retry' WHERE id=?",(int(time.time()),int(time.time()),ident))
   db.execute("INSERT INTO audit(user_id,action,created_at) VALUES(?,'forward_manual_retry',?)",(user['user_id'],int(time.time())))
  return {'ok':True}
