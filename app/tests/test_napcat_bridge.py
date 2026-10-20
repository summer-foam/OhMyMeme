import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import webui
from src.webui import JsApi


@pytest.fixture
def api(monkeypatch):
    # 绕过真实配置、数据库、QQ 服务及运行环境构造。
    monkeypatch.setattr(
        webui,
        "webview",
        SimpleNamespace(FileDialog=SimpleNamespace(FOLDER="folder")),
        raising=False,
    )
    bridge = JsApi.__new__(JsApi)
    values = {"napcat_directory": "previous-package"}
    bridge._cfg = SimpleNamespace(
        values=values,
        get=Mock(side_effect=values.get),
        set=Mock(side_effect=values.__setitem__),
        save=Mock(),
    )
    bridge._qq_connection_lock = threading.RLock()
    bridge._qq_token = "previous-private-token"
    bridge._qq_managed_endpoint = ""
    bridge._qq_shutting_down = False
    bridge._db = SimpleNamespace(get_by_id=Mock())
    bridge._qq_favorites = Mock(
        spec=["progress", "status", "cancel", "start_upload", "start_delete"]
    )
    bridge._qq_favorites.progress.return_value = {"running": False}
    bridge._qq_favorites.status.return_value = {"ok": True, "account": "123456789"}
    bridge._napcat_manager = Mock(
        spec=[
            "status",
            "inspect",
            "start",
            "stop",
            "login_status",
            "refresh_qr",
            "connect_favorites",
        ]
    )
    bridge._napcat_manager.status.return_value = {"ok": True, "running": False}
    bridge._napcat_manager.start.return_value = {"ok": True, "running": True}
    bridge._napcat_manager.stop.return_value = {"ok": True, "running": False}
    bridge._napcat_manager.connect_favorites.return_value = {
        "ok": True,
        "endpoint": "http://127.0.0.1:3456",
        "token": "runtime-private-token",
    }
    bridge._napcat_runtime = Mock(return_value=bridge._napcat_manager)
    return bridge


@pytest.mark.parametrize(
    "method,args",
    [
        ("napcat_runtime_start", ("new-package", "qq.exe")),
        ("napcat_runtime_start", ("new-package", "qq.exe", "framework")),
        ("napcat_runtime_stop", ()),
        ("napcat_connect_favorites", ("123456789",)),
    ],
)
def test_busy_rejects_runtime_and_connection_changes(api, method, args):
    # 收藏任务忙碌时不触碰 manager、配置或已有令牌。
    api._qq_favorites.progress.return_value = {"running": True}
    before = dict(api._cfg.values)

    result = getattr(api, method)(*args)

    assert result["ok"] is False
    assert result["error"]
    api._napcat_runtime.assert_not_called()
    assert api._napcat_manager.mock_calls == []
    assert api._cfg.values == before
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()
    assert api._qq_token == "previous-private-token"


def test_connect_passes_internal_token_to_connection_save(api, monkeypatch):
    # 记录内部参数，同时执行真实保存方法以验证令牌不会回到前端。
    save = Mock(wraps=api.qq_favorites_save_connection)
    monkeypatch.setattr(api, "qq_favorites_save_connection", save)

    result = api.napcat_connect_favorites("123456789")

    api._napcat_manager.connect_favorites.assert_called_once_with("123456789")
    save.assert_called_once_with(
        "http://127.0.0.1:3456", "runtime-private-token", "123456789"
    )
    assert result["ok"] is True
    assert result["token_configured"] is True
    assert api._qq_token == "runtime-private-token"
    assert api._qq_managed_endpoint == "http://127.0.0.1:3456"
    assert api._cfg.values == {
        "napcat_directory": "previous-package",
        "qq_favorites_endpoint": "http://127.0.0.1:3456",
        "qq_favorites_account": "123456789",
    }
    api._cfg.save.assert_called_once_with()
    assert "token" not in result
    assert "runtime-private-token" not in json.dumps(result)
    assert "runtime-private-token" not in json.dumps(api._cfg.values)
    assert "runtime-private-token" not in repr(api._cfg.set.call_args_list)


@pytest.mark.parametrize("error", ["账号未登录", None])
def test_failed_connect_drops_manager_credentials(api, monkeypatch, error):
    # manager 的失败附带数据不能进入桥接响应或覆盖当前连接。
    failure = {
        "ok": False,
        "token": "failed-private-token",
        "endpoint": "http://127.0.0.1:3456",
        "data": {"token": "nested-private-token"},
    }
    if error is not None:
        failure["error"] = error
    api._napcat_manager.connect_favorites.return_value = failure
    save = Mock()
    monkeypatch.setattr(api, "qq_favorites_save_connection", save)

    result = api.napcat_connect_favorites("123456789")

    assert result == {"ok": False, "error": error or "连接失败"}
    save.assert_not_called()
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()
    assert api._qq_token == "previous-private-token"
    assert api._qq_managed_endpoint == ""
    assert "failed-private-token" not in json.dumps(result)
    assert "nested-private-token" not in json.dumps(result)


@pytest.mark.parametrize(
    "account",
    [
        None,
        123456789,
        True,
        [],
        {},
        "",
        "abc",
        "123x",
        "-123",
        "12.3",
        "123 456",
        " 123456789",
        "123456789\n",
        "１２３",
        "١٢٣",
        "²",
    ],
)
def test_connect_rejects_invalid_account_before_manager_call(api, account):
    # 只允许非空 ASCII 数字字符串，拒绝 Unicode 数字及类型混淆。
    result = api.napcat_connect_favorites(account)

    assert result["ok"] is False
    assert result["error"]
    api._napcat_runtime.assert_not_called()
    api._qq_favorites.progress.assert_not_called()
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()
    assert api._qq_token == "previous-private-token"


@pytest.mark.parametrize("ok", [True, False])
def test_owned_stop_clears_token_only_after_success(api, ok):
    # 当前连接属于受控实例时，只有停止成功才断开并清除凭据。
    endpoint = "http://127.0.0.1:3456"
    api._cfg.values["qq_favorites_endpoint"] = endpoint
    api._qq_managed_endpoint = endpoint
    expected = {"ok": ok, "running": not ok}
    api._napcat_manager.stop.return_value = expected

    assert api.napcat_runtime_stop() == {**expected, "disconnected": ok}

    api._napcat_manager.stop.assert_called_once_with()
    assert api._qq_token == ("" if ok else "previous-private-token")
    assert api._qq_managed_endpoint == ("" if ok else endpoint)
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()


@pytest.mark.parametrize("managed", ["", "http://127.0.0.1:3456"])
def test_stop_preserves_external_connection_token(api, managed):
    # 手动连接的外部实例不受受控运行环境停止影响。
    api._cfg.values["qq_favorites_endpoint"] = "http://127.0.0.1:4567"
    api._qq_managed_endpoint = managed

    result = api.napcat_runtime_stop()

    assert result == {"ok": True, "running": False, "disconnected": False}
    assert api._qq_token == "previous-private-token"
    assert api._qq_managed_endpoint == managed
    api._napcat_manager.stop.assert_called_once_with()
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()


def test_empty_endpoint_does_not_establish_managed_ownership(api):
    # 两个空地址不能被当成已建立的受控连接。
    api._cfg.values["qq_favorites_endpoint"] = ""

    result = api.napcat_runtime_stop()

    assert result["disconnected"] is False
    assert api._qq_token == "previous-private-token"


def test_failed_connection_save_does_not_record_managed_endpoint(api, monkeypatch):
    # manager 连接成功但保存被拒绝时不得改变连接归属。
    api._qq_managed_endpoint = "http://127.0.0.1:4567"
    save = Mock(return_value={"ok": False, "error": "保存被拒绝"})
    monkeypatch.setattr(api, "qq_favorites_save_connection", save)

    assert api.napcat_connect_favorites("123456789") == save.return_value

    assert api._qq_managed_endpoint == "http://127.0.0.1:4567"
    assert api._qq_token == "previous-private-token"
    api._cfg.save.assert_not_called()


@pytest.mark.parametrize("mode", ["shell", "framework"])
@pytest.mark.parametrize("ok", [True, False])
def test_start_saves_directory_and_mode_only_after_success(api, tmp_path, ok, mode):
    # 启动成功才保存目录和模式，失败保留之前的选择。
    directory = str(tmp_path / "NapCat 中文目录")
    qq_path = str(tmp_path / "QQ.exe")
    api._cfg.values["napcat_mode"] = "framework" if mode == "shell" else "shell"
    before = dict(api._cfg.values)
    expected = {"ok": ok, "running": ok}
    api._napcat_manager.start.return_value = expected

    assert api.napcat_runtime_start(directory, qq_path, mode) == expected

    api._napcat_manager.start.assert_called_once_with(directory, qq_path, mode)
    if ok:
        assert api._cfg.set.call_count == 2
        api._cfg.set.assert_any_call("napcat_directory", directory)
        api._cfg.set.assert_any_call("napcat_mode", mode)
        api._cfg.save.assert_called_once_with()
        status = api.napcat_runtime_status()
        assert status["directory"] == directory
        assert status["preferred_mode"] == mode
    else:
        api._cfg.set.assert_not_called()
        api._cfg.save.assert_not_called()
        assert api._cfg.values == before


@pytest.mark.parametrize("method", ["napcat_runtime_inspect", "napcat_runtime_start"])
@pytest.mark.parametrize(
    "mode", [None, True, 1, [], {}, "", "Framework", " framework", "other"]
)
def test_runtime_rejects_invalid_mode_before_manager_call(api, method, mode):
    # 非法模式在访问运行环境和修改配置之前拒绝。
    before = dict(api._cfg.values)

    assert getattr(api, method)("package", "qq.exe", mode) == {
        "ok": False,
        "error": "invalid_mode",
    }

    api._napcat_runtime.assert_not_called()
    assert api._napcat_manager.mock_calls == []
    api._qq_favorites.progress.assert_not_called()
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()
    assert api._cfg.values == before
    assert api._qq_token == "previous-private-token"


@pytest.mark.parametrize("mode", ["shell", "framework"])
def test_inspect_passes_mode_without_saving(api, mode):
    # 检查仅透传模式，不提前保存用户选择。
    expected = {"ok": True, "mode": mode}
    api._napcat_manager.inspect.return_value = expected

    assert api.napcat_runtime_inspect("package", "qq.exe", mode) == expected

    api._napcat_manager.inspect.assert_called_once_with("package", "qq.exe", mode)
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()


@pytest.mark.parametrize(
    "method,delegate",
    [
        ("napcat_runtime_inspect", "inspect"),
        ("napcat_runtime_start", "start"),
    ],
)
def test_runtime_defaults_to_shell(api, method, delegate):
    # 兼容省略 QQ 路径及模式的现有调用方。
    getattr(api, method)("package")

    getattr(api._napcat_manager, delegate).assert_called_once_with("package", "", "shell")


@pytest.mark.parametrize("mode", ["shell", "framework"])
def test_start_persists_mode_in_local_config(api, tmp_path, mode):
    # 使用隔离配置验证模式写盘并可在下次加载恢复。
    from src.config import Config

    path = tmp_path / "config.json"
    api._cfg = Config(path)
    assert api._cfg.get("napcat_mode") == "shell"

    assert api.napcat_runtime_start("package", "qq.exe", mode)["ok"] is True

    restored = Config(path)
    assert restored.get("napcat_directory") == "package"
    assert restored.get("napcat_mode") == mode
    assert api._qq_token not in path.read_text(encoding="utf-8")


def test_status_does_not_add_stored_credentials(api):
    # 前端状态仅接收 manager 公共状态和收藏令牌存在标记。
    api._cfg.values.update(
        qq_favorites_endpoint="http://127.0.0.1:3456",
        qq_favorites_account="123456789",
    )
    expected = {"ok": True, "running": True, "state": "ready"}
    api._napcat_manager.status.return_value = expected

    runtime = api.napcat_runtime_status()
    favorites = api.qq_favorites_status()

    assert runtime == {
        **expected,
        "directory": "previous-package",
        "preferred_mode": "shell",
    }
    assert favorites["token_configured"] is True
    assert favorites["expected_account"] == "123456789"
    api._napcat_manager.status.assert_called_once_with()
    api._qq_favorites.status.assert_called_once_with(
        "http://127.0.0.1:3456", "previous-private-token", "123456789"
    )
    for result in (runtime, favorites):
        assert "token" not in result
        assert "previous-private-token" not in json.dumps(result)
    assert expected == {"ok": True, "running": True, "state": "ready"}
    api._cfg.save.assert_not_called()


@pytest.mark.parametrize("selection", [None, (), []])
def test_native_directory_cancel_does_not_save(api, monkeypatch, selection):
    # 原生目录选择取消不会改动配置或触发运行环境操作。
    dialog = Mock(return_value=selection)
    monkeypatch.setattr(api, "_dialog", dialog)

    assert api.napcat_pick_directory() == {"ok": False, "cancelled": True}

    dialog.assert_called_once_with(webui.webview.FileDialog.FOLDER)
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()
    api._napcat_runtime.assert_not_called()


def test_native_directory_selection_returns_path_without_saving(
    api, monkeypatch, tmp_path
):
    # 选择仅返回路径，尚未启动前不持久化目录。
    directory = tmp_path / "NapCat"
    dialog = Mock(return_value=(directory,))
    monkeypatch.setattr(api, "_dialog", dialog)

    assert api.napcat_pick_directory() == {"ok": True, "directory": str(directory)}

    dialog.assert_called_once_with(webui.webview.FileDialog.FOLDER)
    api._cfg.set.assert_not_called()
    api._cfg.save.assert_not_called()
    api._napcat_runtime.assert_not_called()


@pytest.mark.parametrize("unconfirmed", [False, True])
def test_shutdown_marks_before_cancel_and_waits_for_final_result(
    api, monkeypatch, tmp_path, unconfirmed
):
    # 退出先落结果未知标记，再取消排队，等待当前核验完成后决定保留标记。
    api._cfg.data_dir = tmp_path
    api._cfg.values["qq_favorites_account"] = "123456789"
    marker = tmp_path / "qq-interrupted-task.json"
    initial = {"running": True, "items": [{"state": "submitting"}]}
    final = {
        "running": False,
        "items": [{"state": "submitted_unconfirmed" if unconfirmed else "confirmed"}],
    }
    events = []
    states = iter([initial, initial, final, final])

    # 按顺序模拟任务从提交中进入完成状态。
    def progress():
        events.append("progress")
        return next(states)

    # 取消发生时检查标记已写入，并验证退出守卫已阻止新写任务。
    def cancel():
        events.append("cancel")
        assert api._qq_shutting_down is True
        assert json.loads(marker.read_text(encoding="utf-8")) == {
            "state": "result_unknown",
            "task": initial,
        }
        assert api.qq_favorites_upload_selected([1]) == {
            "ok": False,
            "error": "应用正在退出",
        }
        assert api.qq_favorites_delete(["resource-one"]) == {
            "ok": False,
            "error": "应用正在退出",
        }

    api._qq_favorites.progress.side_effect = progress
    api._qq_favorites.cancel.side_effect = cancel
    api._napcat_manager.stop.side_effect = lambda: events.append("stop")
    sleep = Mock(side_effect=lambda seconds: events.append("wait"))
    monkeypatch.setattr(webui.time, "sleep", sleep)

    api._napcat_shutdown()

    assert events == [
        "progress",
        "cancel",
        "progress",
        "wait",
        "progress",
        "progress",
        "stop",
    ]
    api._qq_favorites.cancel.assert_called_once_with()
    sleep.assert_called_once_with(0.1)
    api._napcat_manager.stop.assert_called_once_with()
    api._qq_favorites.start_upload.assert_not_called()
    api._qq_favorites.start_delete.assert_not_called()
    api._db.get_by_id.assert_not_called()
    if unconfirmed:
        assert json.loads(marker.read_text(encoding="utf-8")) == {
            "state": "result_unknown",
            "task": final,
        }
        assert api._qq_token not in marker.read_text(encoding="utf-8")
    else:
        assert not marker.exists()
    api._cfg.save.assert_not_called()


def test_shutdown_timeout_preserves_unknown_result(api, monkeypatch, tmp_path):
    # 核验超时仍保留最后状态，并停止受控进程，不真实等待一分钟。
    api._cfg.data_dir = tmp_path
    initial = {"running": True, "items": [{"state": "submitting"}]}
    final = {"running": True, "items": [{"state": "submitted_unconfirmed"}]}
    api._qq_favorites.progress.side_effect = [initial, initial, final]
    monkeypatch.setattr(webui.time, "monotonic", Mock(side_effect=[0, 61]))
    sleep = Mock()
    monkeypatch.setattr(webui.time, "sleep", sleep)

    api._napcat_shutdown()

    marker = tmp_path / "qq-interrupted-task.json"
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "state": "result_unknown",
        "task": final,
    }
    api._qq_favorites.cancel.assert_called_once_with()
    api._napcat_manager.stop.assert_called_once_with()
    sleep.assert_not_called()
    assert api._qq_shutting_down is True


def test_idle_shutdown_without_manager_does_not_create_runtime(api, tmp_path):
    # 从未启动运行环境且没有收藏任务时，退出不取消任务或创建实例。
    api._cfg.data_dir = tmp_path
    api._napcat_manager = None

    api._napcat_shutdown()

    assert api._qq_shutting_down is True
    api._qq_favorites.cancel.assert_not_called()
    api._napcat_runtime.assert_not_called()
    assert not (tmp_path / "qq-interrupted-task.json").exists()
