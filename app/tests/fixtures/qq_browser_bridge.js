// Browser-only isolated bridge: no QQ connection and no user files.
(() => {
  const calls = []
  let configured = false
  let favorites = []
  let progress = { ok: true, running: false, items: [], total: 0, done: 0 }
  let runtime = { ok: true, managed: false, running: false, directory: '', qq_path: '', mode: 'shell', preferred_mode: 'shell', error: '' }
  let login = { ok: true, is_login: false, phase: 'waiting_qrcode', qr_image: '', error: '' }
  let localMemes = []
  window.qqBrowserTest = {
    calls,
    setFavorites(value) { favorites = value },
    setProgress(value) { progress = value },
    setLogin(value) { login = value },
    setRuntime(value) { runtime = value },
    setLocalMemes(value) { localMemes = value },
    failList: false,
    statusBusy: false,
  }
  const methods = {
    get_init_data: () => ({ theme: 'light', memes: localMemes, tags: [], collections: [], guide_ok: true, show_startup_animation: false }),
    count_memes: () => localMemes.length,
    search_memes: () => localMemes, get_tags: () => [], get_collections: () => [],
    get_settings: () => ({ theme: 'light' }), check_update: () => ({ has_update: false }),
    get_sync_status: () => ({ configured: false }),
    qq_favorites_status: () => window.qqBrowserTest.statusBusy ? ({ok:false,error:'busy',configured,account:''}) : ({ ok: true, configured, endpoint: 'http://127.0.0.1:3000', account: configured ? '123456789' : '', token_configured: configured }),
    qq_favorites_save_connection: () => { configured = true; return methods.qq_favorites_status() },
    qq_favorites_list: () => window.qqBrowserTest.failList ? ({ ok: false, error: 'transport_or_invalid_response' }) : ({ ok: true, account: '123456789', favorites, completeness: 'unknown' }),
    qq_favorites_progress: () => progress,
    qq_favorites_upload_selected: () => ({ ok: true, task_id: 'browser-upload' }),
    qq_favorites_upload_preview: ids => ({ ok: true, account: '123456789', total: ids.length, items: ids.map(id => ({ id, name: `本地测试表情 ${id}`, thumbnail_url: '' })) }),
    qq_favorites_delete: (ids) => { favorites = favorites.filter(face => !ids.includes(face.resId)); return { ok: true, task_id: 'browser-delete' } },
    qq_favorites_cancel: () => ({ ok: true }),
    napcat_runtime_status: () => runtime,
    napcat_pick_directory: () => ({ ok: true, directory: 'C:\\Test\\NapCat' }),
    napcat_runtime_inspect: (directory, qqPath, mode = 'shell') => ({ ok: true, directory, qq_path: 'C:\\Test\\QQ.exe', mode }),
    napcat_runtime_start: (directory, qqPath, mode = 'shell') => { runtime = { ...runtime, running: true, managed: true, directory, mode, preferred_mode: mode }; return runtime },
    napcat_runtime_stop: () => { runtime = { ...runtime, running: false, managed: false }; return { ...runtime, disconnected: true } },
    napcat_login_status: () => login,
    napcat_refresh_qr: () => login,
    napcat_connect_favorites: account => { configured = true; return { ok: true, account, endpoint: 'http://127.0.0.1:3000' } },
  }
  window.pywebview = { api: new Proxy({}, { get: (_, method) => async (...args) => {
    calls.push({ method, args: method === 'qq_favorites_save_connection' ? ['[redacted]'] : args })
    return methods[method]?.(...args) ?? null
  } }) }
})()
