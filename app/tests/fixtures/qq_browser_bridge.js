// Browser-only isolated bridge: no QQ connection and no user files.
(() => {
  const calls = []
  let configured = false
  let favorites = []
  let progress = { ok: true, running: false, items: [], total: 0, done: 0 }
  let runtime = { ok: true, managed: false, running: false, directory: '', qq_path: '', mode: 'shell', preferred_mode: 'shell', error: '' }
  let login = { ok: true, is_login: false, phase: 'waiting_qrcode', qr_image: '', error: '' }
  let localMemes = []
  let install = {running: false, ok: true, state: 'idle', downloaded: 0, total: 0}
  window.qqBrowserTest = {
    calls,
    setFavorites(value) { favorites = value },
    setProgress(value) { progress = value },
    setLogin(value) { login = value },
    setRuntime(value) { runtime = value },
    setLocalMemes(value) { localMemes = value },
    setInstall(value) { install = value },
    failInstall: false,
    failDownload: false,
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
    qq_favorites_download: ids => {
      if (window.qqBrowserTest.failDownload) return {ok: false, error: 'transport_or_invalid_response'}
      const selected = ids === null ? favorites : favorites.filter(face => ids.includes(face.resId))
      progress = {ok: true, running: true, kind: 'download', task_id: 'browser-download-' + calls.length, account: '123456789', total: selected.length, done: 0, items: selected.map(face => ({name: face.desc, state: 'pending'}))}
      return {ok: true, task_id: progress.task_id}
    },
    qq_favorites_cancel: () => ({ ok: true }),
    napcat_runtime_status: () => runtime,
    napcat_install_start: () => {
      if (window.qqBrowserTest.failInstall) return {ok: false, error: 'download_failed token=browser-install-secret Bearer browser-bearer-secret'}
      install = {ok: true, running: true, state: 'downloading', downloaded: 1048576, total: 4194304}
      return {ok: true}
    },
    napcat_install_progress: () => install,
    napcat_install_cancel: () => {install = {...install, ok: false, running: false, state: 'cancelled'}; return {ok: true}},
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
