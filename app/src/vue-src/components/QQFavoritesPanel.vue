<script setup lang="ts">
import {computed, nextTick, onBeforeUnmount, ref, watch} from 'vue'
import {api} from '../utils/api'
import NapCatRuntimePanel from './NapCatRuntimePanel.vue'

interface Favorite {
  resId: string;
  md5?: string;
  url?: string;
  thumbPath?: string;
  desc?: string
}

interface TaskItem {
  name?: string;
  state?: string;
  error?: string;

  [key: string]: unknown
}

interface Progress {
  running?: boolean;
  task_id?: string;
  items?: TaskItem[];
  done?: number;
  total?: number;
  status?: string;
  error?: string;
  message?: string;

  [key: string]: unknown
}

const props = withDefaults(defineProps<{ open: boolean; selectedIds?: number[] }>(), {selectedIds: () => []})
const emit = defineEmits<{ close: [] }>()
const panel = ref<HTMLElement>()
const confirmation = ref<HTMLElement>()
const tokenInput = ref<HTMLInputElement>()
const cancelButton = ref<HTMLButtonElement>()
const deleteButton = ref<HTMLButtonElement>()
const settingsButton = ref<HTMLButtonElement>()
const settingsPanel = ref<HTMLElement>()
const previewPanel = ref<HTMLElement>()
const uploadPanel = ref<HTMLElement>()
const uploadButton = ref<HTMLButtonElement>()
const page = ref(1)
const pageSize = 60
const preview = ref<Favorite | null>(null)
const pendingUpload = ref<{
  ids: number[];
  account: string;
  items: { id: number; name: string; thumbnail_url?: string }[]
} | null>(null)
let overlayFocus: HTMLElement | null = null
const endpoint = ref('http://127.0.0.1:3000')
const expectedAccount = ref('')
const account = ref('')
const connected = ref(false)
const configured = ref(false)
const interruptedTask = ref(false)
const checking = ref(false)
const saving = ref(false)
const loading = ref(false)
const starting = ref(false)
const cancelling = ref(false)
const connectionError = ref('')
const listError = ref('')
const taskError = ref('')
const notice = ref('')
const favorites = ref<Favorite[]>([])
const loaded = ref(false)
const listAccount = ref('')
const search = ref('')
const selection = ref<string[]>([])
const brokenImages = ref(new Set<string>())
const pendingDelete = ref<{ ids: string[]; account: string } | null>(null)
const progress = ref<Progress>({})
const hasTask = ref(false)
const taskUncertain = ref(false)
const settingsOpen = ref(false)
const runtimeBusy = ref(false)
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
let previousFocus: HTMLElement | null = null

const localIds = computed(() => [...new Set(props.selectedIds.filter(id => Number.isInteger(id) && id > 0))])
const running = computed(() => progress.value.running === true || progress.value.active === true || taskUncertain.value)
const busy = computed(() => checking.value || saving.value || loading.value || starting.value || running.value || runtimeBusy.value)
const validScope = computed(() => connected.value && !!account.value && (!expectedAccount.value.trim() || account.value === expectedAccount.value.trim()))
const canDelete = computed(() => validScope.value && loaded.value && !listError.value && listAccount.value === account.value && selection.value.length > 0 && !busy.value)
const filtered = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return favorites.value.filter(item => !query || [item.desc, item.md5, item.resId].some(value => String(value || '').toLocaleLowerCase().includes(query)))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize)))
const pageItems = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
const outsideSelection = computed(() => selection.value.filter(id => !pageItems.value.some(item => item.resId === id)).length)
const allSelected = computed(() => pageItems.value.length > 0 && pageItems.value.every(item => selection.value.includes(item.resId)))
const overlayOpen = computed(() => !!pendingDelete.value || !!pendingUpload.value || !!preview.value || settingsOpen.value)
const taskItems = computed(() => Array.isArray(progress.value.items) ? progress.value.items : [])
const terminalStates = ['done', 'success', 'completed', 'verified', 'duplicate', 'failed', 'error', 'submitted_unconfirmed', 'blocked', 'cancelled', 'canceled', 'skipped']
const completed = computed(() => typeof progress.value.done === 'number' ? Math.max(0, progress.value.done) : taskItems.value.filter(item => terminalStates.includes(String(item.state || item.status))).length)
const total = computed(() => typeof progress.value.total === 'number' ? Math.max(0, progress.value.total) : taskItems.value.length)
const failed = computed(() => taskItems.value.filter(item => ['failed', 'error', 'blocked'].includes(String(item.state || item.status))).length)
const verified = computed(() => taskItems.value.filter(item => String(item.state || item.status) === 'verified').length)
const duplicates = computed(() => taskItems.value.filter(item => String(item.state || item.status) === 'duplicate').length)
const blocked = computed(() => taskItems.value.filter(item => String(item.state || item.status) === 'blocked').length)
const unconfirmed = computed(() => taskItems.value.filter(item => String(item.state || item.status) === 'submitted_unconfirmed').length)
const taskTitle = computed(() => taskUncertain.value ? '任务状态待确认' : running.value ? '正在处理收藏' : ['cancelled', 'canceled'].includes(String(progress.value.status)) ? '任务已取消' : failed.value ? '任务结束，有失败项' : unconfirmed.value ? '任务结束，部分结果待核对' : progress.value.error ? '任务结束，需要检查' : '任务已结束')

// 统一服务端逐项状态，未确认提交不作为成功展示。
function itemState(item: TaskItem) {
  const state = String(item.state || item.status || 'pending')
  return ({
    pending: '等待',
    queued: '等待',
    running: '处理中',
    uploading: '上传中',
    deleting: '删除中',
    verified: '已核验',
    duplicate: '已存在',
    submitted_unconfirmed: '已提交，待核对',
    blocked: '已阻止',
    done: '成功',
    success: '成功',
    completed: '成功',
    failed: '失败',
    error: '失败',
    cancelled: '已取消',
    canceled: '已取消',
    skipped: '已跳过'
  } as Record<string, string>)[state] || state
}

// 只接受明确的 HTTP 图片地址，不把本地路径拼入浏览器 URL。
function imageUrl(item: Favorite) {
  for (const value of [item.thumbPath, item.url]) {
    if (!value || !/^https?:\/\//i.test(value)) continue
    try {
      const url = new URL(value)
      if (['http:', 'https:'].includes(url.protocol) && !url.username && !url.password) return url.href
    } catch { /* 无效地址使用占位文字。 */
    }
  }
  return ''
}

// 错误只展示可读信息；不回显 token 或连接中的认证字段。
function errorText(result: unknown, fallback: string) {
  const text = result && typeof result === 'object' && 'error' in result ? String(result.error || fallback) : fallback
  const secret = tokenInput.value?.value
  return readableError((secret ? text.split(secret).join('[已隐藏]') : text).replace(/Bearer\s+\S+/gi, 'Bearer [已隐藏]').slice(0, 500))
}

// 已知错误码翻译为可操作说明，未知码保留用于诊断。
function readableError(value: unknown) {
  const text = String(value || '')
  return ({
    busy: '已有任务正在运行，请等待任务结束。',
    account_mismatch: 'QQ 账号不匹配，请重新核对连接。',
    native_failed: 'QQ 未接受此操作，请检查 QQ 状态后核对收藏。',
    verification_pending: '操作已提交，结果待核对，请勿重复提交。',
    transport_or_invalid_response: '连接失败或服务返回无效响应，请检查本机服务。',
    resource_not_in_current_list: '该收藏已不在当前列表，请刷新后重新选择。',
    previous_submission_unconfirmed: '上次提交尚未核验，请先在 QQ 中核对结果。',
    unsupported_image: '此图片格式暂不支持上传。',
    http_403: '服务拒绝访问，请检查访问 token。'
  } as Record<string, string>)[text] || text
}

// 本地预览只允许后端缩略图路由。
function localThumbnail(value?: string) {
  return value && /^\/api\/thumb\/[a-f0-9]{64}$/i.test(value) ? value : ''
}

// 清除本轮定时器，过期响应由代次守卫丢弃。
function stopPolling() {
  if (timer) clearTimeout(timer);
  timer = undefined
}

let connectionCheckDeferred = false

// 获取连接状态，不读取或回填已保存的 token。
async function checkConnection() {
  if (busy.value) return
  checking.value = true
  connectionError.value = ''
  const current = generation
  const result = await api('qq_favorites_status')
  if (current !== generation) return
  checking.value = false
  if (result?.error === 'busy') {
    connectionCheckDeferred = true;
    return
  }
  connectionCheckDeferred = false
  configured.value = result?.configured === true
  interruptedTask.value = result?.interrupted_task === true
  account.value = result?.account ? String(result.account) : ''
  connected.value = result?.ok === true && configured.value && !!account.value
  if (result?.expected_account) expectedAccount.value = String(result.expected_account)
  else if (!expectedAccount.value && connected.value) expectedAccount.value = account.value
  if (result?.endpoint) endpoint.value = String(result.endpoint)
  if (!connected.value) connectionError.value = errorText(result, '未连接到本机 QQ 服务，请检查地址、token 和 QQ 登录状态。')
  if (!validScope.value && connected.value) connectionError.value = '当前 QQ 账号与预期账号不一致，请检查连接设置。'
  if (loaded.value && listAccount.value !== account.value) clearList()
}

// 换账号后丢弃旧收藏与选择，避免误删其他账号的数据。
function clearList() {
  favorites.value = [];
  selection.value = [];
  loaded.value = false;
  listAccount.value = '';
  brokenImages.value = new Set()
}

// 保存本机连接；输入框留空由后端保留原 token。
async function saveConnection() {
  if (busy.value) return
  connectionError.value = '';
  notice.value = ''
  try {
    const url = new URL(endpoint.value.trim())
    if (url.protocol !== 'http:' || !url.port || !['127.0.0.1', '[::1]'].includes(url.hostname) || url.username || url.password || url.search || url.hash) throw new Error()
  } catch {
    connectionError.value = '请输入带端口的本机 HTTP 地址，例如 http://127.0.0.1:3000 或 http://[::1]:3000。';
    return
  }
  if (!/^\d{5,20}$/.test(expectedAccount.value.trim())) {
    connectionError.value = '请填写预期 QQ 号（5–20 位数字）。';
    return
  }
  saving.value = true
  const current = generation
  const result = await api('qq_favorites_save_connection', endpoint.value.trim(), tokenInput.value?.value || '', expectedAccount.value.trim())
  if (current !== generation) return
  saving.value = false
  if (!result?.ok) {
    connectionError.value = errorText(result, '连接设置保存失败，请重试。');
    return
  }
  if (tokenInput.value) tokenInput.value.value = ''
  clearList()
  await checkConnection()
  if (validScope.value) {
    notice.value = '连接设置已保存。';
    settingsOpen.value = false;
    await loadFavorites()
  }
}

// 后台接入成功后重新核验连接并加载账号收藏。
async function onRuntimeConnected(expected: string) {
  if (!expected) {
    onRuntimeDisconnected();
    return
  }
  expectedAccount.value = expected
  clearList()
  await checkConnection()
  if (validScope.value) await loadFavorites()
}

// 停止自有后台后立即清除旧账号快照与连接状态。
function onRuntimeDisconnected() {
  connected.value = false
  clearList()
  connectionError.value = '工具实例已停止，请重新启动并接入收藏。'
}

// 加载账号快照，仅在响应账号匹配时开放删除。
async function loadFavorites() {
  if (busy.value || !validScope.value) return
  loading.value = true;
  listError.value = '';
  notice.value = ''
  const current = generation
  const result = await api('qq_favorites_list')
  if (current !== generation) return
  loading.value = false
  if (!result?.ok || !Array.isArray(result.favorites)) {
    listError.value = errorText(result, '收藏加载失败，可重试；当前显示的是上次成功加载的快照。');
    return
  }
  if (!result.account || String(result.account) !== account.value) {
    clearList();
    connected.value = false;
    listError.value = 'QQ 账号发生变化，请重新检查连接后再加载。';
    return
  }
  const seen = new Set<string>()
  favorites.value = result.favorites.filter((item: Favorite) => {
    if (!item || !item.resId || seen.has(String(item.resId))) return false
    item.resId = String(item.resId);
    seen.add(item.resId);
    return true
  })
  listAccount.value = String(result.account);
  loaded.value = true
  selection.value = selection.value.filter(id => seen.has(id));
  brokenImages.value = new Set()
}

// 只切换搜索结果中的选择，隐藏的已选收藏保持显式计数。
function toggleAll() {
  if (busy.value) return
  const visible = new Set(pageItems.value.map(item => item.resId))
  selection.value = allSelected.value ? selection.value.filter(id => !visible.has(id)) : [...new Set([...selection.value, ...visible])]
}

// 搜索和刷新只调整页码，跨页选择仍然保留。
watch(search, () => {
  page.value = 1
})
watch(pageCount, count => {
  page.value = Math.min(page.value, count)
})
// 自动连接成功收起设置时也归还焦点。
watch(settingsOpen, open => {
  if (!open && props.open) nextTick(() => settingsButton.value?.focus())
})

// 设置单独占据一层，并把焦点放到设置标题。
async function openSettings() {
  settingsOpen.value = true
  await nextTick();
  settingsPanel.value?.focus()
}

// 图片只通过已经校验的 HTTP 地址展示。
async function openPreview(item: Favorite, event: Event) {
  if (!imageUrl(item)) return
  overlayFocus = event.currentTarget as HTMLElement
  preview.value = item
  await nextTick();
  previewPanel.value?.focus()
}

// 上传前固定本地选择并读取名称、缩略图与目标账号。
async function confirmUpload() {
  if (busy.value || !validScope.value || !localIds.value.length) return
  const ids = [...localIds.value]
  const current = generation
  starting.value = true;
  taskError.value = ''
  const result = await api('qq_favorites_upload_preview', ids)
  if (current !== generation) return
  starting.value = false
  if (!result?.ok || !result.account || String(result.account) !== account.value || !Array.isArray(result.items) || result.items.length !== ids.length || !ids.every(id => result.items.some((item: {
    id: number
  }) => item.id === id))) {
    taskError.value = errorText(result, '无法确认上传图片或目标账号，请重新检查连接与本地选择。');
    return
  }
  pendingUpload.value = {ids, account: String(result.account), items: result.items}
  await nextTick();
  uploadPanel.value?.querySelector<HTMLButtonElement>('button')?.focus()
}

// 关闭最上层，恢复对应入口焦点。
function dismissOverlay() {
  if (starting.value) return
  if (preview.value) {
    preview.value = null;
    nextTick(() => overlayFocus?.isConnected && overlayFocus.focus());
    return
  }
  if (pendingUpload.value) {
    pendingUpload.value = null;
    nextTick(() => uploadButton.value?.focus());
    return
  }
  if (pendingDelete.value) {
    dismissConfirmation();
    return
  }
  if (settingsOpen.value) {
    settingsOpen.value = false;
    if (tokenInput.value) tokenInput.value.value = '';
    nextTick(() => settingsButton.value?.focus())
  }
}

// 打开危险确认时固定张数和账号，默认焦点放在取消。
async function confirmDelete() {
  if (!canDelete.value) return
  pendingDelete.value = {ids: [...selection.value], account: account.value}
  await nextTick();
  cancelButton.value?.focus()
}

// 收起确认后把焦点归还删除入口。
function dismissConfirmation() {
  pendingDelete.value = null;
  nextTick(() => deleteButton.value?.focus())
}

// 提交上传或已确认的删除，随后以服务端进度作为任务真源。
async function startTask(kind: 'upload' | 'delete') {
  if (busy.value || !validScope.value) return
  const deletion = pendingDelete.value
  const upload = pendingUpload.value
  if (kind === 'delete' && (!deletion || deletion.account !== account.value || listAccount.value !== account.value)) return
  if (kind === 'upload' && (!upload || upload.account !== account.value || !upload.ids.length)) return
  starting.value = true;
  taskError.value = '';
  notice.value = ''
  const current = generation
  const result = kind === 'upload' ? await api('qq_favorites_upload_selected', upload!.ids) : await api('qq_favorites_delete', deletion!.ids)
  if (current !== generation) return
  starting.value = false
  if (!result?.ok) {
    taskError.value = errorText(result, '任务未能启动，请检查连接后重试。');
    return
  }
  pendingDelete.value = null;
  pendingUpload.value = null;
  hasTask.value = true
  nextTick(() => (kind === 'upload' ? uploadButton.value : deleteButton.value)?.focus())
  progress.value = {
    running: true,
    task_id: result.task_id,
    items: [],
    total: kind === 'upload' ? upload!.ids.length : deletion!.ids.length,
    done: 0
  }
  await pollProgress(current)
}

// 单飞轮询；读取失败时保持操作锁，防止未知状态下重复修改。
async function pollProgress(current = generation) {
  stopPolling()
  const result = await api('qq_favorites_progress')
  if (current !== generation || !props.open) return
  if (!result || typeof result.running !== 'boolean' || !Array.isArray(result.items)) {
    taskError.value = errorText(result, '无法获取任务状态。请恢复连接后重新检查任务，避免重复上传或删除。')
    taskUncertain.value = true
    return
  }
  const wasRunning = running.value
  taskUncertain.value = false;
  taskError.value = ''
  progress.value = result
  hasTask.value = !!result.running || !!result.active || !!result.task_id || (Array.isArray(result.items) && result.items.length > 0)
  if (result.running || result.active) timer = setTimeout(() => pollProgress(current), 650)
  else {
    cancelling.value = false
    if (wasRunning || connectionCheckDeferred) {
      selection.value = []
      await checkConnection()
      if (current !== generation) return
      if (validScope.value) await loadFavorites()
    }
  }
}

// 取消只停止后续操作，已成功处理的收藏由重新加载核对。
async function cancelTask() {
  if (!running.value || cancelling.value) return
  cancelling.value = true
  const current = generation
  const result = await api('qq_favorites_cancel')
  if (current !== generation) return
  if (!result?.ok) {
    cancelling.value = false;
    taskError.value = errorText(result, '取消请求失败，请重新检查任务状态。');
    return
  }
  notice.value = '已请求取消，正在等待当前操作结束。'
  await pollProgress(current)
}

// 关闭面板不隐式取消后台任务，清除尚未提交的 token。
function closePanel() {
  if (overlayOpen.value) {
    dismissOverlay();
    return
  }
  if (tokenInput.value) tokenInput.value.value = ''
  emit('close')
}

// 捕获 Esc 与 Tab，防止主窗口快捷键穿透当前面板。
function onKeydown(event: KeyboardEvent) {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopImmediatePropagation();
    closePanel();
    return
  }
  if (event.key !== 'Tab') return
  if (!overlayOpen.value) return
  event.stopImmediatePropagation()
  const root = preview.value ? previewPanel.value : pendingUpload.value ? uploadPanel.value : pendingDelete.value ? confirmation.value : settingsOpen.value ? settingsPanel.value : panel.value
  const controls = root ? [...root.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary, [tabindex="0"]')].filter(el => el.getClientRects().length > 0) : []
  const first = controls[0];
  const last = controls[controls.length - 1]
  if (!first) {
    event.preventDefault();
    root?.focus();
    return
  }
  if (event.shiftKey && (document.activeElement === first || !root?.contains(document.activeElement))) {
    event.preventDefault();
    last.focus()
  } else if (!event.shiftKey && (document.activeElement === last || !root?.contains(document.activeElement))) {
    event.preventDefault();
    first.focus()
  }
}

// 每次打开恢复任务状态，关闭使所有在途请求失效。
watch(() => props.open, async open => {
  generation++;
  stopPolling()
  checking.value = false;
  saving.value = false;
  loading.value = false;
  starting.value = false;
  cancelling.value = false
  pendingDelete.value = null
  pendingUpload.value = null;
  preview.value = null;
  settingsOpen.value = false
  if (!open) {
    document.removeEventListener('keydown', onKeydown, true)
    if (tokenInput.value) tokenInput.value.value = ''
    if (previousFocus?.isConnected && !previousFocus.closest('[inert]')) previousFocus.focus()
    return
  }
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  document.addEventListener('keydown', onKeydown, true)
  progress.value = {};
  taskUncertain.value = false
  await nextTick();
  panel.value?.focus()
  const current = generation
  await checkConnection()
  if (current !== generation) return
  await pollProgress(current)
  if (current !== generation) return
  settingsOpen.value = !validScope.value && !running.value && !connectionCheckDeferred
  if (settingsOpen.value) {
    await nextTick();
    settingsPanel.value?.focus()
  }
}, {immediate: true})

// 卸载时回收事件和轮询，不影响后端正在运行的任务。
onBeforeUnmount(() => {
  generation++;
  stopPolling();
  document.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
    <section ref="panel" class="qq-panel" role="region" aria-label="QQ 收藏管理"
             tabindex="-1" @pointerdown.stop @click.stop>
      <header class="qq-header">
        <div><h1 id="qq-title">QQ 收藏管理</h1>
          </div>
        <div class="qq-header-actions"><span
            class="qq-account">{{ validScope ? `已连接 · QQ ${account}` : connected ? '账号不匹配' : '未连接' }}</span>
          <button ref="settingsButton" class="qq-settings-entry" :aria-expanded="settingsOpen" @click="openSettings">
            连接设置
          </button>
        </div>
      </header>
      <div class="qq-layout">
        <main class="qq-library" :inert="overlayOpen ? true : undefined">
          <div class="qq-library-heading">
            <div><h2>QQ 收藏</h2>
              <p>{{ loaded ? `已加载 ${favorites.length} 张 · 列表完整性未知` : '从本机 QQ 服务加载收藏列表' }}</p>
            </div>
            <button :disabled="busy || !validScope" @click="loadFavorites">
              {{ loading ? '加载中…' : loaded ? '刷新收藏' : '加载收藏' }}
            </button>
          </div>
          <div class="qq-toolbar"><label class="qq-search">搜索收藏<input v-model="search" type="search"
                                                                          placeholder="描述、MD5 或资源 ID"
                                                                          :disabled="!loaded"/></label>
            <button :disabled="busy || !pageItems.length" @click="toggleAll">{{
                allSelected ? '取消当前页' : '全选当前页'
              }}
            </button>
          </div>
          <div class="qq-selection"><span aria-live="polite">{{ filtered.length }} 张结果 · 跨页已选 {{
              selection.length
            }} 张<span v-if="outsideSelection">（当前页外 {{ outsideSelection }} 张）</span></span>
            <div>
              <button v-if="selection.length" :disabled="busy" @click="selection = []">清除选择</button>
              <button ref="deleteButton" class="qq-danger" :disabled="!canDelete" @click="confirmDelete">删除所选
              </button>
            </div>
          </div>
          <p v-if="connectionError && !settingsOpen" class="qq-notice" role="alert">{{ connectionError }}
            <button @click="openSettings">展开连接设置</button>
          </p>
          <div v-if="listError" class="qq-notice" role="alert"><p>{{ listError }}</p>
            <button :disabled="busy || !validScope" @click="loadFavorites">重试加载</button>
          </div>
          <div class="qq-grid-wrap" :aria-busy="loading">
            <div v-if="loading && !loaded" class="qq-state" role="status"><h3>正在加载收藏</h3>
              <p>正在读取 QQ 返回的收藏列表，请稍候。</p></div>
            <div v-else-if="!validScope" class="qq-state">
              <p>未登陆</p>
            </div>
            <div v-else-if="!loaded" class="qq-state"><h3>收藏列表尚未加载</h3>
              <p>加载后可搜索、选择和删除 QQ 收藏。</p>
              <button :disabled="busy" @click="loadFavorites">加载收藏</button>
            </div>
            <div v-else-if="!filtered.length" class="qq-state"><h3>{{
                search ? '没有匹配的收藏' : '本次未返回收藏'
              }}</h3>
              <p>{{ search ? '换个关键词，或清空搜索查看已加载收藏。' : '列表完整性未知；可刷新列表，或在 QQ 中核对。' }}</p>
              <button v-if="search" @click="search = ''">清空搜索</button>
            </div>
            <div v-else class="qq-grid">
              <article v-for="item in pageItems" :key="item.resId" class="qq-favorite"
                       :class="{ 'is-selected': selection.includes(item.resId) }">
                <div class="qq-image">
                  <button class="qq-image-preview" :disabled="!imageUrl(item) || brokenImages.has(item.resId)"
                          :aria-label="`放大预览 ${item.desc || item.resId}`" @click="openPreview(item, $event)"><img
                      v-if="imageUrl(item) && !brokenImages.has(item.resId)" :src="imageUrl(item)"
                      :alt="item.desc || 'QQ 收藏表情'" loading="lazy" referrerpolicy="no-referrer" draggable="false"
                      @error="brokenImages.add(item.resId)"/><span v-else>预览不可用</span></button>
                  <input v-model="selection" type="checkbox" :value="item.resId" :disabled="busy"
                         :aria-label="`选择 ${item.desc || item.resId}`"/></div>
                <span class="qq-caption" :title="item.desc || item.resId">{{ item.desc || '未命名收藏' }}</span><span
                  class="qq-resource" :title="item.resId">{{ item.resId }}</span>
              </article>
            </div>
          </div>
          <nav class="qq-pagination" aria-label="QQ 收藏分页"><span>{{
              filtered.length ? (page - 1) * pageSize + 1 : 0
            }}–{{ Math.min(page * pageSize, filtered.length) }} / {{ filtered.length }} 张 · 每页 {{
              pageSize
            }} 张</span>
            <div>
              <button :disabled="page <= 1" @click="page--">上一页</button>
              <span aria-live="polite">{{ page }} / {{ pageCount }}</span>
              <button :disabled="page >= pageCount" @click="page++">下一页</button>
            </div>
          </nav>
          <footer class="qq-library-footer">删除作用于全部跨页已选收藏，请先核对 QQ 账号。点击图片可放大预览。</footer>
        </main>
        <aside class="qq-sidebar" :inert="overlayOpen ? true : undefined">
          <section class="qq-upload"><h2>上传本地所选</h2>
            <p>本地已选 <strong>{{ localIds.length }}</strong> 张表情</p>
            <button ref="uploadButton" class="qq-primary" :disabled="busy || !validScope || !localIds.length"
                    @click="confirmUpload">上传 {{ localIds.length }} 张到 QQ
            </button>
            <p class="qq-help">{{
                !localIds.length ? '切换到本地表情库，选择要上传的表情后返回。' : `目标账号：${account || '尚未连接'}。上传前将展示图片与名称供确认。`
              }}</p></section>
          <section class="qq-task" aria-labelledby="qq-task-title">
            <p v-if="interruptedTask" class="qq-notice" role="status">上次退出时有操作未完成核验，请先刷新 QQ
              收藏并核对，避免重复提交。</p>
            <div class="qq-task-heading"><h2 id="qq-task-title">{{ hasTask || taskUncertain ? taskTitle : '任务' }}</h2>
              <button v-if="running && !taskUncertain" :disabled="cancelling" @click="cancelTask">
                {{ cancelling ? '取消中…' : '取消任务' }}
              </button>
            </div>
            <template v-if="hasTask">
              <div class="qq-progress-meta"><span>已处理 {{ completed }} / {{ total || '待确认' }} 张</span></div>
              <dl class="qq-task-counts">
                <div>
                  <dt>已核验</dt>
                  <dd>{{ verified }}</dd>
                </div>
                <div>
                  <dt>已存在</dt>
                  <dd>{{ duplicates }}</dd>
                </div>
                <div>
                  <dt>失败</dt>
                  <dd>{{ failed }}</dd>
                </div>
                <div>
                  <dt>待核对</dt>
                  <dd>{{ unconfirmed }}</dd>
                </div>
              </dl>
              <p v-if="blocked" class="qq-help">已阻止 {{ blocked }} 张（未执行）</p>
              <progress :value="completed" :max="Math.max(total, 1)" aria-label="收藏任务进度"/>
              <p v-if="progress.account" class="qq-help">任务账号：{{ progress.account }}</p>
              <p v-if="progress.message" class="qq-help">{{ progress.message }}</p>
              <ul v-if="taskItems.length" class="qq-task-items">
                <li v-for="(item, index) in taskItems" :key="index">
                  <div><span>{{ item.name || `表情 ${index + 1}` }}</span><span>{{ itemState(item) }}</span></div>
                  <p v-if="item.error" class="qq-item-error">{{ readableError(item.error) }}</p></li>
              </ul>
              <p v-if="unconfirmed" class="qq-notice">{{ unconfirmed }} 张已提交但尚未核验结果，请先在 QQ
                中确认，避免重复提交。</p>
              <p v-if="!running" class="qq-help">
                已完成的操作不会因取消而撤回。重试前先刷新收藏核对；上传请在本地重新选择失败项，删除需重新选择并确认。</p>
            </template>
            <p v-else class="qq-help">还没有收藏任务。上传、删除的处理结果会显示在这里。</p>
            <div v-if="taskError || progress.error" class="qq-notice" role="alert"><p>
              {{ taskError || readableError(progress.error) }}</p>
              <button :disabled="starting || checking || saving" @click="pollProgress()">重新检查任务</button>
            </div>
          </section>
          <p v-if="notice" class="qq-notice" role="status">{{ notice }}</p>
          <p v-if="running" class="qq-help">任务期间暂不能修改连接或收藏。切换工作区后任务会在后台继续。</p>
        </aside>
      </div>
      <div v-show="settingsOpen" class="qq-settings-overlay" @click.self="dismissOverlay">
        <section ref="settingsPanel" class="qq-settings-drawer qq-settings" role="dialog" aria-modal="true"
                 aria-labelledby="qq-settings-title" tabindex="-1">
          <header class="qq-drawer-heading">
            <div><h2 id="qq-settings-title">连接设置</h2>
              <p class="qq-help">
                {{ checking ? '检查中' : validScope ? `已连接 QQ ${account}` : configured ? '连接异常' : '未配置' }}</p>
            </div>
            <button aria-label="关闭连接设置" @click="dismissOverlay">×</button>
          </header>
          <NapCatRuntimePanel :open="open && settingsOpen" :disabled="busy || !!pendingDelete"
                              @busy="runtimeBusy = $event" @connected="onRuntimeConnected"
                              @disconnected="onRuntimeDisconnected"/>
          <form @submit.prevent="saveConnection"><h3>手工连接已有服务</h3><label>本机服务地址<input v-model="endpoint"
                                                                                                    type="url"
                                                                                                    :disabled="busy"
                                                                                                    autocomplete="off"
                                                                                                    spellcheck="false"
                                                                                                    aria-describedby="qq-connection-error"/></label><label>访问
            token<input ref="tokenInput" type="password" :disabled="busy" autocomplete="new-password"
                        placeholder="留空保留同地址、同账号的 token" aria-describedby="qq-token-help"/></label>
            <p id="qq-token-help" class="qq-help">已保存的 token 不回显；留空仅保留同地址、同账号的 token。</p><label>预期
              QQ 号<input v-model="expectedAccount" inputmode="numeric" :disabled="busy" autocomplete="off" required
                          placeholder="必填，用于核对目标账号"/></label>
            <div class="qq-setting-actions">
              <button type="submit" :disabled="busy">{{ saving ? '保存中…' : '保存并连接' }}</button>
              <button type="button" :disabled="busy" @click="checkConnection">检查连接</button>
            </div>
          </form>
          <p v-if="connectionError" id="qq-connection-error" class="qq-notice" role="alert">{{ connectionError }}</p>
        </section>
      </div>
      <div v-if="preview" class="qq-confirm-overlay" @click.self="dismissOverlay">
        <section ref="previewPanel" class="qq-preview" role="dialog" aria-modal="true"
                 aria-labelledby="qq-preview-title" tabindex="-1">
          <header class="qq-drawer-heading"><h2 id="qq-preview-title">{{ preview.desc || '未命名收藏' }}</h2>
            <button aria-label="关闭图片预览" @click="dismissOverlay">×</button>
          </header>
          <img :src="imageUrl(preview)" :alt="preview.desc || 'QQ 收藏表情'" referrerpolicy="no-referrer"/>
          <p class="qq-help">资源 ID：{{ preview.resId }}</p></section>
      </div>
      <div v-if="pendingUpload" class="qq-confirm-overlay" @click.self="dismissOverlay">
        <section ref="uploadPanel" class="qq-confirm qq-upload-confirm" role="dialog" aria-modal="true"
                 aria-labelledby="qq-upload-title" tabindex="-1"><h2 id="qq-upload-title">确认上传
          {{ pendingUpload.ids.length }} 张到 QQ</h2>
          <p>目标账号：<strong>{{ pendingUpload.account }}</strong>。请核对图片和名称后再开始上传。</p>
          <ul class="qq-upload-preview">
            <li v-for="item in pendingUpload.items" :key="item.id"><img v-if="localThumbnail(item.thumbnail_url)"
                                                                        :src="localThumbnail(item.thumbnail_url)"
                                                                        :alt="item.name" loading="lazy"/><span v-else
                                                                                                               class="qq-help">预览不可用</span><span>{{
                item.name
              }}</span></li>
          </ul>
          <p v-if="taskError" class="qq-notice" role="alert">{{ taskError }}</p>
          <div class="qq-confirm-actions">
            <button :disabled="starting" @click="dismissOverlay">取消</button>
            <button :disabled="busy" @click="startTask('upload')">
              {{ starting ? '正在提交…' : `确认上传 ${pendingUpload.ids.length} 张` }}
            </button>
          </div>
        </section>
      </div>
      <div v-if="pendingDelete" class="qq-confirm-overlay" @click.self="!starting && dismissConfirmation()">
        <section ref="confirmation" class="qq-confirm" role="alertdialog" aria-modal="true"
                 aria-labelledby="qq-confirm-title" aria-describedby="qq-confirm-desc" tabindex="-1"><h2
            id="qq-confirm-title">删除 {{ pendingDelete.ids.length }} 张 QQ 收藏？</h2>
          <p id="qq-confirm-desc">将从 QQ 账号 <strong>{{ pendingDelete.account }}</strong> 删除已选的
            <strong>{{ pendingDelete.ids.length }}</strong> 张收藏。此操作会修改 QQ 收藏，不能在这里撤销。</p>
          <p v-if="taskError" class="qq-notice" role="alert">{{ taskError }}</p>
          <div class="qq-confirm-actions">
            <button ref="cancelButton" :disabled="starting" @click="dismissConfirmation">取消</button>
            <button class="qq-danger" :disabled="busy" @click="startTask('delete')">
              {{ starting ? '正在提交…' : `确认删除 ${pendingDelete.ids.length} 张` }}
            </button>
          </div>
        </section>
      </div>
    </section>
</template>

<style scoped>
.qq-panel {
  position: relative;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Microsoft YaHei', sans-serif;
  font-variant-numeric: tabular-nums;
  -webkit-app-region: no-drag;
}

.qq-panel *, .qq-panel *::before, .qq-panel *::after {
  box-sizing: border-box;
}

.qq-panel h1, .qq-panel h2, .qq-panel h3, .qq-panel p {
  margin: 0;
}

.qq-panel h1 {
  font-size: 22px;
  font-weight: 600;
}

.qq-panel h2, .qq-panel h3 {
  font-size: 16px;
  font-weight: 600;
}

.qq-panel button, .qq-panel input {
  font: inherit;
}

.qq-panel button {
  min-height: 36px;
  padding: 6px 12px;
  color: var(--fg);
  border: 1px solid var(--border-light);
  border-radius: 6px;
  background: var(--surface);
  cursor: pointer;
  white-space: nowrap;
}

.qq-panel button:hover:not(:disabled) {
  background: var(--surface-2);
}

.qq-panel button:active:not(:disabled) {
  transform: translateY(1px);
}

.qq-panel button:disabled {
  color: var(--muted);
  cursor: not-allowed;
}

.qq-panel :focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: 3px;
}

.qq-panel input:not([type='checkbox']) {
  width: 100%;
  min-width: 0;
  height: 40px;
  margin-top: 6px;
  padding: 8px 10px;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  background: var(--bg-elevated);
  color: var(--fg);
  user-select: text;
}

.qq-panel input::placeholder {
  color: var(--muted);
  opacity: 1;
}

.qq-panel .qq-primary {
  width: 100%;
  background: var(--fg);
  color: var(--bg);
  border-color: transparent;
  font-weight: 600;
}

.qq-panel .qq-primary:hover:not(:disabled) {
  background: var(--fg);
  filter: brightness(.9);
}

.qq-panel .qq-primary:disabled {
  background: var(--surface-2);
  color: var(--muted);
}

.qq-panel .qq-danger {
  color: var(--danger);
}

.qq-panel .qq-danger:disabled {
  color: var(--muted);
}

.qq-header {
  min-height: 76px;
  padding: 12px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid var(--border);
}

.qq-header > div:first-child, .qq-header-actions {
  display: flex;
  align-items: center;
  gap: 20px;
}

.qq-account {
  color: var(--muted);
  overflow-wrap: anywhere;
}

.qq-header-actions button {
  min-width: 36px;
}

/* 固定侧栏 288px + 间距 24px + 可伸缩收藏列，占满两侧相同的 24px 留白。 */
.qq-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 288px;
  gap: 24px;
  padding: 24px;
  flex: 1;
  min-height: 0;
}

.qq-library {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.qq-library-heading, .qq-selection, .qq-task-heading, .qq-progress-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.qq-library-heading p, .qq-help, .qq-library-footer, .qq-resource {
  color: var(--muted);
  font-size: 13px;
}

.qq-library-heading p {
  margin-top: 4px;
}

.qq-toolbar {
  display: flex;
  align-items: end;
  gap: 12px;
  margin-top: 20px;
}

.qq-search {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--muted);
}

.qq-toolbar button {
  height: 40px;
}

.qq-selection {
  margin: 12px 0;
  font-size: 12px;
  color: var(--muted);
  flex-wrap: wrap;
}

.qq-selection > div {
  display: flex;
  gap: 8px;
}

.qq-selection button {
  min-height: 32px;
  font-size: 12px;
  padding: 4px 10px;
}

.qq-grid-wrap {
  overflow-y: auto;
  min-height: 0;
  flex: 1;
  padding: 4px;
  margin: -4px;
  scrollbar-width: thin;
}

.qq-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 16px;
  align-content: start;
}

.qq-favorite {
  min-width: 0;
  display: flex;
  flex-direction: column;
  cursor: pointer;
  border-radius: 6px;
  padding: 4px;
}

.qq-favorite:hover {
  outline: 1px solid var(--border-light);
}

.qq-favorite.is-selected {
  outline: 2px solid var(--primary);
}

.qq-image {
  aspect-ratio: 1;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface-2);
  border-radius: 4px;
  overflow: hidden;
}

.qq-image img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.qq-panel .qq-image-preview {
  border: 0;
  padding: 0;
  border-radius: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  white-space: normal;
}

.qq-image > span {
  font-size: 12px;
  color: var(--muted);
}

.qq-image input {
  position: absolute;
  top: 8px;
  left: 8px;
  width: 18px;
  height: 18px;
  accent-color: var(--primary-strong);
  cursor: pointer;
}

.qq-caption, .qq-resource {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.qq-caption {
  margin-top: 8px;
  font-size: 13px;
}

.qq-resource {
  margin-top: 2px;
}

.qq-state {
  padding: 32px 20px;
  background: var(--surface);
  border-radius: 6px;
}

.qq-state p {
  margin-top: 8px;
  color: var(--muted);
  max-width: 440px;
}

.qq-state button {
  margin-top: 16px;
}

.qq-library-footer {
  padding-top: 16px;
}

.qq-pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding-top: 16px;
  font-size: 12px;
  color: var(--muted);
  flex-wrap: wrap;
}

.qq-pagination > div {
  display: flex;
  align-items: center;
  gap: 12px;
}

.qq-task-counts {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin: 12px 0;
}

.qq-task-counts dt {
  color: var(--muted);
  font-size: 12px;
}

.qq-task-counts dd {
  margin: 4px 0 0;
  font-size: 20px;
}

.qq-settings-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, .65);
  display: flex;
  justify-content: flex-end;
}

.qq-settings-drawer {
  width: min(520px, 100%);
  height: 100%;
  overflow-y: auto;
  padding: 24px;
  background: var(--bg);
}

.qq-drawer-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
}

.qq-drawer-heading h2 {
  overflow-wrap: anywhere;
  min-width: 0;
}

.qq-settings-drawer > .qq-notice {
  margin-top: 16px;
}

.qq-preview {
  width: min(900px, 100%);
  max-height: 100%;
  overflow: auto;
  background: var(--surface);
  padding: 20px;
  border-radius: 6px;
}

.qq-preview > img {
  display: block;
  max-width: 100%;
  height: auto;
  max-height: 65vh;
  object-fit: contain;
  margin: 0 auto 16px;
}

.qq-upload-confirm {
  width: min(620px, 100%);
}

.qq-upload-preview {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  padding: 0;
  margin: 20px 0;
  max-height: 44vh;
  overflow: auto;
}

.qq-upload-preview li {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-wrap: anywhere;
}

.qq-upload-preview img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: contain;
  background: var(--surface-2);
  border-radius: 4px;
}

.qq-sidebar {
  overflow-y: auto;
  min-width: 0;
  scrollbar-width: thin;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.qq-upload, .qq-task {
  padding: 16px;
  background: var(--surface);
  border-radius: 6px;
}

.qq-upload > p {
  margin: 8px 0 12px;
}

.qq-upload > .qq-help {
  margin-bottom: 0;
}

.qq-upload strong {
  font-size: 20px;
  font-weight: 500;
  margin: 0 4px;
}

.qq-settings summary {
  min-height: 36px;
  cursor: pointer;
  font-weight: 600;
}

.qq-settings summary span {
  float: right;
  font-size: 12px;
  font-weight: 400;
  color: var(--muted);
}

.qq-settings form {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
}

.qq-settings label {
  display: block;
  font-size: 13px;
}

.qq-setting-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.qq-setting-actions button {
  flex: 1;
  padding-inline: 8px;
}

.qq-notice {
  padding: 12px;
  background: var(--surface-2);
  border-radius: 6px;
  font-size: 13px;
  overflow-wrap: anywhere;
}

.qq-notice button {
  margin-top: 8px;
}

.qq-task-heading {
  align-items: start;
}

.qq-task-heading button {
  padding: 2px 8px;
  min-height: 28px;
  font-size: 12px;
}

.qq-task > .qq-help {
  margin-top: 12px;
}

.qq-progress-meta {
  margin-top: 12px;
  font-size: 12px;
  color: var(--muted);
  flex-wrap: wrap;
}

.qq-task progress {
  display: block;
  width: 100%;
  height: 8px;
  margin: 8px 0 12px;
  accent-color: var(--primary);
}

.qq-task-items {
  padding: 0;
  margin: 12px 0;
  list-style: none;
  max-height: 220px;
  overflow: auto;
  scrollbar-width: thin;
}

.qq-task-items li {
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
}

.qq-task-items li > div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.qq-task-items li > div > span:first-child {
  overflow-wrap: anywhere;
  min-width: 0;
}

.qq-task-items li > div > span:last-child {
  flex-shrink: 0;
  color: var(--muted);
}

.qq-item-error {
  margin-top: 4px !important;
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.qq-confirm-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, .65);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.qq-confirm {
  width: min(460px, 100%);
  max-height: 100%;
  overflow-y: auto;
  padding: 24px;
  background: var(--surface);
  border-radius: 6px;
}

.qq-confirm > p {
  margin-top: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.qq-confirm-actions {
  display: flex;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 24px;
}

@media (max-width: 760px) {
  .qq-header {
    padding: 12px 16px;
    min-height: 64px;
  }

  .qq-header > div:first-child {
    gap: 12px;
  }

  .qq-header h1 {
    font-size: 20px;
  }

  .qq-header-actions {
    gap: 8px;
  }

  .qq-header {
    flex-wrap: wrap;
  }

  .qq-header-actions {
    flex-wrap: wrap;
  }

  .qq-account {
    font-size: 12px;
  }

  .qq-layout {
    display: flex;
    flex-direction: column;
    padding: 16px;
    overflow-y: auto;
    gap: 20px;
  }

  .qq-library {
    flex: none;
    min-height: 320px;
  }

  .qq-grid-wrap {
    flex: none;
    max-height: 45vh;
  }

  .qq-grid {
    grid-template-columns: repeat(auto-fill, minmax(112px, 1fr));
    gap: 12px;
  }

  .qq-sidebar {
    overflow: visible;
    flex-shrink: 0;
  }

  .qq-panel button {
    min-height: 44px;
  }

  .qq-panel .qq-selection button {
    min-height: 36px;
  }

  .qq-state {
    padding: 20px 16px;
  }

  .qq-library-footer {
    padding-top: 12px;
  }

  .qq-settings-drawer {
    padding: 16px;
  }

  .qq-upload-preview {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (prefers-reduced-motion: reduce) {
  .qq-panel button:active:not(:disabled) {
    transform: none;
  }
}
</style>
