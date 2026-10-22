<script setup>
import {ref,onMounted,onUnmounted} from 'vue'
import {ask} from './ui'
const props=defineProps({api:Function,id:String})
const items=ref([]),name=ref('默认接入'),secret=ref(''),error=ref(''),busy=ref(false)
const endpoint=window.location.origin+'/api/ingest/events'
async function load(){items.value=(await props.api('/numbers/'+props.id+'/ingress-tokens')).items}
async function create(){busy.value=true;error.value='';secret.value='';try{const d=await props.api('/numbers/'+props.id+'/ingress-tokens',{method:'POST',body:JSON.stringify({name:name.value})});secret.value=d.token;await load()}catch(e){error.value=e.message}finally{busy.value=false}}
async function revoke(t){if(!await ask('撤销此密钥？使用它的提交程序将立即失去接入权限。'))return;busy.value=true;try{await props.api('/numbers/'+props.id+'/ingress-tokens/'+t.id,{method:'DELETE'});await load()}catch(e){error.value=e.message}finally{busy.value=false}}
function date(v){return v?new Date(v*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未使用'}
onMounted(()=>load().catch(e=>error.value=e.message))
onUnmounted(()=>secret.value='')
</script>
<template>
 <section class="ingress-panel"><hr><h3>HTTP 接入密钥</h3><p>密钥仅允许向当前来源提交事件，不能读取消息。关闭窗口后不会再次显示明文。</p>
 <form @submit.prevent="create"><label>密钥名称<input v-model="name" required maxlength="80" autocomplete="off"></label><button class="button primary" :disabled="busy||!name.trim()">生成接入密钥</button></form>
 <p v-if="error" role="alert" class="error-banner">{{error}}</p>
 <div v-if="secret" class="secret-box"><strong>请现在保存，仅显示一次</strong><textarea :value="secret" readonly rows="3" aria-label="新接入密钥" spellcheck="false"></textarea><button class="text-button" @click="secret=''">隐藏密钥</button></div>
 <details><summary>提交方式</summary><p>POST {{endpoint}}</p><p>请求头：Authorization: Bearer &lt;接入密钥&gt;<br>Content-Type: application/json</p><pre>{
  "event_id": "unique-event-001",
  "kind": "message",
  "sender": "My service",
  "body": "一条授权消息",
  "received_at": "2026-10-22T12:00:00+08:00"
}</pre><p>同一密钥的 event_id 必须唯一。相同内容重试不会重复入库，不同内容复用同一 ID 返回 409。生产环境使用 HTTPS。</p></details>
 <div v-for="t in items" :key="t.id" class="token-row"><div><strong>{{t.name}}</strong><small>{{t.enabled?'有效':'已撤销'}} · 创建 {{date(t.created_at)}}<br>最近使用 {{date(t.last_used)}}</small></div><button v-if="t.enabled" class="text-button" :disabled="busy" @click="revoke(t)">撤销</button></div><p v-if="!items.length">暂无接入密钥</p>
 </section>
</template>
<style scoped>
.ingress-panel p,.ingress-panel small{font-size:12px;color:#758194;line-height:1.8;overflow-wrap:anywhere}.ingress-panel details{margin:20px 0}.ingress-panel pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f7fa;padding:14px;border-radius:9px;font-size:12px}.token-row{display:flex;justify-content:space-between;gap:12px;border-top:1px solid #edf0f4;padding:14px 0}.token-row strong,.token-row small{display:block}.secret-box{background:#fff9e9;padding:16px;margin:18px 0;border-radius:10px}.secret-box textarea{width:100%;margin:10px 0;font:12px monospace;padding:10px;border:1px solid #e6d7ac;border-radius:6px;box-sizing:border-box}.secret-box strong{font-size:13px}
</style>
