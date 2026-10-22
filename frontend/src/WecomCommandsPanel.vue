<script setup>
import {ref,onMounted,onUnmounted} from 'vue'
import {ask} from './ui'
const props=defineProps({api:Function})
const state=ref({available:false,bound:false}),code=ref(''),expiry=ref(0),busy=ref(false),error=ref('')
let timer
async function load(){try{state.value=await props.api('/wecom/binding');if(state.value.bound)code.value=''}catch(e){error.value=e.message}}
async function generate(){busy.value=true;error.value='';try{const r=await props.api('/wecom/binding-code',{method:'POST'});code.value=r.code;expiry.value=r.expires_at}catch(e){error.value=e.message}finally{busy.value=false}}
async function unbind(){if(!await ask('解除企业微信绑定后，该成员将无法查询本账号的消息。确定解除？'))return;busy.value=true;try{await props.api('/wecom/binding',{method:'DELETE'});code.value='';await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function copy(){try{await navigator.clipboard.writeText('绑定 '+code.value)}catch{error.value='请手动复制上方绑定指令'}}
onMounted(()=>{load();timer=setInterval(load,10000)});onUnmounted(()=>{clearInterval(timer);code.value=''})
</script>
<template><section class="simple-panel command-panel"><header class="simple-panel-title"><h3>企业微信指令</h3><span class="command-badge">{{state.bound?'已绑定':'未绑定'}}</span></header><div class="command-body"><p>在企业微信应用会话中查询本账号的号码、短信和来电。查询使用北京时间，验证码正文隐藏。</p><p v-if="!state.available" class="command-muted">指令服务尚未启用。</p><template v-else-if="state.bound"><p>绑定成员：<strong>{{state.member}}</strong></p><button class="button" :disabled="busy" @click="unbind">解除绑定</button></template><template v-else><button class="button primary" :disabled="busy" @click="generate">{{busy?'正在生成…':code?'重新生成绑定码':'生成绑定码'}}</button><div v-if="code" class="binding-code"><span>复制下方指令，发送给当前企业微信应用：</span><code>绑定 {{code}}</code><small>有效期 10 分钟，一次使用；请勿转发给他人。</small><button class="button" @click="copy">复制绑定指令</button></div></template><p v-if="error" class="command-error" role="alert">{{error}}</p><details><summary>可用指令</summary><dl><dt>帮助</dt><dd>查看指令说明</dd><dt>号码列表</dt><dd>本人号码、备注及接收器存活状态</dd><dt>状态 8475</dt><dd>号码状态与上次托管查询结果</dd><dt>短信 8475</dt><dd>最近 5 条短信（敏感正文隐藏）</dd><dt>来电 8475</dt><dd>最近 5 通已采集来电</dd></dl><p>将 8475 换成你的号码尾号；重号时使用完整号码。状态查询不会切换托管或重连接收。</p></details></div></section></template>
<style scoped>
.command-panel{margin:0 0 22px}.command-body{padding:18px 22px;font-size:13px;color:#7b8491;line-height:1.9}.command-body p{margin:0 0 14px}.command-body details{margin-top:18px;border-top:1px solid #e9edf3;padding-top:14px}.command-body summary{cursor:pointer;color:#404b5b}.command-body dl{display:grid;grid-template-columns:110px 1fr;gap:7px;font-size:12px;margin:16px 0}.command-body dd{margin:0}.command-body dt{color:#3d4859}.command-badge{font-size:11px;color:#55806a;background:#eff7f1;padding:4px 9px;border-radius:20px}.binding-code{display:grid;gap:10px;background:#f7f8fb;border:1px solid #e6eaf0;padding:16px;border-radius:10px;margin-top:16px}.binding-code code{font-size:13px;color:#343e4d;overflow-wrap:anywhere;user-select:all}.binding-code button{justify-self:start}.command-error{color:#b94b48}.command-muted{color:#999}
</style>
