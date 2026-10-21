<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../utils/api'

const props = defineProps<{ open: boolean; disabled: boolean }>()
const emit = defineEmits<{ connected: [account: string]; disconnected: []; busy: [value: boolean] }>()
const directory = ref('')
const qqPath = ref('')
const mode = ref('shell')
const managed = ref(false)
const running = ref(false)
const known = ref(false)
const inspected = ref(false)
const action = ref('')
const error = ref('')
const message = ref('')
const phase = ref('')
const loggedIn = ref(false)
const qrImage = ref('')
const expectedAccount = ref('')
const confirmStop = ref(false)
const install = ref<{ running: boolean; ok?: boolean; state?: string; error?: string; directory?: string; downloaded?: number; total?: number }>({running: false})
const installError = ref('')
const installCancelling = ref(false)
const installVersion = 'v4.18.38'
let installedDirectory = ''
let generation = 0
let disposed = false
let timer: ReturnType<typeof setTimeout> | undefined
const locked = computed(() => props.disabled || !!action.value || install.value.running)
const framework = computed(() => mode.value === 'framework')
const packageName = computed(() => framework.value ? 'NapCat Framework 原始发布包' : 'NapCat Shell 原始发布包')
const installState = computed(() => ({idle: '尚未下载', downloading: '正在下载发布包', extracting: '正在解压发布包', validating: '正在检查发布包', installing: '正在安装', done: '安装完成', completed: '安装完成', installed: '安装完成', cancelled: '安装已取消', canceled: '安装已取消', error: '安装失败', failed: '安装失败'} as Record<string, string>)[install.value.state || ''] || (install.value.running ? '正在准备安装' : '安装状态待检查'))
const installBytes = computed(() => `${((install.value.downloaded || 0) / 1048576).toFixed(1)} MiB${install.value.total ? ` / ${(install.value.total / 1048576).toFixed(1)} MiB` : ' · 总大小未知'}`)
const loginText = computed(() => loggedIn.value ? 'QQ 已登录，请核对预期账号后接入收藏。' : ({ waiting_qr: '等待登录二维码', qr_pending: '正在生成登录二维码', qr_ready: '请用手机 QQ 扫码登录', waiting_scan: '请用手机 QQ 扫码登录', scanned: '已扫码，请在手机上确认', expired: '二维码已过期，请刷新', qr_expired: '二维码已过期，请刷新', offline: 'QQ 已离线，请重新登录', loading: '正在准备登录' } as Record<string, string>)[phase.value] || '正在等待 QQ 登录')

// 仅展示经过认证字段脱敏的错误，不读取或回显后台 token。
function errorText(result: any, fallback: string) {
  return String(result?.error || fallback).replace(/Bearer\s+\S+/gi, 'Bearer [已隐藏]').replace(/((?:access[_-]?)?token["']?\s*[:=]\s*["']?)[^\s,"'&}]+/gi, '$1[已隐藏]').slice(0, 500)
}

// 清除敏感登录图，状态转换和关闭面板时都不保留旧二维码。
function clearLogin() { qrImage.value = ''; loggedIn.value = false; phase.value = '' }

// 取消后续轮询，已发请求由代次守卫丢弃。
function stopPolling() { if (timer) clearTimeout(timer); timer = undefined }

// 恢复安装进度，完成后检查目录，启动仍由用户明确点击。
async function pollInstall(current: number) {
  const result = await api('napcat_install_progress')
  if (current !== generation || !props.open) return
  if (!result || typeof result.running !== 'boolean') {
    installError.value = '无法读取安装进度，请重新检查安装状态。'
    return
  }
  install.value = result
  emit('busy', !!action.value || result.running)
  if (result.error) installError.value = errorText(result, 'NapCat 安装失败。')
  else if (!result.running && result.ok === false && !['idle', 'cancelled', 'canceled'].includes(result.state || '')) installError.value = 'NapCat 安装失败，请重新下载安装。'
  else if (result.state !== 'idle') installError.value = ''
  if (result.running) return
  installCancelling.value = false
  if (!result.ok || !result.directory || installedDirectory === result.directory) return
  if (result.mode === 'shell' || result.mode === 'framework') mode.value = result.mode
  installedDirectory = String(result.directory)
  directory.value = installedDirectory; qqPath.value = ''; inspected.value = false
  const checked = await api('napcat_runtime_inspect', directory.value, '', mode.value)
  if (current !== generation || !props.open) return
  inspected.value = checked?.ok === true
  if (!inspected.value) { installError.value = errorText(checked, '发布包已下载，但目录检查失败，请检查 QQ 安装情况后重试检查目录。'); return }
  directory.value = String(checked.directory || directory.value); qqPath.value = String(checked.qq_path || '')
  message.value = '安装并检查完成，请点击启动按钮继续。'
}

// 下载固定版本的原始发布包，不自动启动 QQ。
async function startInstall() {
  if (running.value || !known.value) return
  await perform('install', async current => {
    installError.value = ''; installedDirectory = ''; inspected.value = false
    const result = await api('napcat_install_start', mode.value, installVersion)
    if (disposed || current !== generation || !props.open) return
    if (!result?.ok) { installError.value = errorText(result, '无法开始下载 NapCat，请重试。'); return }
    install.value = {running: true, state: 'downloading', downloaded: 0, total: 0}
  })
}

// 取消只终止安装任务，保留已存在的运行目录。
async function cancelInstall() {
  if (!install.value.running || installCancelling.value || action.value) return
  installCancelling.value = true
  const current = generation
  const result = await api('napcat_install_cancel')
  if (disposed || current !== generation || !props.open) return
  if (!result?.ok) { installCancelling.value = false; installError.value = errorText(result, '取消安装失败，请重试。'); return }
  await poll(current)
}

// 读取进程与登录快照，仅允许 PNG 的内联 base64 图片。
async function poll(current = generation) {
  stopPolling()
  await pollInstall(current)
  if (current !== generation || !props.open) return
  const result = await api('napcat_runtime_status')
  if (current !== generation || !props.open) return
  const restoreMode = (!known.value || result?.running === true) && !install.value.running
  known.value = result?.ok === true
  managed.value = result?.managed === true
  running.value = result?.running === true
  if (!known.value) { clearLogin(); error.value = errorText(result, '无法检查 NapCat 后台状态。'); return }
  const reportedMode = running.value ? result.mode : result.preferred_mode || result.mode
  if (restoreMode && (reportedMode === 'shell' || reportedMode === 'framework') && mode.value !== reportedMode) {
    mode.value = reportedMode; inspected.value = false; confirmStop.value = false
  }
  if (!running.value) confirmStop.value = false
  if (result.directory && !directory.value) directory.value = String(result.directory)
  if (managed.value && result.qq_path && !qqPath.value) qqPath.value = String(result.qq_path)
  if (running.value && managed.value) {
    const login = await api('napcat_login_status')
    if (current !== generation || !props.open) return
    const nextPhase = String(login?.phase || '')
    if (nextPhase !== phase.value || loggedIn.value !== (login?.is_login === true)) qrImage.value = ''
    phase.value = nextPhase
    loggedIn.value = login?.ok === true && login?.is_login === true
    const image = typeof login?.qr_image === 'string' ? login.qr_image : ''
    qrImage.value = login?.ok === true && !loggedIn.value && /^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(image) ? image : ''
    if (!login?.ok) error.value = errorText(login, '登录状态读取失败，将自动重试。')
  } else clearLogin()
  timer = setTimeout(() => poll(current), 2000)
}

// 串行执行按钮操作，启动和停止期间禁止重复提交。
async function perform(name: string, task: (current: number) => Promise<void>) {
  if (locked.value) return
  stopPolling(); generation++
  action.value = name; emit('busy', true); error.value = ''; message.value = ''
  const current = generation
  try { await task(current) }
  finally {
    action.value = ''; emit('busy', install.value.running)
    if (!disposed && props.open) await poll(generation)
  }
}

// 通过原生选择器指定外部目录，不预填系统临时路径。
async function pickDirectory() {
  await perform('pick', async current => {
    const result = await api('napcat_pick_directory')
    if (disposed || current !== generation || !props.open) return
    if (result?.cancelled) return
    if (!result?.ok || !result.directory) { error.value = errorText(result, '选择 NapCat 目录失败。'); return }
    directory.value = String(result.directory); qqPath.value = ''; inspected.value = false; clearLogin()
  })
}

// 检查用户选择的目录，并接受后端识别出的 QQ 路径。
async function inspectDirectory() {
  if (!directory.value.trim()) return
  await perform('inspect', async current => {
    const result = await api('napcat_runtime_inspect', directory.value.trim(), qqPath.value.trim(), mode.value)
    if (disposed || current !== generation || !props.open) return
    inspected.value = result?.ok === true
    if (!inspected.value) { error.value = errorText(result, `目录检查失败，请选择有效的${packageName.value}目录。`); return }
    directory.value = String(result.directory || directory.value); qqPath.value = String(result.qq_path || '')
    message.value = framework.value ? 'Framework 包检查通过，可以启动桌面 QQ。' : 'Shell 包检查通过，可以启动后台。'
  })
}

// 仅启动所选目录的工具实例，不调用任何自动快捷登录接口。
async function startRuntime() {
  if (!known.value || running.value || !inspected.value) return
  await perform('start', async current => {
    clearLogin()
    const result = await api('napcat_runtime_start', directory.value.trim(), qqPath.value.trim(), mode.value)
    if (disposed || current !== generation || !props.open) return
    if (!result?.ok) error.value = errorText(result, 'NapCat 启动失败。')
    else message.value = '启动请求已完成，正在检查登录状态。'
  })
}

// 确认后只停止工具管理的实例，拒绝操作外部进程。
async function stopRuntime() {
  if (!known.value || !confirmStop.value || !managed.value || !running.value) return
  await perform('stop', async current => {
    confirmStop.value = false; clearLogin()
    const result = await api('napcat_runtime_stop')
    if (disposed || current !== generation || !props.open) return
    if (!result?.ok) error.value = errorText(result, framework.value ? '停止 Framework 和桌面 QQ 失败，请重新检查状态。' : '停止后台失败，请重新检查状态。')
    else { message.value = '本工具启动的实例已停止。'; if (result.disconnected) emit('disconnected') }
  })
}

// 刷新之前先销毁旧图，后续轮询只显示最新登录状态。
async function refreshQr() {
  if (!managed.value || !running.value || loggedIn.value) return
  await perform('qr', async current => {
    clearLogin()
    const result = await api('napcat_refresh_qr')
    if (disposed || current !== generation || !props.open) return
    if (!result?.ok) error.value = errorText(result, '刷新二维码失败。')
  })
}

// 必须由用户明确输入预期账号并点击，成功后由父面板重新核验收藏连接。
async function connectFavorites() {
  const account = expectedAccount.value.trim()
  if (!/^\d{5,20}$/.test(account)) { error.value = '请填写预期 QQ 号（5–20 位数字）。'; return }
  if (!managed.value || !running.value || !loggedIn.value) return
  let connected = false
  await perform('connect', async current => {
    const result = await api('napcat_connect_favorites', account)
    if (disposed || current !== generation || !props.open) return
    if (!result?.ok) error.value = errorText(result, '接入收藏失败，请核对当前登录账号。')
    else if (String(result.account || '') !== account) error.value = '返回的 QQ 账号与预期账号不一致，请重新检查登录状态。'
    else connected = true
  })
  if (connected && !disposed && props.open) emit('connected', account)
}

// 开关面板时恢复状态或销毁二维码，不停止后台进程。
watch(() => props.open, open => {
  generation++; stopPolling(); clearLogin(); confirmStop.value = false
  if (open && !action.value) { error.value = ''; poll(generation) }
}, { immediate: true })

// 卸载后停止轮询并丢弃敏感数据和在途响应。
onBeforeUnmount(() => { disposed = true; generation++; stopPolling(); clearLogin(); emit('busy', false) })
</script>

<template>
  <section class="napcat-runtime" aria-labelledby="napcat-runtime-title">
    <div class="napcat-heading"><h3 id="napcat-runtime-title">NapCat 运行管理</h3><span role="status">{{ !known ? '状态待检查' : running ? managed ? '工具实例运行中' : '外部实例' : '未运行' }}</span></div>
    <label>运行模式<select v-model="mode" aria-label="运行模式" :disabled="locked || !known || running" @change="inspected = false; confirmStop = false; error = ''; message = ''; clearLogin()"><option value="shell">Shell 后台模式</option><option value="framework">Framework 桌面模式</option></select></label>
    <p v-if="framework" class="napcat-help">Framework 通过所选原始发布包的加载器启动桌面 QQ，并与该桌面 QQ 共用登录会话。不能在不重启 QQ 的情况下附加到已运行的 QQ；请先自行退出原有同账号 QQ。</p>
    <p v-else class="napcat-help">Shell 在后台运行，启动后按提示扫码登录。同账号的桌面 QQ 可能发生冲突，请先自行退出原有同账号 QQ。</p>
    <p v-if="framework" class="napcat-notice">停止 Framework 或退出 OhMyMeme 工具，会关闭本工具启动的桌面 QQ，并中断登录与收藏连接。外部 QQ 不会被接管或关闭。</p>
    <section class="napcat-install" aria-labelledby="napcat-install-title">
      <h4 id="napcat-install-title">下载 NapCat {{ installVersion }}</h4>
      <p class="napcat-help">下载所选模式的原始发布包到工具目录。完成后检查目录，由你手动启动。</p>
      <button type="button" :disabled="locked || !known || running" @click="startInstall">{{ action === 'install' ? '正在提交…' : '自动下载安装' }}</button>
      <template v-if="install.running || install.state && install.state !== 'idle'">
        <p role="status">{{ installState }}<span v-if="install.running || install.downloaded"> · {{ installBytes }}</span></p>
        <progress v-if="install.running || install.downloaded" :value="install.total ? Math.min(install.downloaded || 0, install.total) : undefined" :max="install.total || 1" aria-label="NapCat 下载进度" />
        <button v-if="install.running" type="button" :disabled="installCancelling || !!action" @click="cancelInstall">{{ installCancelling ? '正在取消…' : '取消安装' }}</button>
      </template>
      <div v-if="installError" class="napcat-notice" role="alert"><p>{{ installError }}</p><button type="button" :disabled="!!action" @click="poll()">重新检查安装状态</button></div>
    </section>
    <label>{{ packageName }}目录<input :value="directory" readonly :placeholder="`请选择${packageName}目录`" aria-label="已选择的 NapCat 目录" /></label>
    <div class="napcat-actions"><button type="button" :disabled="locked || running" @click="pickDirectory">{{ action === 'pick' ? '选择中…' : '选择目录' }}</button><button type="button" :disabled="locked || !directory || running" @click="inspectDirectory">{{ action === 'inspect' ? '检查中…' : '检查目录' }}</button></div>
    <label>QQ 程序路径（可选）<input v-model="qqPath" :disabled="locked || running" placeholder="留空由后台检测" autocomplete="off" spellcheck="false" @input="inspected = false" /></label>
    <div class="napcat-actions"><button type="button" :disabled="locked || !known || running || !inspected" @click="startRuntime">{{ action === 'start' ? '启动中…' : framework ? '启动桌面 QQ' : '启动后台' }}</button><button type="button" :disabled="locked || !known || !running || !managed" @click="confirmStop = true">{{ action === 'stop' ? '停止中…' : framework ? '停止 Framework 并关闭桌面 QQ' : '停止工具实例' }}</button></div>
    <p v-if="running && !managed" class="napcat-help">检测到外部实例，本工具拒绝接管或停止。可使用下方手工连接设置。</p>
    <div v-if="confirmStop" class="napcat-notice" role="group" :aria-label="framework ? '停止 Framework 并关闭桌面 QQ 确认' : '停止 Shell 后台确认'"><p>{{ framework ? '确认停止 Framework 并关闭本工具启动的桌面 QQ？登录与收藏连接将中断。退出 OhMyMeme 工具也会关闭该桌面 QQ。不会接管或关闭外部 QQ。' : '只停止由本工具启动的 Shell 后台 / QQ 实例，登录与收藏连接将中断。不会接管或停止外部实例。' }}</p><div class="napcat-actions"><button type="button" :disabled="locked" @click="confirmStop = false">取消</button><button type="button" :disabled="locked" @click="stopRuntime">{{ framework ? '确认停止 Framework 并关闭桌面 QQ' : '确认停止 Shell 后台' }}</button></div></div>
    <div v-if="running && managed" class="napcat-login">
      <p role="status">{{ loginText }}</p>
      <p v-if="framework" class="napcat-help">Framework 通常会恢复桌面 QQ 的登录会话；如需扫码或确认授权，请按桌面 QQ 或下方二维码提示操作，仍须由你完成必要授权。</p>
      <img v-if="qrImage" :src="qrImage" alt="QQ 登录二维码，请使用手机 QQ 扫码并确认账号" draggable="false" referrerpolicy="no-referrer" @error="qrImage = ''" />
      <p v-if="qrImage" class="napcat-help">登录二维码属于敏感信息，请勿分享或截图外传。</p>
      <p v-else-if="!loggedIn" class="napcat-help">{{ framework ? '若此处尚未显示二维码，请查看桌面 QQ 登录窗口；如需扫码，请按其提示操作。' : 'Shell 登录二维码准备好后将在此显示，请使用手机 QQ 扫码并确认。' }}</p>
      <button v-if="!loggedIn" type="button" :disabled="locked" @click="refreshQr">{{ action === 'qr' ? '刷新中…' : '刷新二维码' }}</button>
      <label>预期 QQ 号<input v-model="expectedAccount" inputmode="numeric" autocomplete="off" :disabled="locked" placeholder="手动填写要接入的 QQ 号" /></label>
      <button type="button" :disabled="locked || !loggedIn || !/^\d{5,20}$/.test(expectedAccount.trim())" @click="connectFavorites">{{ action === 'connect' ? '接入中…' : '接入收藏' }}</button>
    </div>
    <p v-if="error" class="napcat-notice" role="alert">{{ error }}</p>
    <button v-if="!known" type="button" :disabled="locked" @click="perform('status', async () => {})">重新检查后台</button>
    <p v-if="message" class="napcat-help" role="status">{{ message }}</p>
  </section>
</template>

<style scoped>
.napcat-runtime { display: flex; flex-direction: column; gap: 12px; padding: 12px 0 16px; margin-bottom: 8px; border-bottom: 1px solid var(--border); }
.napcat-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px; }
.napcat-heading h3 { margin: 0; font-size: 14px; font-weight: 600; }
.napcat-heading span, .napcat-help { color: var(--muted); font-size: 12px; }
.napcat-runtime p { margin: 0; overflow-wrap: anywhere; }
.napcat-runtime label { display: block; font-size: 13px; }
.napcat-runtime input, .napcat-runtime select { width: 100%; min-width: 0; height: 40px; margin-top: 6px; padding: 8px 10px; border: 1px solid var(--border-light); border-radius: 6px; background: var(--bg-elevated); color: var(--fg); font: inherit; user-select: text; }
.napcat-runtime input::placeholder { color: var(--muted); opacity: 1; }
.napcat-runtime button { min-height: 36px; padding: 6px 8px; color: var(--fg); border: 1px solid var(--border-light); border-radius: 6px; background: var(--surface); font: inherit; cursor: pointer; }
.napcat-runtime button:hover:not(:disabled) { background: var(--surface-2); }
.napcat-runtime button:disabled { color: var(--muted); cursor: not-allowed; }
.napcat-runtime :focus-visible { outline: 2px solid var(--primary); outline-offset: 3px; }
.napcat-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.napcat-actions button { flex: 1; }
.napcat-notice { padding: 12px; background: var(--surface-2); border-radius: 6px; font-size: 13px; }
.napcat-notice .napcat-actions { margin-top: 12px; }
.napcat-login { display: flex; flex-direction: column; align-items: stretch; gap: 12px; }
.napcat-login img { align-self: center; width: min(208px, 100%); aspect-ratio: 1; object-fit: contain; padding: 8px; background: #fff; border-radius: 6px; }
.napcat-install { display: flex; flex-direction: column; align-items: stretch; gap: 10px; padding: 12px; background: var(--surface); border-radius: 6px; }
.napcat-install h4 { margin: 0; font-size: 14px; font-weight: 600; }
.napcat-install progress { width: 100%; height: 8px; accent-color: var(--primary); }
@media (max-width: 760px) { .napcat-runtime button { min-height: 44px; } }
</style>
