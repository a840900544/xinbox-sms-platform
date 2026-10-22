"""Adapter for /opt/wecom-callback/wecom_bot, after verified callback decryption."""
import hashlib,hmac,json,time
from pathlib import Path
import httpx

CONFIG=Path('/etc/wecom-sms-command-bridge.json')

def recognized(text):
    return text.split(maxsplit=1)[0] in {'绑定','帮助','短信帮助','信匣帮助','/sms','号码列表','我的号码','短信号码','状态','短信','来电'} if text.strip() else False

async def handle_sms_command(message,client):
    text=(message.content or '').strip()
    if not recognized(text):return False
    try:
        cfg=json.loads(CONFIG.read_text())
    except (OSError,ValueError):
        await client.send_text(message.from_user,'信匣指令服务尚未启用。')
        return True
    if message.to_user!=cfg['corp_id'] or str(getattr(message,'agent_id',''))!=str(cfg['agent_id']):
        return True
    if not message.msg_id:return True
    body=json.dumps({'corp_id':cfg['corp_id'],'agent_id':cfg['agent_id'],'member':message.from_user,'content':text,'message_id':message.msg_id,'timestamp':int(time.time())},ensure_ascii=False,separators=(',',':')).encode()
    signature=hmac.new(cfg['key'].encode(),body,hashlib.sha256).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=8.0,trust_env=False) as http:
            response=await http.post('http://127.0.0.1:8787/internal/wecom/command',content=body,headers={'Content-Type':'application/json','X-Wecom-Bridge-Signature':signature})
            response.raise_for_status()
            reply=response.json()['reply']
    except Exception:
        reply='信匣查询服务暂时不可用，请稍后重发指令。'
    await client.send_text(message.from_user,reply)
    return True
