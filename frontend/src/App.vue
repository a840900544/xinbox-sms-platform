<script setup>
function displayPhone(v){return String(v||'').replace(/^\+86(?=1\d{10}$)/,'')}

import {ask} from './ui'
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import Icon from './Icon.vue'
import ForwardingPanel from './ForwardingPanel.vue'
import AdminPanel from './AdminPanel.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import CredentialsPanel from './CredentialsPanel.vue'
import PlatformSettings from './PlatformSettings.vue'
const passwordOpen=ref(false)
const role=ref('user'),adminMode=window.location.pathname.replace(/\/$/,'')==='/admin'
const isAdmin=computed(()=>role.value==='admin')

const ready = ref(false), user = ref(null), csrf = ref(''), busy = ref(false), loading = ref(false)
const error = ref(''), loginError = ref(''), toast = ref(''), mobileNav = ref(false)
const credentials = ref({ username: '', password: '' }),registerMode=ref(false),registerConfirm=ref('')
const page = ref(adminMode?'users':'inbox'), status = ref('all'), query = ref(''), number = ref(''), start = ref(''), end = ref('')
const pageIndex = ref(1), selected = ref([]), items = ref([]), total = ref(0), searchLimited = ref(false)
const summary = ref({ total: 0, unread: 0, archived: 0, accounts: 0, today: 0, numbers: [], source: {} })
const detail = ref(null), detailBusy = ref(false), revealBusy = ref(false), audit = ref([])
const pass = ref({ current_password: '', new_password: '', confirm: '' }), passBusy = ref(false)
let timer, searchTimer, noticeTimer, sequence = 0
const baseNav = [{ id: 'inbox', label: '短信收件箱', icon: 'inbox' }, { id: 'calls', label: '来电记录', icon: 'bell' }, { id: 'numbers', label: '号码管理', icon: 'phone' }, { id: 'forwarding', label: '消息转发', icon: 'mail' }, { id: 'settings', label: '安全设置', icon: 'settings' }]
const nav=computed(()=>adminMode&&isAdmin.value?[{id:'users',label:'平台总览与用户',icon:'user'},...baseNav.map(n=>({...n,label:n.id==='inbox'?'短信查询':n.id==='calls'?'来电查询':n.id==='forwarding'?'转发管理':n.id==='settings'?'管理员设置':n.label})),{id:'platform',label:'注册与公告',icon:'settings'},{id:'recycle',label:'回收站',icon:'archive'}]:[...baseNav,{id:'announcements',label:'站内公告',icon:'bell'}])
const registrationOpen=ref(false),announcements=ref([]),popup=ref(null),popupQueue=ref([])
async function loadAnnouncements(login=false){if(!user.value||adminMode)return;try{announcements.value=(await api('/announcements')).items;popupQueue.value=popupQueue.value.filter(a=>announcements.value.some(v=>v.id===a.id&&v.revision===a.revision));if(popup.value){popup.value=announcements.value.find(a=>a.id===popup.value.id)||popupQueue.value.shift()||null}if(login){popupQueue.value=announcements.value.filter(a=>a.popup&&(!a.once_only||!a.is_read));popup.value=popupQueue.value.shift()||null}}catch{}}
async function dismissAnnouncement(){if(!popup.value)return;try{await api('/announcements/'+popup.value.id+'/read',{method:'POST'});popup.value=popupQueue.value.shift()||null}catch(e){notice(e.message)}}
const captchaOpen = ref(false), captchaNonce = ref(''), captchaFrame = ref(null)
function openLoginCaptcha() {
 if (operationBusy.value) return
 captchaNonce.value = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,'0')).join('')
 captchaOpen.value = true
}
function captchaMessage(event) {
 if (!captchaOpen.value || !editing.value || event.source !== captchaFrame.value?.contentWindow || event.origin !== 'null') return
 const data = event.data
 if (data?.type !== 'jego-captcha-result' || data.nonce !== captchaNonce.value || typeof data.value !== 'string' || data.value.length > 32768) return
 sessionInput.value.captcha_token = data.value; captchaOpen.value = false; numberAction('send_code')
}
onMounted(() => window.addEventListener('message',captchaMessage))
onUnmounted(() => window.removeEventListener('message',captchaMessage))
const managed = ref([]), calls = ref([]), callTotal = ref(0), callPage = ref(1), callScope = ref('')
const newNumber = ref({ mobile: '', country_code: '86', label: '', authorized: false })
const adding = ref(false), editing = ref(null), operationBusy = ref(false), operationResult = ref('')
const hostingDeviceInput = ref('')
const sessionInput = ref({ token: '', captcha_token: '', code: '' })
watch(editing, value => { if (!value) { hostingDeviceInput.value = ''; sessionInput.value = { token: '', captcha_token: '', code: '' }; captchaOpen.value = false } })
async function addNumber() {
 operationBusy.value = true
 try { await api('/numbers', { method: 'POST', body: JSON.stringify(newNumber.value) }); adding.value = false; newNumber.value = { mobile: '', country_code: '86', label: '', authorized: false }; await refresh(); notice('号码已添加，完成登录接入后才能接收') }
 catch(e) { notice(e.message) } finally { operationBusy.value = false }
}
function editNumber(n) { hostingDeviceInput.value = ''; editing.value = { ...n }; sessionInput.value = { token: '', captcha_token: '', code: '' }; operationResult.value = '' }
async function deleteNumber(){
 if(operationBusy.value||!editing.value)return
 if(!await ask(`删除 ${editing.value.number}？将停止本系统接收并移入回收站，历史记录保留；无忧行托管开关不会改变。`))return
 operationBusy.value=true
 try{const result=await api('/numbers/'+editing.value.id,{method:'DELETE'});editing.value=null;await refresh();notice(result.message)}catch(e){notice(e.message)}finally{operationBusy.value=false}
}
async function saveNumber() {
 operationBusy.value = true
 try { await api('/numbers/' + editing.value.id, { method: 'PATCH', body: JSON.stringify({ label: editing.value.label, archived: editing.value.archived }) }); await refresh(); notice('号码资料已保存') }
 catch(e) { notice(e.message) } finally { operationBusy.value = false }
}
async function numberAction(action) {
 if (operationBusy.value) return
 const ident = editing.value.id
 operationBusy.value = true; operationResult.value = '正在执行…'
 try {
  const job = await api('/numbers/' + ident + '/actions', { method: 'POST', body: JSON.stringify({ action, ...sessionInput.value, ...(action === 'save_device_id' ? { device_id: hostingDeviceInput.value.trim() } : {}) }) })
  if (action === 'save_device_id') hostingDeviceInput.value = ''
  else { sessionInput.value.token = ''; sessionInput.value.code = ''; sessionInput.value.captcha_token = '' }
  let done = false
  for (let i=0;i<60;i++) {
   await new Promise(resolve => setTimeout(resolve, 1500))
   const result = await api('/jobs/' + job.job_id)
   if (result.state === 'done' || result.state === 'failed') {
    operationResult.value = (result.result?.message || '操作已结束') + (result.result?.code ? ' · 业务码 ' + result.result.code : '')
    await refresh(); const current = managed.value.find(n => n.id === ident); if (current && editing.value?.id === ident) editing.value = { ...current }
    done = true; break
   }
  }
  if (!done) operationResult.value = '操作仍在处理，请稍后刷新状态，勿重复提交。'
 } catch(e) { operationResult.value = e.message } finally { operationBusy.value = false }
}
async function loadCalls() { const result = await api('/calls?' + new URLSearchParams({ account: number.value, page: callPage.value })); calls.value = result.items; callTotal.value = result.total; callScope.value = result.scope }
watch(callPage, () => { if (user.value && page.value === 'calls') loadCalls().catch(e => notice(e.message)) })
const pageName = computed(() => nav.value.find(n => n.id === page.value)?.label || '短信收件箱')
const allSelected = computed(() => items.value.length > 0 && items.value.every(x => selected.value.includes(x.id)))
const pages = computed(() => Math.max(1, Math.ceil(total.value / 20)))
const sourceHealthy = computed(() => summary.value.source?.state === 'connected')
const dateFormat = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
function formatTime(value) { return value ? dateFormat.format(new Date(typeof value === 'number' ? value * 1000 : value)) : '—' }
function fullTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—' }
function notice(text) { toast.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => toast.value = '', 3200) }
function maskedNumber(value) { return displayPhone(value)?.replace(/(\d{3})\d{4}(\d{4})$/, '$1 •••• $2') || '—' }

async function api(path, options = {}) {
 const headers = { ...options.headers, ...(adminMode?{'X-Console-Scope':'admin'}:{}) }
 if (options.body) headers['Content-Type'] = 'application/json'
 if (options.method && options.method !== 'GET' && csrf.value) headers['X-CSRF-Token'] = csrf.value
 const response = await fetch('/api' + path, { ...options, headers, credentials: 'same-origin', cache: 'no-store' })
 let data; try { data = await response.json() } catch { data = {} }
 if (!response.ok) {
  if (response.status === 401 && !path.endsWith('/login')) { user.value = null; csrf.value = ''; detail.value = null; items.value = []; editing.value = null; managed.value = []; calls.value = []; pass.value = { current_password: '', new_password: '', confirm: '' } }
  throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请稍后重试')
 }
 return data
}
async function loadMessages(quiet = false) {
 if (!user.value) return
 const current = ++sequence
 if (!quiet) loading.value = true
 try {
  const params = new URLSearchParams({ q: query.value, account: number.value, status: status.value, start: start.value, end: end.value, page: pageIndex.value, page_size: 20 })
  const result = await api('/messages?' + params)
  if (current !== sequence) return
  items.value = result.items; total.value = result.total; searchLimited.value = result.search_limited
  selected.value = selected.value.filter(id => items.value.some(m => m.id === id))
  error.value = ''
 } catch (e) { error.value = e.message }
 finally { if (current === sequence) loading.value = false }
}
async function refresh(quiet = false) {
 if (!user.value) return
 try {
  summary.value = await api('/overview')
  if (page.value === 'inbox') await loadMessages(quiet)
  if (page.value === 'audit') audit.value = (await api('/audit')).items
  if (page.value === 'numbers') managed.value = (await api('/numbers')).items
  if (page.value === 'calls') await loadCalls()
 } catch (e) { error.value = e.message }
}
async function signIn() {
 if(registerMode.value){
  if(credentials.value.password!==registerConfirm.value){loginError.value='两次密码不一致';return}
  busy.value=true;loginError.value=''
  try{await api('/auth/register',{method:'POST',body:JSON.stringify(credentials.value)});registerMode.value=false;registerConfirm.value='';credentials.value.password='';notice('注册成功，请登录')}catch(e){loginError.value=e.message}finally{busy.value=false}
  return
 }
 busy.value = true; loginError.value = ''
 try { const result = await api('/auth/login', { method: 'POST', body: JSON.stringify(credentials.value) }); user.value = result.username; role.value=result.role||'user'; if(adminMode&&role.value!=='admin'){user.value=null;throw new Error('该账号不是管理员，请从用户端登录')} csrf.value = result.csrf; credentials.value.password = ''; await refresh();await loadAnnouncements(true) }
 catch (e) { loginError.value = e.message }
 finally { busy.value = false }
}
async function logout() {
 try { await api('/auth/logout', { method: 'POST' }) } catch (e) { if (user.value) { error.value = e.message; return } }
 user.value = null; csrf.value = ''; detail.value = null; items.value = []; selected.value = []; summary.value.numbers = []; pass.value = { current_password: '', new_password: '', confirm: '' }
 editing.value = null; adding.value = false; captchaOpen.value = false; managed.value = []; calls.value = []; sessionInput.value = { token: '', captcha_token: '', code: '' }
}
async function manualSync() {
 busy.value = true
 try { const result = await api('/sync', { method: 'POST' }); await refresh(); notice(result.error || (result.imported ? `已同步 ${result.imported} 条新短信` : '已是最新数据')) }
 catch (e) { notice(e.message) }
 finally { busy.value = false }
}
function toggleAll() { selected.value = allSelected.value ? [] : items.value.map(x => x.id) }
async function batch(action, ids = selected.value) {
 if (!ids.length) return
 try { await api('/messages/batch', { method: 'POST', body: JSON.stringify({ ids, action }) }); selected.value = []; if (detail.value && ids.includes(detail.value.id)) { if (action === 'archive' || action === 'restore') detail.value = null; else detail.value.is_read = action === 'read' } await refresh(true); notice(({ read: '已标记为已读', unread: '已标记为未读', archive: '已归档', restore: '已移回收件箱' })[action]) }
 catch (e) { notice(e.message) }
}
async function openMessage(message) {
 detailBusy.value = true; detail.value = { ...message }
 try { detail.value = await api('/messages/' + message.id); if (!message.is_read) { await api('/messages/batch', { method: 'POST', body: JSON.stringify({ ids: [message.id], action: 'read' }) }); detail.value.is_read = true; await refresh(true) } }
 catch (e) { notice(e.message) }
 finally { detailBusy.value = false }
}
async function reveal() {
 revealBusy.value = true
 try { detail.value = await api('/messages/' + detail.value.id + '/reveal', { method: 'POST' }); notice('原文已显示，本次查看已记录') }
 catch (e) { notice(e.message) }
 finally { revealBusy.value = false }
}
async function copyBody() {
 try { await navigator.clipboard.writeText(detail.value.body); notice(detail.value.redacted ? '已复制脱敏内容' : '已复制短信内容') }
 catch { notice('浏览器未允许复制，请手动选择短信内容') }
}
async function changePassword() {
 if (pass.value.new_password !== pass.value.confirm) { notice('两次新密码不一致'); return }
 if (pass.value.new_password.length < 6) { notice('新密码至少 6 位'); return }
 passBusy.value = true
 try { await api('/auth/password', { method: 'POST', body: JSON.stringify({ current_password: pass.value.current_password, new_password: pass.value.new_password }) }); user.value = null; detail.value = null; csrf.value = ''; pass.value = { current_password: '', new_password: '', confirm: '' }; passwordOpen.value=false;notice('密码已更新，请重新登录') }
 catch (e) { notice(e.message) }
 finally { passBusy.value = false }
}
function switchPage(value) { if(!nav.value.some(n=>n.id===value))return; if(value==='announcements')loadAnnouncements();page.value = value; mobileNav.value = false; detail.value = null; selected.value = []; refresh(true) }
function resetFilters() { query.value = ''; number.value = ''; start.value = ''; end.value = ''; status.value = 'all'; pageIndex.value = 1 }
function keyboard(event) { if (event.key === 'Escape') { detail.value = null; if (!operationBusy.value) { editing.value = null; adding.value = false }; mobileNav.value = false } }
watch([status, number, start, end], () => { pageIndex.value = 1; selected.value = []; loadMessages() })
watch(query, () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { pageIndex.value = 1; loadMessages() }, 350) })
watch(pageIndex, () => loadMessages())
onMounted(async () => {
 try {registrationOpen.value=(await api('/site')).registration_open} catch {}
 try { const result = await api('/auth/me'); user.value = result.username; role.value=result.role||'user'; if(adminMode&&role.value!=='admin'){user.value=null;throw new Error('该账号不是管理员，请从用户端登录')} csrf.value = result.csrf; await refresh();await loadAnnouncements(true) } catch {} finally { ready.value = true }
 timer = setInterval(() => { if (user.value && !document.hidden) {refresh(true);if(page.value==='announcements'||popup.value||popupQueue.value.length)loadAnnouncements()} }, 10000)
 window.addEventListener('keydown', keyboard)
})
onUnmounted(() => { clearInterval(timer); clearTimeout(searchTimer); clearTimeout(noticeTimer); window.removeEventListener('keydown', keyboard) })
const actionNames = {user_register:'注册账号', login: '登录管理后台', reveal: '查看短信原文', read: '标记已读', unread: '标记未读', archive: '归档短信', restore: '恢复短信', password_change: '修改登录密码', number_add: '添加号码', number_edit: '修改号码资料', number_activate: '验证号码会话', number_status: '查询托管状态', number_enable_hosting: '开启号码托管', number_start: '启动号码接收', number_stop: '停止号码接收', number_restart: '重连号码接收', number_send_code: '发送登录验证码', number_login: '提交号码登录', number_save_device_id: '修改托管设备标识', forward_channel_updated: '修改转发渠道', forward_manual_retry: '手动重发消息', number_delete:'删除号码', number_restore:'恢复号码', number_assign:'分配号码', user_create:'创建用户',user_status_change:'修改用户状态' }
</script>

<template>
 <ConfirmDialog/>
 <div v-if="!ready" class="boot"><span class="brand-mark"><Icon name="mail" :size="26" /></span><p>正在打开信匣…</p></div>
 <main v-else-if="!user" class="login-shell">
  <section class="login-story">
   <a class="brand light"><span class="brand-mark"><Icon name="mail" :size="25" /></span><span>信匣<small>{{adminMode?'ADMIN CONSOLE':'SMS CONSOLE'}}</small></span></a>
   <div class="story-main"><span class="eyebrow">让每一条消息，都有归处</span><h1>你的短信，<br>都在这里。</h1><p>在一个清晰、安全的空间里，<br>查看授权号码收到的每一条短信。</p><div class="story-features"><span><Icon name="check" :size="16" /> 统一收件箱</span><span><Icon name="check" :size="16" /> 敏感信息保护</span><span><Icon name="check" :size="16" /> 随时查找</span></div></div>
   <div class="story-visual" aria-hidden="true"><div class="visual-card"><div class="visual-top"><span class="mini-mail"><Icon name="mail" /></span><span>所有消息，井然有序<small>收件箱 · 已同步</small></span><span class="visual-check"><Icon name="check" :size="16" /></span></div><i></i><i></i><i></i></div><div class="visual-shield"><Icon name="shield" :size="25" />加密存储 · 按需查看</div></div>
   <footer>仅管理本人持有或明确授权的号码</footer>
  </section>
  <section class="login-form-panel"><form class="login-form" @submit.prevent="signIn"><span class="welcome-icon"><Icon name="user" :size="25" /></span><h2>{{registerMode?'创建账号':adminMode?'管理后台':'欢迎回来'}}</h2><p>{{registerMode?'注册后即可添加你获授权管理的号码':'登录你的短信管理空间'}}</p><label for="username">{{adminMode?'管理员账号':'登录账号'}}</label><div class="input-icon"><Icon name="user" :size="18" /><input id="username" v-model="credentials.username" autocomplete="username" maxlength="64" required></div><label for="password">登录密码</label><div class="input-icon"><Icon name="lock" :size="18" /><input id="password" v-model="credentials.password" type="password" :autocomplete="registerMode?'new-password':'current-password'" :minlength="registerMode?6:1" placeholder="请输入登录密码" maxlength="128" required></div><template v-if="registerMode"><label for="register-confirm">确认密码</label><div class="input-icon"><Icon name="lock" :size="18"/><input id="register-confirm" v-model="registerConfirm" type="password" minlength="6" maxlength="128" required autocomplete="new-password"></div></template><div v-if="loginError" role="alert" class="form-error">{{ loginError }}</div><button class="button primary login-button" :disabled="busy">{{ busy ? '正在处理…' : registerMode?'注册账号':adminMode?'登录管理后台':'登录用户端' }}<Icon name="arrow" :size="18" /></button><button v-if="!adminMode && registrationOpen" class="text-button register-toggle" type="button" :disabled="busy" @click="registerMode=!registerMode;loginError='';credentials.password='';registerConfirm=''">{{registerMode?'已有账号？返回登录':'没有账号？立即注册'}}</button><p class="login-foot"><Icon name="shield" :size="15" /> 登录后才能查看短信内容</p></form><div class="login-bottom">信匣 · 私有短信管理</div></section>
 </main>
 <div v-else class="app-shell">
  <div v-if="mobileNav" class="nav-scrim" @click="mobileNav = false"></div>
  <aside class="sidebar" :class="{ open: mobileNav }"><a class="brand" @click="switchPage(adminMode?'users':'inbox')"><span class="brand-mark"><Icon name="mail" :size="24" /></span><span>信匣<small>{{adminMode?'ADMIN CONSOLE':'SMS CONSOLE'}}</small></span></a><div class="workspace-label">{{adminMode?'管理后台':'工作空间'}} <span>{{adminMode?'管理':'个人'}}</span></div><nav><button v-for="n in nav" :key="n.id" :class="{ active: page === n.id }" @click="switchPage(n.id)"><Icon :name="n.icon" :size="19" /><span>{{ n.label }}</span><b v-if="n.id === 'inbox' && summary.unread">{{ summary.unread }}</b></button></nav><div class="sidebar-source"><span class="source-icon"><Icon name="shield" :size="20" /></span><strong>{{adminMode?'平台管理中心':'你的私有收件箱'}}</strong><p>{{adminMode?'统一管理用户、号码与公告':'短信加密保存'}}<br>{{adminMode?'当前处于管理后台':'敏感内容默认脱敏'}}</p></div><div class="sidebar-user"><span class="avatar">A</span><span><strong>{{ user }}</strong><small>{{adminMode?'平台管理员':isAdmin?'管理员':'用户'}}</small></span><button class="icon-button" title="退出登录" aria-label="退出登录" @click="logout"><Icon name="logout" :size="18" /></button></div></aside>
  <div class="main-shell"><header class="topbar"><div class="topbar-navigation"><button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileNav = true"><Icon name="menu" /></button></div><div class="topbar-right"><a v-if="isAdmin && !adminMode" class="text-button" href="/admin">管理后台</a><span class="avatar small">A</span></div></header>
   <main class="content"><section class="page-header"><div><span class="eyebrow">{{adminMode?'PLATFORM ADMINISTRATION':'YOUR MESSAGES, ORGANIZED'}}</span><h1>{{ pageName }}</h1><p>{{ page === 'platform' ? '控制公开注册，发布和维护用户公告。' : page === 'announcements' ? '查看平台发布的最新通知。' : page === 'users' ? '管理平台用户、号码归属和回收站。' : page === 'inbox' ? '集中查看每一条短信，让重要消息不再散落。' : page === 'numbers' ? '添加和管理授权号码，查看托管与接收状态。' : page === 'calls' ? '查看采集器实际记录的来电与未接事件。' : page === 'forwarding' ? '配置转发平台、筛选规则与敏感信息权限。' : page === 'audit' ? '查看管理员操作，敏感内容访问有迹可循。' : '管理登录凭据，了解短信内容的保护方式。' }}</p></div><button v-if="page === 'inbox' || page === 'numbers'" class="button sync-button" :disabled="busy" @click="manualSync"><Icon name="refresh" :size="16" :class="{ spin: busy }" />{{ busy ? '同步中' : '同步短信' }}</button></section>
    <div v-if="error" class="error-banner" role="alert"><Icon name="info" :size="18" />{{ error }}<button @click="refresh()">重试</button></div>
    <PlatformSettings v-if="page==='platform' && adminMode && isAdmin" :api="api"/><section v-if="page==='announcements'" class="simple-panel"><div v-for="a in announcements" :key="a.id" class="announcement-item"><h3>{{a.title}}</h3><p>{{a.body}}</p></div><div v-if="!announcements.length" class="empty-state"><p>暂无公告</p></div></section>
    <AdminPanel v-if="['users','recycle'].includes(page) && isAdmin && adminMode" :key="page" :section="page" :api="api" />
    <template v-if="page === 'inbox'">
     <section class="stats-grid"><article class="stat-card"><div><span>短信总数</span><strong>{{ summary.total.toLocaleString() }}</strong><small>包含已归档短信</small></div><span class="stat-icon blue"><Icon name="mail" :size="22" /></span></article><article class="stat-card"><div><span>未读短信</span><strong>{{ summary.unread.toLocaleString() }}<i v-if="summary.unread" class="unread-dot"></i></strong><small>待你查看的消息</small></div><span class="stat-icon amber"><Icon name="inbox" :size="22" /></span></article><article class="stat-card"><div><span>今日接收</span><strong>{{ summary.today.toLocaleString() }}</strong><small>按北京时间统计</small></div><span class="stat-icon mint"><Icon name="clock" :size="22" /></span></article><article class="stat-card"><div><span>已接入号码</span><strong>{{ summary.accounts }}<em>个</em></strong><small>已有短信记录的号码</small></div><span class="stat-icon violet"><Icon name="phone" :size="22" /></span></article></section>
     <section class="messages-panel"><div class="panel-heading"><div class="tabs"><button :class="{ active: status === 'all' || status === 'read' }" @click="status = 'all'">全部短信</button><button :class="{ active: status === 'unread' }" @click="status = 'unread'">未读<span v-if="summary.unread">{{ summary.unread }}</span></button><button :class="{ active: status === 'archived' }" @click="status = 'archived'">已归档<span v-if="summary.archived">{{ summary.archived }}</span></button></div><span class="auto-refresh"><i :class="{ connected: sourceHealthy }"></i>每 10 秒更新</span></div>
      <div class="filter-bar"><div class="search-field"><Icon name="search" :size="18" /><input v-model="query" aria-label="搜索短信" placeholder="搜索内容、发送方或接收号码"><button v-if="query" class="icon-button" aria-label="清除搜索" @click="query = ''"><Icon name="close" :size="15" /></button></div><select v-model="number" aria-label="筛选接收号码"><option value="">全部接收号码</option><option v-for="n in summary.numbers" :key="n.id" :value="n.id">{{ maskedNumber(n.number) }}</option></select><div class="date-filter"><input v-model="start" type="date" aria-label="开始日期"><span>—</span><input v-model="end" type="date" aria-label="结束日期"></div><button v-if="query || number || start || end" class="text-button" @click="resetFilters">重置</button></div>
      <div v-if="selected.length" class="bulk-bar"><span>已选择 <strong>{{ selected.length }}</strong> 条</span><button @click="batch('read')"><Icon name="check" :size="15" />标为已读</button><button v-if="status !== 'archived'" @click="batch('archive')"><Icon name="archive" :size="15" />归档</button><button v-else @click="batch('restore')">移回收件箱</button><button class="bulk-cancel" @click="selected = []">取消选择</button></div>
      <div v-if="loading && !items.length" class="empty-state"><span class="loading-ring"></span><h3>正在读取短信</h3><p>请稍候…</p></div>
      <div v-else-if="!items.length" class="empty-state"><span class="empty-icon"><Icon :name="query || number || start || end ? 'search' : 'inbox'" :size="32" /></span><h3>{{ query || number || start || end ? '没有找到匹配的短信' : status === 'unread' ? '未读消息已处理完毕' : status === 'archived' ? '还没有归档短信' : '收件箱正在等候第一条消息' }}</h3><p>{{ query || number || start || end ? '试试其他关键词，或清除筛选条件。' : '数据源收到新短信后，将自动同步到这里。' }}</p><button v-if="query || number || start || end" class="button" @click="resetFilters">清除筛选</button></div>
      <div v-else class="table-wrap" :class="{ updating: loading }"><table class="message-table"><thead><tr><th class="check-col"><input type="checkbox" aria-label="选择本页全部短信" :checked="allSelected" @change="toggleAll"></th><th>发送方</th><th>短信内容</th><th>接收号码</th><th>接收时间</th><th>状态</th><th class="row-arrow"></th></tr></thead><tbody><tr v-for="m in items" :key="m.id" :class="{ unread: !m.is_read, chosen: selected.includes(m.id) }" tabindex="0" role="button" :aria-label="`查看来自 ${m.sender} 的短信`" @click="openMessage(m)" @keydown.enter="openMessage(m)"><td class="check-col" @click.stop><input v-model="selected" type="checkbox" :value="m.id" :aria-label="`选择短信 ${m.id}`" @keydown.enter.stop></td><td><div class="sender-cell"><span class="sender-avatar" :class="{ otp: m.sensitive }"><Icon :name="m.sensitive ? 'shield' : 'mail'" :size="18" /></span><div><strong>{{ m.sender }}</strong><span>{{ m.source }}</span></div><i v-if="!m.is_read" class="unread-dot"></i></div></td><td class="body-col"><span v-if="m.sensitive" class="sensitive-label"><Icon name="lock" :size="11" />验证码已脱敏</span><p>{{ m.body }}</p></td><td class="recipient-col"><span class="number-chip"><Icon name="phone" :size="12" />{{ maskedNumber(m.recipient) }}</span></td><td class="time-cell">{{ formatTime(m.received_at) }}<small v-if="!m.source_time_parsed">接入时间</small></td><td><span class="read-state" :class="{ pending: !m.is_read }">{{ m.is_read ? '已读' : '未读' }}</span></td><td class="row-arrow"><Icon name="chevron" :size="16" /></td></tr></tbody></table></div>
      <div class="panel-footer"><span>{{ total ? `共 ${total} 条 · 第 ${pageIndex} / ${pages} 页` : '共 0 条短信' }}<small v-if="searchLimited"> · 搜索范围为最近 5,000 条</small></span><div class="pagination"><button :disabled="pageIndex <= 1 || loading" aria-label="上一页" @click="pageIndex--"><Icon name="chevron" :size="16" class="flip" /></button><span>{{ pageIndex }}</span><button :disabled="pageIndex >= pages || loading" aria-label="下一页" @click="pageIndex++"><Icon name="chevron" :size="16" /></button></div></div>
     </section><div class="privacy-note"><Icon name="shield" :size="15" /><span>验证码默认隐藏，仅授权用户可按需查看原文；查看操作会被记录。</span></div>
    </template>
    <template v-if="page === 'numbers'">
     <section class="source-summary"><span class="source-large"><Icon name="phone" :size="24" /></span><div><h3>号码与接收服务</h3><p>{{ summary.collector?.label || '正在取得连接状态' }} · 数据同步 {{ sourceHealthy ? '正常' : '异常' }}</p></div><button class="button primary" @click="adding = true">添加号码</button></section>
     <div class="info-banner"><Icon name="info" :size="19"/><p>添加号码后需验证登录会话。托管开关、SDK 连接和实际收信分别展示；归档号码不会停止接收。</p></div>
     <section class="number-grid"><article v-for="n in managed" :key="n.id" class="number-card" :class="{ 'archived-number': n.archived }"><div class="number-identity"><span class="stat-icon blue"><Icon name="phone" :size="21"/></span><span class="number-presence" :class="{online:n.receiver?.state==='online'}" :title="n.receiver?.label || '尚未取得状态'"><i></i>{{ n.receiver?.state==='online' ? '存活' : n.receiver?.state==='connecting' ? '连接中' : n.receiver?.state==='offline' ? '离线' : '未知' }}</span></div><h3>{{displayPhone(n.number)}}</h3><p class="number-note">{{ n.label || '未设置备注' }}<span v-if="n.archived"> · 已归档</span></p><div class="number-card-bottom"><CredentialsPanel :api="api" :id="n.id"/><button class="text-button" @click="editNumber(n)">管理号码</button><button class="text-button" @click="number = n.number_key; status = 'all'; switchPage('inbox')">查看短信</button></div></article><article v-if="!managed.length" class="empty-state"><h3>还没有管理号码</h3><p>添加本人或已获授权管理的号码。</p></article></section>
    </template>
    <template v-if="page === 'calls'"><section class="simple-panel"><div class="simple-panel-title"><h3>来电记录 · {{ callTotal }} 通</h3><button class="text-button" @click="loadCalls">刷新记录</button></div><div class="filter-bar"><select v-model="number" @change="callPage = 1; loadCalls()"><option value="">全部接收号码</option><option v-for="n in summary.numbers" :value="n.id" :key="n.id">{{displayPhone(n.number)}}</option></select></div><p class="call-scope">{{ callScope }}。仅记录号码与状态，不自动接听。</p><div v-if="!calls.length" class="empty-state"><Icon name="bell" :size="30"/><h3>暂无已采集的来电记录</h3><p>后续收到的来电会自动显示在这里。</p></div><div v-for="c in calls" :key="c.id" class="audit-row"><span class="audit-icon"><Icon name="bell" :size="18"/></span><div><strong>{{ c.sender || '来源未提供号码' }}</strong><small>{{displayPhone(c.account)}} · {{ ({ringing:'来电中',answered:'已接通',ended:'已结束',missed:'未接来电',ended_unconfirmed:'已结束 · 接通未知'})[c.status] || '状态待确认' }} · {{ c.event_count }} 个事件</small></div><time>{{ fullTime(c.started_at) }}<small v-if="c.ended_at">结束 {{ fullTime(c.ended_at) }}</small></time></div><div class="panel-footer"><span>第 {{ callPage }} 页</span><div class="pagination"><button :disabled="callPage <= 1" @click="callPage--">上一页</button><button :disabled="callPage * 30 >= callTotal" @click="callPage++">下一页</button></div></div></section></template>
    <ForwardingPanel v-if="page === 'forwarding'" :api="api" :numbers="summary.numbers" />
    <template v-if="false"><section class="simple-panel"><div class="simple-panel-title"><h3>最近操作</h3><span>最近 100 条 · 北京时间</span></div><div v-if="!audit.length" class="empty-state"><Icon name="clock" :size="30" /><h3>暂无操作记录</h3></div><div v-for="a in audit" :key="a.id" class="audit-row"><span class="audit-icon"><Icon :name="a.action === 'reveal' ? 'eye' : a.action === 'login' ? 'user' : 'file'" :size="18" /></span><div><strong>{{ actionNames[a.action] || a.action }}</strong><small>{{ a.username || '管理员' }}<span v-if="a.message_id"> · 短信 #{{ a.message_id }}</span></small></div><time>{{ formatTime(a.created_at) }}</time></div></section></template>
    <template v-if="page === 'settings'"><div class="settings-grid"><section class="simple-panel password-panel"><div class="section-icon"><Icon name="lock" :size="22" /></div><h3>修改登录密码</h3><p>使用至少 6 位的新密码。修改后，所有登录会话将失效。</p><button class="button primary" @click="passwordOpen=true">修改密码</button><Transition name="modal"><div v-if="passwordOpen" class="drawer-overlay password-overlay"><section class="message-drawer password-dialog" role="dialog" aria-modal="true" aria-label="修改密码"><header><h2>修改密码</h2><button class="text-button" :disabled="passBusy" @click="passwordOpen=false;pass={current_password:'',new_password:'',confirm:''}">关闭</button></header><div class="drawer-body"><form @submit.prevent="changePassword"><label for="old-password">当前密码</label><input id="old-password" v-model="pass.current_password" type="password" autocomplete="current-password" required maxlength="128"><label for="new-password">新密码</label><input id="new-password" v-model="pass.new_password" type="password" autocomplete="new-password" minlength="6" maxlength="128" required placeholder="至少 6 位"><label for="confirm-password">再次输入新密码</label><input id="confirm-password" v-model="pass.confirm" type="password" autocomplete="new-password" minlength="6" maxlength="128" required><button class="button primary" :disabled="passBusy">{{ passBusy ? '正在保存…' : '更新密码' }}</button></form></div></section></div></Transition></section><section class="simple-panel protection-panel"><div class="section-icon"><Icon name="shield" :size="22" /></div><h3>内容保护</h3><div class="protection-item"><Icon name="check" :size="17" /><div><strong>短信加密存储</strong><p>短信正文、发送方与接收号码保存在加密记录中。</p></div></div><div class="protection-item"><Icon name="check" :size="17" /><div><strong>验证码默认脱敏</strong><p>在详情中主动查看原文，系统记录查看操作。</p></div></div><div class="protection-item"><Icon name="check" :size="17" /><div><strong>会话自动到期</strong><p>登录会话最长 8 小时，可随时主动退出。</p></div></div><div class="protection-item"><Icon name="info" :size="17" /><div><strong>保留与转发</strong><p>短信不会自动删除；消息转发仅在你配置并启用渠道后执行。</p></div></div></section></div></template>
    <footer class="content-footer"><span>信匣 <i>·</i> 让消息有序，让隐私安心</span><span>最近同步 {{ formatTime(summary.source.last_sync) }}<i class="footer-dot" :class="{ healthy: sourceHealthy }"></i></span></footer>
   </main>
  </div>
  <Transition name="modal"><div v-if="popup && !adminMode" class="announcement-overlay"><section class="announcement-modal" role="dialog" aria-modal="true" aria-label="平台公告"><span class="eyebrow">平台公告</span><h2>{{popup.title}}</h2><p>{{popup.body}}</p><button class="button primary" @click="dismissAnnouncement">我知道了</button></section></div></Transition>
 <Transition name="modal"><div v-if="detail" class="drawer-overlay" @click.self="detail = null"><aside class="message-drawer" role="dialog" aria-modal="true" aria-labelledby="detail-heading"><header><div><span class="eyebrow">MESSAGE DETAILS</span><h2 id="detail-heading">短信详情</h2></div><button class="icon-button" aria-label="关闭详情" autofocus @click="detail = null"><Icon name="close" :size="21" /></button></header><div class="drawer-body"><div class="detail-sender"><span class="sender-avatar large" :class="{ otp: detail.sensitive }"><Icon :name="detail.sensitive ? 'shield' : 'mail'" :size="27" /></span><div><h3>{{ detail.sender }}</h3><p>{{ detail.source }} · {{ fullTime(detail.received_at) }}</p></div></div><dl class="message-meta"><div><dt>接收号码</dt><dd>{{displayPhone(detail.recipient)}}</dd></div><div><dt>同步状态</dt><dd><span class="synced"><Icon name="check" :size="13" />已同步并加密保存</span></dd></div><div><dt>接入时间</dt><dd>{{ fullTime(detail.ingested_at) }}</dd></div><div><dt>来源时间</dt><dd>{{ detail.source_time_parsed ? fullTime(detail.received_at) : detail.source_time_raw || '来源未提供' }}</dd></div><div><dt>投递类型</dt><dd>{{ detail.offline ? '离线消息投递' : '在线消息投递' }}</dd></div></dl><div class="body-heading"><h3>短信内容</h3><button class="text-button" @click="copyBody"><Icon name="copy" :size="15" />复制{{ detail.redacted ? '脱敏内容' : '内容' }}</button></div><div class="message-body" :class="{ redacted: detail.redacted }">{{ detail.body }}</div><div v-if="detail.redacted" class="sensitive-notice"><Icon name="shield" :size="19" /><div><strong>验证码等敏感内容已隐藏</strong><p>仅在需要时查看原文。本次查看将写入操作记录。</p><button class="button" :disabled="revealBusy || detailBusy" @click="reveal"><Icon name="eye" :size="16" />{{ revealBusy ? '正在读取…' : '查看原文' }}</button></div></div><div v-else-if="detail.sensitive" class="reveal-warning"><Icon name="eye" :size="16" />当前显示原文，关闭详情后重新隐藏。</div></div><footer><span>短信 #{{ detail.id }}</span><button class="button" @click="batch(detail.archived ? 'restore' : 'archive', [detail.id])"><Icon name="archive" :size="16" />{{ detail.archived ? '移回收件箱' : '归档短信' }}</button></footer></aside></div></Transition>
 </div>
 <Transition name="modal"><div v-if="adding || editing" class="drawer-overlay" @click.self="!operationBusy && (adding = false, editing = null)"><aside class="message-drawer" role="dialog" aria-modal="true" aria-label="号码管理"><header><h2>{{ adding ? '添加号码' : '管理号码' }}</h2><button class="icon-button" :disabled="operationBusy" @click="adding = false; editing = null"><Icon name="close"/></button></header><div class="drawer-body account-form">
  <form v-if="adding" @submit.prevent="addNumber"><label>手机号码<input v-model="newNumber.mobile" required inputmode="tel" maxlength="15"></label><label>备注名称<input v-model="newNumber.label" maxlength="80" placeholder="例如：工作号码"></label><label class="check-label"><input type="checkbox" v-model="newNumber.authorized" required>此号码为本人持有或已获明确管理授权</label><button class="button primary" :disabled="operationBusy">添加号码</button></form>
  <template v-else-if="editing"><h3>{{displayPhone(editing.number)}}</h3><CredentialsPanel :api="api" :id="editing.id"/><form @submit.prevent="saveNumber"><label>备注名称<input v-model="editing.label" maxlength="80"></label><label class="check-label"><input type="checkbox" v-model="editing.archived">归档号码资料</label><button class="button" :disabled="operationBusy">保存资料</button></form><button class="button number-delete" :disabled="operationBusy" @click="deleteNumber">删除号码</button><hr><h3>托管设备标识</h3><p>当前：{{ editing.hosting_device_hint || '尚未单独设置' }}</p><label>设备标识<input v-model="hostingDeviceInput" maxlength="4096" autocomplete="off" spellcheck="false" placeholder="设备标识，不限制 UUID、连字符或字符格式"></label><button class="button" :disabled="operationBusy || !hostingDeviceInput.trim()" @click="numberAction('save_device_id')">保存标识</button><hr><p>{{ editing.status_label }}</p><p v-if="editing.receiver">接收器：{{ editing.receiver.label }} · {{ formatTime(editing.receiver.heartbeat) }}</p><p v-if="editing.hosting">上次查询：托管 {{ String(editing.hosting.cs_forward) === '2' ? '开启' : '关闭' }}，验证状态 {{ editing.hosting.authentication_code }}</p><div class="account-actions"><button class="button" :disabled="operationBusy" @click="numberAction('status')">查询托管</button><button class="button" :disabled="operationBusy" @click="numberAction('enable_hosting')">开启托管</button><button class="button" :disabled="operationBusy" @click="numberAction('start')">启动接收</button><button class="button" :disabled="operationBusy" @click="numberAction('stop')">停止接收</button></div><p v-if="operationResult" class="operation-result" role="status">{{ operationResult }}</p>
  <details><summary>Token 登录</summary><label>登录 token<input v-model="sessionInput.token" type="password" autocomplete="off" spellcheck="false" placeholder="仅用于本次会话验证，提交后清空"></label><button class="button" :disabled="operationBusy || !sessionInput.token" @click="numberAction('activate')">接入会话</button></details>
  <details><summary>手机号验证码登录</summary><button class="button primary" :disabled="operationBusy" @click="openLoginCaptcha">发送验证码</button><label>短信验证码<input v-model="sessionInput.code" inputmode="numeric" autocomplete="one-time-code" maxlength="8"></label><button class="button" :disabled="operationBusy || !sessionInput.code" @click="numberAction('login')">提交登录</button></details>
  </template></div></aside></div></Transition>
 <Transition name="modal"><div v-if="captchaOpen" class="captcha-overlay"><section class="captcha-panel"><header><h3>请本人完成人机验证</h3><button class="button" @click="captchaOpen = false">关闭</button></header><p>使用无忧行页面中的验证配置。通过后将为当前号码发送登录验证码；加载失败请关闭重试。</p><iframe ref="captchaFrame" :key="captchaNonce" :src="'/login-captcha?nonce=' + captchaNonce" sandbox="allow-scripts" title="登录人机验证"></iframe></section></div></Transition>
 <Transition name="toast"><div v-if="toast" class="toast" role="status"><Icon name="info" :size="17" />{{ toast }}</div></Transition>
</template>


