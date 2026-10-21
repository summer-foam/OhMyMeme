import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import window_dpi


# 模拟原生消息与实际 resize，验证双向缩放及默认处理顺序。
@pytest.mark.parametrize(
    "old,new,width,height,expected",
    [
        (144, 96, 1200, 840, (800, 560)),
        (96, 144, 800, 560, (1200, 840)),
    ],
)
def test_dpi_change_preserves_logical_client(old, new, width, height, expected):
    state = {"dpi": old, "busy": False}
    size = [width, height]
    calls = []

    def get_client(hwnd, pointer):
        pointer._obj.right, pointer._obj.bottom = size

    def get_outer(hwnd, pointer):
        pointer._obj.right, pointer._obj.bottom = size[0] + 16, size[1] + 39

    api = SimpleNamespace(GetClientRect=get_client, GetWindowRect=get_outer)

    def default():
        calls.append("default")
        size[:] = [999, 777]
        return 42

    def set_position(*args):
        calls.append(args)
        assert state["busy"]
        nested = Mock(return_value=19)
        assert (
            window_dpi._dpi_message(api, state, 123, 0x02E0, new, address, nested) == 19
        )
        nested.assert_called_once()

    api.SetWindowPos = set_position
    rect = window_dpi.wintypes.RECT(2680, 120, 3480, 680)
    address = ctypes.addressof(rect)
    assert window_dpi._dpi_message(api, state, 123, 0x02E0, new, address, default) == 42
    assert calls == [
        "default",
        (123, None, 2680, 120, expected[0] + 16, expected[1] + 39, 20),
    ]
    assert state == {"dpi": new, "busy": False}


# 普通 resize 只交给原生处理，不发生再次缩放。
def test_resize_is_not_rescaled():
    api = Mock()
    default = Mock(return_value=7)
    assert (
        window_dpi._dpi_message(api, {"dpi": 144, "busy": False}, 1, 5, 0, 0, default)
        == 7
    )
    api.GetClientRect.assert_not_called()
    api.SetWindowPos.assert_not_called()


# 无支持的平台和后端不导入 CLR、不影响窗口创建。
def test_unsupported_is_harmless(monkeypatch):
    monkeypatch.setattr(window_dpi.platform, "system", lambda: "Linux")
    assert window_dpi.install_window_dpi(SimpleNamespace()) is False
    monkeypatch.setattr(window_dpi.platform, "system", lambda: "Windows")
    assert window_dpi.install_window_dpi(SimpleNamespace(native=None)) is False


# 启动测试替身及非标准后端可能没有 shown 事件，不能阻断启动。
@pytest.mark.parametrize(
    "window",
    [
        SimpleNamespace(),
        SimpleNamespace(events=None),
        SimpleNamespace(events=SimpleNamespace()),
        SimpleNamespace(events=SimpleNamespace(shown=None)),
        SimpleNamespace(events=SimpleNamespace(shown=object())),
    ],
)
def test_eventless_window_is_harmless(window):
    assert window_dpi.watch_window_dpi(window) is False
    assert not hasattr(window, "_omm_dpi_shown")


# shown 与原生挂钩均幂等，回调保存在窗口上。
def test_duplicate_registration():
    class Event:
        def __init__(self):
            self.handlers = []

        def __iadd__(self, handler):
            self.handlers.append(handler)
            return self

    event = Event()
    window = SimpleNamespace(events=SimpleNamespace(shown=event))
    window_dpi.watch_window_dpi(window)
    window_dpi.watch_window_dpi(window)
    assert event.handlers == [window._omm_dpi_shown]
    assert window_dpi._attach(SimpleNamespace(_omm_dpi_handler=object())) is True


# 异常也必须释放递归保护，否则后续 DPI 切换会永久失效。
def test_resize_failure_releases_guard():
    api = Mock()
    api.SetWindowPos.side_effect = RuntimeError("resize failed")
    state = {"dpi": 144, "busy": False}
    rect = window_dpi.wintypes.RECT(0, 0, 800, 560)
    with pytest.raises(RuntimeError, match="resize failed"):
        window_dpi._dpi_message(
            api, state, 1, 0x02E0, 96, ctypes.addressof(rect), Mock()
        )
    assert state["busy"] is False
