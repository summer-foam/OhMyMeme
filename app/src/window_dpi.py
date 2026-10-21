import ctypes
import logging
import platform
from ctypes import wintypes

logger = logging.getLogger(__name__)


# 原生消息先读取旧客户区，默认处理后仅修正本次 DPI 变化的尺寸。
def _dpi_message(api, state, hwnd, message, wparam, lparam, default):
    if message != 0x02E0 or state["busy"]:
        return default()
    old_dpi = state["dpi"]
    new_dpi = wparam & 0xFFFF
    if not old_dpi or not new_dpi or old_dpi == new_dpi:
        return default()
    client = wintypes.RECT()
    api.GetClientRect(hwnd, ctypes.byref(client))
    suggested = ctypes.cast(lparam, ctypes.POINTER(wintypes.RECT)).contents
    x, y = suggested.left, suggested.top
    width = round(client.right * new_dpi / old_dpi)
    height = round(client.bottom * new_dpi / old_dpi)
    state.update(busy=True, dpi=new_dpi)
    try:
        result = default()
        outer = wintypes.RECT()
        api.GetWindowRect(hwnd, ctypes.byref(outer))
        api.GetClientRect(hwnd, ctypes.byref(client))
        width += outer.right - outer.left - client.right
        height += outer.bottom - outer.top - client.bottom
        api.SetWindowPos(hwnd, None, x, y, width, height, 0x0004 | 0x0010)
        return result
    finally:
        state["busy"] = False


# 在 UI 线程安装原生子类；当前 Framework 宿主不派发托管 DpiChanged。
def _attach(form):
    if getattr(form, "_omm_dpi_handler", None) is not None:
        return True
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    for name in ("GetClientRect", "GetWindowRect"):
        getattr(user32, name).argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    comctl32.SetWindowSubclass.argtypes = [
        wintypes.HWND,
        callback_type,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    comctl32.RemoveWindowSubclass.argtypes = [
        wintypes.HWND,
        callback_type,
        ctypes.c_size_t,
    ]
    comctl32.DefSubclassProc.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    comctl32.DefSubclassProc.restype = ctypes.c_ssize_t
    hwnd = form.Handle.ToInt64()
    state = {"dpi": user32.GetDpiForWindow(hwnd), "busy": False}

    def callback(handle, message, wparam, lparam, subclass_id, data):
        processed = []

        def default():
            if not processed:
                processed.append(
                    comctl32.DefSubclassProc(handle, message, wparam, lparam)
                )
            return processed[0]

        if message == 0x0082:
            comctl32.RemoveWindowSubclass(handle, handler, 1)
        try:
            return _dpi_message(user32, state, handle, message, wparam, lparam, default)
        except Exception:
            logger.exception("Window DPI adjustment failed")
            return default()

    handler = callback_type(callback)
    if not comctl32.SetWindowSubclass(hwnd, handler, 1, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    form._omm_dpi_handler = handler
    return True


# shown 后同步调度至 UI 线程；其他平台和后端无害退出。
def install_window_dpi(window):
    if platform.system() != "Windows":
        return False
    try:
        form = window.native
        if form is None or not hasattr(form, "InvokeRequired"):
            return False
        from System import Action

        result = []

        def attach():
            result.append(_attach(form))

        if form.InvokeRequired:
            form.Invoke(Action(attach))
        else:
            attach()
        return result[0]
    except Exception:
        logger.warning("Unable to attach window DPI handler", exc_info=True)
        return False


# 每个窗口仅订阅一次并保留回调引用防止 GC。
def watch_window_dpi(window):
    events = getattr(window, "events", None)
    event = getattr(events, "shown", None)
    if event is None or not hasattr(event, "__iadd__"):
        return False
    if getattr(window, "_omm_dpi_shown", None) is not None:
        return True

    def shown():
        install_window_dpi(window)

    events.shown += shown
    window._omm_dpi_shown = shown
    return True
