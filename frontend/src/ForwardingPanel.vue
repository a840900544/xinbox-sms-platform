<script setup>
function displayPhone(v){return String(v||'').replace(/^\+86(?=1\d{10}$)/,'')}

import {ask} from './ui'
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
const props=defineProps({api:Function,numbers:Array})
const channels=ref([]),logs=ref([]),providers=ref([]),editing=ref(null),busy=ref(false),error=ref(''),message=ref('')
const allNumbers=ref([]),currentUserId=ref(null)
const availableNumbers=computed(()=>allNumbers.value.filter(n=>n.owner_id===(editing.value?.owner_id??currentUserId.value)))
const origin=window.location.origin
const modal=ref(null), logPage=ref(1), logFilter=ref('all')
const filteredLogs=computed(()=>logs.value.filter(l=>logFilter.value==='all'||(logFilter.value==='failed'?['failed','retry'].includes(l.state):l.state===logFilter.value)))
const logPages=computed(()=>Math.max(1,Math.ceil(filteredLogs.value.length/10)))
const visibleLogs=computed(()=>filteredLogs.value.slice((logPage.value-1)*10,logPage.value*10))
watch(logFilter,()=>logPage.value=1)
watch(logPages,n=>{if(logPage.value>n)logPage.value=n})
let initial='', opener, timer
watch(editing,async value=>{if(value){await nextTick();modal.value?.showModal()}else modal.value?.close()})
async function close(){if(busy.value)return;if(editing.value&&JSON.stringify(editing.value)!==initial&&!await ask('还有未保存的修改，确定放弃吗？'))return;editing.value=null;nextTick(()=>opener?.focus())}
function isSecret(key){return /token|secret|password|sendkey|url/.test(key)}
const callTemplate='{event_label}\n接收：{recipient}\n主叫：{sender}\n时间：{received_at}'
const template='收到短信\n接收：{recipient}\n发送：{sender}\n时间：{received_at}\n{body}'
const selected=computed(()=>providers.value.find(p=>p.id===editing.value?.provider))
const states={pending:'等待发送',sending:'发送中',retry:'等待重试',sent:'平台已接受',failed:'发送失败',skipped:'规则已过滤',cancelled:'已取消'}
const reasons={ownership_changed:'号码归属已变化或已删除，停止投递',accepted_by_provider:'平台已接受，不代表终端已阅读',rule_filtered:'不符合转发规则',channel_updated:'渠道修改后取消旧任务',connection_or_delivery_error:'连接或发送异常',destination_not_public:'目的地址不是公网地址',invalid_configuration:'渠道配置无效',wecom_partial_recipient_rejection:'部分接收人无效，其他人可能已收到；请修正接收范围后再决定重发',interrupted_delivery_unknown:'上次发送被中断，结果未知'}
async function retry(id){try{await props.api('/forwarding/jobs/'+id+'/retry',{method:'POST'});await load()}catch(e){error.value=e.message}}
function time(v){if(!v)return '—';const parts=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(v*1000));const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`}
async function load(){try{const d=await props.api('/forwarding');channels.value=d.channels;logs.value=d.logs;providers.value=d.providers;allNumbers.value=d.numbers||[];currentUserId.value=d.current_user_id;error.value=''}catch(e){error.value=e.message}}
function edit(c){error.value='';message.value='';opener=document.activeElement;editing.value=c?{...c,config:{...(c.config||{})},commands_enabled:!!c.commands_enabled,command_members_text:(c.command_members||[]).join('\n'),events:[...(c.events||['sms'])],call_template:c.call_template||callTemplate,accounts:[...c.accounts],include_text:c.include.join('\n'),exclude_text:c.exclude.join('\n')}:{name:'',provider:'telegram',enabled:false,config:{},accounts:[],include_text:'',exclude_text:'',template,events:['sms'],call_template:callTemplate,allow_sensitive:false,mask_numbers:false,commands_enabled:false,command_members_text:''};initial=JSON.stringify(editing.value)}
function providerName(id){return providers.value.find(p=>p.id===id)?.name||id}
async function save(){
 busy.value=true;error.value='';message.value=''
 try{
  const e=editing.value
  const payload={name:e.name,provider:e.provider,enabled:e.enabled,config:e.config,accounts:e.accounts,include:e.include_text.split('\n').map(s=>s.trim()).filter(Boolean),exclude:e.exclude_text.split('\n').map(s=>s.trim()).filter(Boolean),template:e.template,allow_sensitive:e.allow_sensitive,mask_numbers:e.mask_numbers,events:e.events,call_template:e.call_template,commands_enabled:e.provider==='wecom_app'&&e.commands_enabled,command_members:e.provider==='wecom_app'?e.command_members_text.split(/[\n|,，]+/).map(s=>s.trim()).filter(Boolean):[]}
  await props.api('/forwarding'+(e.id?'/'+e.id:''),{method:e.id?'PUT':'POST',body:JSON.stringify(payload)})
  editing.value=null;message.value='已保存。仅转发所选类型的新记录，不补发历史短信或历史来电。';await load()
 }catch(e){error.value=e.message}finally{busy.value=false}
}
onMounted(()=>{load();timer=setInterval(load,10000)})
onUnmounted(()=>{clearInterval(timer);editing.value=null})
</script>
<template>
 <section class="simple-panel forwarding">
  <div class="simple-panel-title"><h3>消息转发渠道</h3><button class="button primary" @click="edit(null)">添加渠道</button></div>
  <p v-if="error" role="alert" class="warning">{{error}}</p><p v-if="message" role="status">{{message}}</p>
  <div v-if="!channels.length" class="empty-state"><h3>尚未配置转发渠道</h3><p>添加你的机器人、推送服务或邮箱，保存后启用。</p></div>
  <div v-for="c in channels" :key="c.id" class="audit-row"><div><strong>{{c.name}}</strong><small>{{providerName(c.provider)}} · {{c.enabled?'已启用':'已停用'}} · {{(c.events||['sms']).map(v=>v==='sms'?'短信':'来电').join(' / ')}} · {{c.accounts.length?'指定 '+c.accounts.length+' 个号码':'本人全部号码'}} · {{c.allow_sensitive?'允许敏感原文':'敏感正文隐藏'}}<span v-if="c.provider==='wecom_app'"> · 指令{{c.commands_enabled&&c.enabled?'开启':'关闭'}}</span></small></div><button class="text-button" @click="edit(c)">配置</button></div>
 </section>
 <dialog ref="modal" class="channel-modal" @cancel.prevent="close"><template v-if="editing">
  <div class="simple-panel-title"><h3>{{editing.id?'渠道配置':'添加渠道'}}</h3><button class="text-button" :disabled="busy" @click="close" aria-label="关闭渠道配置">关闭 ✕</button></div>
  <form id="channel-form" @submit.prevent="save"><p v-if="error" role="alert" class="warning">{{error}}</p>
   <label>渠道名称<input v-model="editing.name" required maxlength="80" placeholder="例如：我的 Telegram"></label>
   <label>转发平台<select v-model="editing.provider" :disabled="!!editing.id" @change="editing.config={}"><option v-for="p in providers" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
   <p class="hint" v-if="editing.id">密钥留空保持不变。</p>
   <label v-for="f in selected?.fields||[]" :key="f.key">{{f.label}}<small v-if="editing.configured_fields?.includes(f.key)&&isSecret(f.key)" class="saved-field">已保存 · 留空保持不变</small><input v-model="editing.config[f.key]" :type="isSecret(f.key)?'password':'text'" autocomplete="off" maxlength="4096" :placeholder="editing.id&&isSecret(f.key)?'已保存，留空保持不变':''"></label>
   <details v-if="editing.provider==='wecom_app'" class="form-help"><summary>企业微信配置帮助</summary><p>企业微信自建应用：接收人需要在应用可见范围内；按企业微信提示配置企业可信 IP。成员填通讯录 UserID，部门和标签填数字 ID；至少选择一种接收方式。填写 @all 会发送给应用可见范围内全员，请谨慎使用。</p></details>
   <fieldset v-if="editing.provider==='wecom_app'" class="commands-settings"><legend>应用指令查询</legend>
    <label class="check"><input type="checkbox" v-model="editing.commands_enabled">允许成员通过此应用查询消息</label>
    <template v-if="editing.commands_enabled"><label class="command-field">允许查询的成员 UserID<textarea v-model="editing.command_members_text" rows="3" maxlength="13000" required placeholder="每行一个成员UserID，不支持@all、部门或标签"></textarea></label>
    <button v-if="editing.config.to_user&&editing.config.to_user!=='@all'" class="text-button" type="button" @click="editing.command_members_text=editing.config.to_user.split('|').join('\n')">使用上方接收成员</button>
    <p class="hint">查询仅限此渠道所属用户的号码，并沿用下方的接收号码、转发类型、敏感正文及号码脱敏设置。关键词只用于自动转发。停用渠道会同时停止查询。</p>
    <div v-if="editing.command_existing_callback" class="existing-callback"><strong>复用服务器已有回调</strong><p>企业ID与应用AgentId已匹配。Token和EncodingAESKey由服务器保存，不回显，也无需重新填写。</p><label class="command-field">当前接收消息 URL<input :value="origin+'/wecom/callback'" readonly @focus="$event.target.select()"></label><small>开启查询并指定允许成员后保存即可；无需更改企业微信后台的现有回调。</small></div>
    <template v-else><label class="command-field">接收消息回调 Token<input v-model="editing.config.command_token" type="password" autocomplete="off" maxlength="32" :placeholder="editing.configured_fields?.includes('command_token')?'已保存，留空保持不变':'与企业微信应用接收消息设置一致'"></label><label class="command-field">接收消息 EncodingAESKey<input v-model="editing.config.command_aes_key" type="password" autocomplete="off" maxlength="43" :placeholder="editing.configured_fields?.includes('command_aes_key')?'已保存，留空保持不变':'企业微信后台生成的43位密钥'"></label><label class="command-field">接收消息 URL<input :value="editing.id?origin+'/wecom/channels/'+editing.id:'先保存渠道，重新打开配置即可复制URL'" readonly @focus="$event.target.select()"></label><p class="hint">其他企业或应用请在企业微信后台「接收消息」中配置上述URL、Token与EncodingAESKey；仅填写推送Secret不能接收指令。</p></template>
    <details class="form-help"><summary>查看指令与权限说明</summary><p>帮助、号码列表、状态 号码尾号、短信 号码尾号、来电 号码尾号。短信/来电最多5条。尾号冲突时使用完整号码。状态仅显示缓存和查询时间，不切换托管。允许查询的成员将能看到该渠道范围内的历史记录，请仅添加获授权的成员。</p></details></template>
   </fieldset>
   <fieldset><legend>转发类型</legend><label class="check"><input type="checkbox" v-model="editing.events" value="sms">短信</label><label class="check"><input type="checkbox" v-model="editing.events" value="call">来电通知</label></fieldset>
   <fieldset><legend>接收号码（也用于指令查询）</legend><p class="hint">不选择表示渠道所属用户的全部号码。</p><label v-for="n in availableNumbers" :key="n.id" class="check"><input type="checkbox" v-model="editing.accounts" :value="n.id">{{displayPhone(n.number)}}{{n.label?' · '+n.label:''}}</label><span v-if="!availableNumbers.length" class="hint">尚未添加号码。</span></fieldset>
   <label>包含关键词<textarea v-model="editing.include_text" rows="3" maxlength="3000"></textarea></label>
   <label>排除关键词<textarea v-model="editing.exclude_text" rows="3" maxlength="3000"></textarea></label>
   <label v-if="editing.events.includes('sms')">短信模板<textarea v-model="editing.template" rows="6" maxlength="1500" required></textarea></label>
   <label v-if="editing.events.includes('call')">来电通知模板<textarea v-model="editing.call_template" rows="5" maxlength="1500" required></textarea></label>
   <details class="form-help"><summary>模板变量</summary><p>可用变量：{recipient} 接收号码、{sender} 发送号码、{body} 正文、{received_at} 时间、{source} 来源、{id} 记录编号、{event_label} 事件名称、{kind} 消息类型。</p></details>
   <label class="check"><input type="checkbox" v-model="editing.mask_numbers">转发时隐藏号码中间四位</label>
   <label class="check"><input type="checkbox" v-model="editing.allow_sensitive">允许把验证码等敏感短信原文发送到此渠道</label>
   <label class="check"><input type="checkbox" v-model="editing.enabled">启用此渠道，将所选类型的新记录发送到上述接收平台</label>
  </form><footer class="modal-footer"><button class="button" :disabled="busy" @click="close">取消</button><button form="channel-form" class="button primary" :disabled="busy || !editing.events.length">{{busy?'保存中…':'保存渠道'}}</button></footer></template></dialog>
 <section class="simple-panel forwarding"><div class="simple-panel-title"><h3>转发日志</h3><button class="text-button" @click="load">刷新</button></div>
  <div v-if="!logs.length" class="empty-state"><p>启用渠道并选择转发类型后，发送状态会显示在这里。</p></div>
  <div class="log-tools"><label>显示 <select v-model="logFilter" aria-label="筛选转发日志"><option value="all">全部状态</option><option value="failed">失败与重试</option><option value="sent">平台已接受</option><option value="skipped">规则已过滤</option></select></label><span>共 {{filteredLogs.length}} 条</span></div><div v-for="l in visibleLogs" :key="l.id" class="audit-row"><div><strong>{{l.name}} · {{states[l.state]||l.state}}</strong><small>{{l.source_kind==='call'?'来电事件':'短信'}} #{{l.message_id}} · 已尝试 {{l.attempts}} 次 · {{reasons[l.result]||l.result||'待处理'}}</small><small v-if="l.state==='retry'">下次重试 {{time(l.next_at)}}</small></div><button v-if="l.state==='failed'" class="text-button" @click="retry(l.id)">重新发送</button><time>{{time(l.created_at)}}</time></div>
 <div class="log-tools"><button class="button" :disabled="logPage<=1" @click="logPage--">上一页</button><span>{{logPage}} / {{logPages}}</span><button class="button" :disabled="logPage>=logPages" @click="logPage++">下一页</button></div></section>
</template>
<style scoped>
.forwarding{margin-bottom:20px}.hint{font-size:13px;color:#64748b;padding:12px 22px;line-height:1.7}.warning{color:#b42318;padding:12px 22px}form{padding:0 22px 24px;max-width:760px}form>label{display:flex;flex-direction:column;gap:8px;margin:16px 0;font-size:14px}input,select,textarea{width:100%;box-sizing:border-box;border:1px solid #dbe2ea;border-radius:8px;padding:10px;background:white;color:#1e293b;font:inherit}textarea{resize:vertical}.check{display:flex!important;flex-direction:row!important;align-items:flex-start;gap:10px}.check input{width:16px;flex-shrink:0;margin-top:3px}fieldset{border:1px solid #dbe2ea;border-radius:8px;padding:12px}fieldset label{margin:8px 0}form .hint{padding:0}.audit-row{gap:16px}.audit-row>div{flex:1;min-width:0}.audit-row small{overflow-wrap:anywhere}.audit-row time{font-size:12px}.simple-panel-title{gap:12px}
.channel-modal{position:fixed;inset:0;margin:auto;width:min(680px,calc(100vw - 28px));max-height:calc(100dvh - 32px);padding:0;border:1px solid #e3e6eb;border-radius:16px;background:#fff;color:#28303c;box-shadow:0 24px 80px #141b3030;overflow:auto}.channel-modal::backdrop{background:#19213275;backdrop-filter:blur(3px)}.channel-modal .simple-panel-title{position:sticky;top:0;background:#fff;z-index:1;border-bottom:1px solid #e9edf4}.modal-footer{position:sticky;bottom:0;display:flex;justify-content:flex-end;gap:10px;background:#fff;padding:16px 22px;border-top:1px solid #e9edf4}.saved-field{font-size:11px;font-weight:400;color:#438365}.log-tools{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 22px;font-size:12px;color:#8a929f;border-top:1px solid #edf0f4}.log-tools select{width:auto;font-size:12px;padding:7px}.forwarding .audit-row{padding-top:16px;padding-bottom:16px}.forwarding .audit-row time{white-space:nowrap;font-size:11px}.channel-modal form{padding-bottom:8px}@media(max-width:600px){.forwarding .audit-row{flex-wrap:wrap}.forwarding .audit-row time{width:100%}.channel-modal{max-height:calc(100dvh - 16px);width:calc(100vw - 16px)}form>label{font-size:13px}}
.commands-settings{margin:20px 0;background:#f8fafc}.command-field{display:flex;flex-direction:column;gap:8px;margin:14px 0;font-size:13px}.commands-settings .hint{font-size:12px;margin:12px 0}.commands-settings textarea{background:white}
.existing-callback{padding:14px;border:1px solid #dce9e1;border-radius:8px;background:#f4faf6;font-size:12px;color:#4f6e5c}.existing-callback p{line-height:1.8;margin:8px 0}.existing-callback strong{font-weight:550}
</style>
