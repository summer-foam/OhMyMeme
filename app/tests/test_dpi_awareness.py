import ctypes
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import platform_util


# 隔离全部 Win32 入口，任何测试都不能修改真实进程 DPI。
@pytest.fixture
def dpi_api(monkeypatch):
    v2 = Mock(return_value=True)
    shcore_setter = Mock(return_value=0)
    legacy = Mock(return_value=True)
    user32 = SimpleNamespace(
        SetProcessDpiAwarenessContext=v2, SetProcessDPIAware=legacy
    )
    shcore = SimpleNamespace(SetProcessDpiAwareness=shcore_setter)
    loader = Mock(
        side_effect=lambda name, **kwargs: {"user32": user32, "shcore": shcore}[name]
    )
    last_error = Mock(return_value=0)
    monkeypatch.setattr(platform_util.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ctypes, "WinDLL", loader, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", last_error, raising=False)
    return SimpleNamespace(
        v2=v2,
        shcore_setter=shcore_setter,
        legacy=legacy,
        user32=user32,
        shcore=shcore,
        loader=loader,
        last_error=last_error,
    )


# v2 成功后不再尝试任何较低 DPI 模式。
def test_windows_enables_per_monitor_v2(dpi_api):
    assert platform_util.enable_high_dpi() == "per_monitor_v2"

    dpi_api.loader.assert_called_once_with("user32", use_last_error=True)
    dpi_api.v2.assert_called_once()
    assert dpi_api.v2.call_args.args[0].value == ctypes.c_void_p(-4).value
    dpi_api.last_error.assert_not_called()
    dpi_api.shcore_setter.assert_not_called()
    dpi_api.legacy.assert_not_called()


# 拒绝重新设置通常表示宿主已配置，不能再降级覆盖。
def test_v2_access_denied_preserves_existing_configuration(dpi_api):
    dpi_api.v2.return_value = False
    dpi_api.last_error.return_value = 5

    assert platform_util.enable_high_dpi() == "already_configured"

    dpi_api.loader.assert_called_once_with("user32", use_last_error=True)
    dpi_api.last_error.assert_called_once_with()
    dpi_api.shcore_setter.assert_not_called()
    dpi_api.legacy.assert_not_called()


# 缺失 v2 入口或系统不支持该模式时回退逐显示器 v1。
@pytest.mark.parametrize("failure", ["missing", "exception", "unsupported"])
def test_v2_unavailable_falls_back_to_shcore(dpi_api, failure):
    if failure == "missing":
        del dpi_api.user32.SetProcessDpiAwarenessContext
    elif failure == "exception":
        dpi_api.v2.side_effect = OSError("v2 unavailable")
    else:
        dpi_api.v2.return_value = False
        dpi_api.last_error.return_value = 87

    assert platform_util.enable_high_dpi() == "per_monitor"

    assert dpi_api.loader.call_args_list == [
        call("user32", use_last_error=True),
        call("shcore", use_last_error=True),
    ]
    dpi_api.shcore_setter.assert_called_once_with(2)
    dpi_api.legacy.assert_not_called()


# HRESULT 的有符号与无符号 access denied 都必须停止降级。
@pytest.mark.parametrize("hresult", [-2147024891, 0x80070005])
def test_shcore_access_denied_preserves_existing_configuration(dpi_api, hresult):
    del dpi_api.user32.SetProcessDpiAwarenessContext
    dpi_api.shcore_setter.return_value = hresult

    assert platform_util.enable_high_dpi() == "already_configured"

    dpi_api.shcore_setter.assert_called_once_with(2)
    dpi_api.legacy.assert_not_called()


# shcore 缺失、异常或返回失败时尝试旧系统 API。
@pytest.mark.parametrize("failure", ["missing", "exception", "hresult"])
def test_shcore_unavailable_falls_back_to_legacy(dpi_api, failure):
    del dpi_api.user32.SetProcessDpiAwarenessContext
    if failure == "missing":
        del dpi_api.shcore.SetProcessDpiAwareness
    elif failure == "exception":
        dpi_api.shcore_setter.side_effect = OSError("shcore unavailable")
    else:
        dpi_api.shcore_setter.return_value = -2147467259

    assert platform_util.enable_high_dpi() == "system"

    dpi_api.legacy.assert_called_once_with()


# 非 Windows 平台完全不接触 Win32 API。
@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_non_windows_does_not_load_dlls(dpi_api, monkeypatch, system):
    monkeypatch.setattr(platform_util.platform, "system", lambda: system)

    assert platform_util.enable_high_dpi() == "not_windows"

    dpi_api.loader.assert_not_called()
    dpi_api.last_error.assert_not_called()
    dpi_api.v2.assert_not_called()
    dpi_api.shcore_setter.assert_not_called()
    dpi_api.legacy.assert_not_called()


# 最后一级 API 失败只返回不可用，异常不能阻断后续启动。
@pytest.mark.parametrize("failure", ["missing", "exception", "false"])
def test_all_dpi_apis_fail_without_raising(dpi_api, failure):
    dpi_api.v2.side_effect = OSError("v2 failed")
    dpi_api.shcore_setter.side_effect = OSError("shcore failed")
    if failure == "missing":
        del dpi_api.user32.SetProcessDPIAware
    elif failure == "exception":
        dpi_api.legacy.side_effect = OSError("legacy failed")
    else:
        dpi_api.legacy.return_value = False

    assert platform_util.enable_high_dpi() == "unavailable"


# DLL 本身无法加载也不能使启动失败。
def test_dll_load_failure_does_not_raise(dpi_api):
    dpi_api.loader.side_effect = OSError("DLL unavailable")

    assert platform_util.enable_high_dpi() == "unavailable"

    assert dpi_api.loader.call_args_list == [
        call("user32", use_last_error=True),
        call("shcore", use_last_error=True),
    ]
    dpi_api.v2.assert_not_called()
    dpi_api.shcore_setter.assert_not_called()
    dpi_api.legacy.assert_not_called()
