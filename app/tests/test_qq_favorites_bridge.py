import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.webui import JsApi


class FakeConfig:
    # 只记录内存配置写入，避免访问真实用户目录。
    def __init__(self):
        self.values = {}
        self.writes = []

    # 模拟项目配置读取接口。
    def get(self, key, default=None):
        return self.values.get(key, default)

    # 记录单项配置更新。
    def set(self, key, value):
        self.writes.append((key, value))
        self.values[key] = value

    # 兼容桥接使用的批量配置保存接口。
    def update_from_dict(self, values):
        for key, value in values.items():
            self.set(key, value)

    # 不执行任何磁盘写入。
    def save(self):
        pass


@pytest.fixture
def api():
    # 绕过构造器及真实配置、数据库、QQ 连接。
    bridge = JsApi.__new__(JsApi)
    bridge._cfg = FakeConfig()
    bridge._db = SimpleNamespace(get_by_id=Mock(return_value=None))
    bridge._webui = SimpleNamespace(_find_meme_file=Mock(return_value=None))
    bridge._find_meme_file = bridge._webui._find_meme_file
    bridge._qq_token = ""
    bridge._qq_connection_lock = threading.Lock()
    bridge._qq_favorites = Mock(
        spec=[
            "status", "list_favorites", "start_upload", "start_delete",
            "progress", "cancel",
        ]
    )
    bridge._qq_favorites.status.return_value = {"ok": True, "account": "123456789"}
    bridge._qq_favorites.list_favorites.return_value = {"ok": True, "items": []}
    bridge._qq_favorites.start_upload.return_value = {"ok": True}
    bridge._qq_favorites.start_delete.return_value = {"ok": True}
    bridge._qq_favorites.progress.return_value = {"status": "idle", "running": False}
    bridge._qq_favorites.cancel.return_value = {"ok": True}
    return bridge


@pytest.fixture
def connected(api):
    # 建立仅在当前测试有效的连接参数。
    api._cfg.values.update(
        qq_favorites_endpoint="http://127.0.0.1:3000",
        qq_favorites_account="123456789",
    )
    api._qq_token = "test-private-token"
    return api


def test_status_defaults_do_not_expose_token(api):
    # 未配置时返回可直接用于表单的默认值。
    result = api.qq_favorites_status()
    assert result["ok"] is True
    assert result["configured"] is False
    assert result["endpoint"] == "http://127.0.0.1:3000"
    assert result["account"] == ""
    assert result["token_configured"] is False
    assert "token" not in result


def test_status_passes_expected_account_without_echoing_secret(connected):
    # 服务探测绑定指定账号，前端只获得令牌存在标记。
    result = connected.qq_favorites_status()
    assert result["ok"] is True
    assert result["configured"] is True
    assert result["account"] == "123456789"
    assert result["token_configured"] is True
    assert "token" not in result
    assert connected._qq_token not in json.dumps(result)
    connected._qq_favorites.status.assert_called_once_with(
        "http://127.0.0.1:3000", "test-private-token", "123456789"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com:3000", "http://192.168.1.2:3000",
        "http://0.0.0.0:3000", "file:///tmp/qq", "http://127.0.0.1.evil.test",
        "http://user:password@127.0.0.1:3000",
    ],
)
def test_save_rejects_nonlocal_or_credentialed_endpoint(connected, endpoint):
    # 不允许把内存凭据发送到非本机或含内嵌凭据的地址。
    before = dict(connected._cfg.values)
    result = connected.qq_favorites_save_connection(endpoint, "new-token", "123456789")
    assert result["ok"] is False
    assert connected._cfg.values == before
    assert connected._qq_token == "test-private-token"
    assert connected._cfg.writes == []


@pytest.mark.parametrize("account", ["", "abc", "123x", "-123", "12.3", "123 456", "１２３"])
def test_save_rejects_nonnumeric_account(connected, account):
    # 无效账号不得改变原有连接。
    before = dict(connected._cfg.values)
    result = connected.qq_favorites_save_connection(
        "http://127.0.0.1:3000", "new-token", account
    )
    assert result["ok"] is False
    assert connected._cfg.values == before
    assert connected._qq_token == "test-private-token"


def test_save_keeps_secret_in_memory_only(api):
    # 只持久化非敏感连接参数，返回值不得回显密钥。
    secret = "new-private-token"
    result = api.qq_favorites_save_connection(
        "http://127.0.0.1:3001", secret, "987654321"
    )
    assert result["ok"] is True
    assert api._qq_token == secret
    assert api._cfg.get("qq_favorites_endpoint") == "http://127.0.0.1:3001"
    assert api._cfg.get("qq_favorites_account") == "987654321"
    assert secret not in json.dumps(api._cfg.values)
    assert secret not in json.dumps(api._cfg.writes)
    assert secret not in json.dumps(result)
    assert "token" not in result


@pytest.mark.parametrize("token", ["", "   ", "\t\n"])
def test_blank_token_keeps_secret_for_same_connection(connected, token):
    # 同地址同账号保存空白令牌时保留现有内存令牌。
    result = connected.qq_favorites_save_connection(
        "http://127.0.0.1:3000", token, "123456789"
    )
    assert result["ok"] is True
    assert connected._qq_token == "test-private-token"


@pytest.mark.parametrize(
    "endpoint,account",
    [
        ("http://127.0.0.1:3001", "123456789"),
        ("http://127.0.0.1:3000", "987654321"),
    ],
)
def test_connection_change_clears_blank_token(connected, endpoint, account):
    # 更换地址或账号不能隐式复用旧连接的密钥。
    result = connected.qq_favorites_save_connection(endpoint, "  ", account)
    assert result["ok"] is True
    assert connected._qq_token == ""


def test_busy_rejects_connection_change(connected):
    # 进行中的任务继续使用原连接，拒绝修改任何参数。
    connected._qq_favorites.progress.return_value = {
        "status": "running", "running": True
    }
    before = dict(connected._cfg.values)
    result = connected.qq_favorites_save_connection(
        "http://127.0.0.1:3001", "replacement", "987654321"
    )
    assert result["ok"] is False
    assert connected._cfg.values == before
    assert connected._qq_token == "test-private-token"
    assert connected._cfg.writes == []


def test_list_uses_saved_connection(connected):
    # 列表请求绑定内存令牌和配置账号。
    result = connected.qq_favorites_list()
    assert result == {"ok": True, "items": []}
    connected._qq_favorites.list_favorites.assert_called_once_with(
        "http://127.0.0.1:3000", "test-private-token", "123456789"
    )


def test_list_and_delete_require_expected_account(api):
    # 读取和删除也必须绑定明确的预期账号。
    assert api.qq_favorites_list()["ok"] is False
    assert api.qq_favorites_delete(["resource-one"])["ok"] is False
    api._qq_favorites.list_favorites.assert_not_called()
    api._qq_favorites.start_delete.assert_not_called()


@pytest.mark.parametrize(
    "method", ["qq_favorites_upload_selected", "qq_favorites_upload_preview"]
)
def test_upload_requires_account(api, method):
    # 账号未指定时不能上传到当前偶然登录的其他账号。
    assert getattr(api, method)([1])["ok"] is False
    api._qq_favorites.start_upload.assert_not_called()
    api._db.get_by_id.assert_not_called()


@pytest.mark.parametrize(
    "ids",
    [None, [], "1", [0], [-1], [True], [1.5], ["1"], [1, "bad"],
     [{"path": "C:/private.png"}], ["C:/private.png"], [1] * 5001],
)
@pytest.mark.parametrize(
    "method", ["qq_favorites_upload_selected", "qq_favorites_upload_preview"]
)
def test_upload_rejects_invalid_ids_without_starting(connected, ids, method):
    # 前端只能提交非空的正整数 ID 列表，不能提交任意文件路径。
    assert getattr(connected, method)(ids)["ok"] is False
    connected._qq_favorites.start_upload.assert_not_called()
    connected._db.get_by_id.assert_not_called()


def test_upload_resolves_database_filenames_and_deduplicates(connected, tmp_path):
    # 上传文件仅来源于数据库记录及宿主路径解析，重复 ID 只处理一次。
    first = tmp_path / "first.png"
    second = tmp_path / "second.gif"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    rows = {1: {"filename": first.name}, 2: {"filename": second.name}}
    paths = {first.name: first, second.name: second}
    connected._db.get_by_id.side_effect = rows.get
    connected._webui._find_meme_file.side_effect = paths.get

    assert connected.qq_favorites_upload_selected([2, 1, 2])["ok"] is True
    args = connected._qq_favorites.start_upload.call_args.args
    assert args[:3] == (
        "http://127.0.0.1:3000", "test-private-token", "123456789"
    )
    assert [Path(path) for path in args[3]] == [second, first]
    assert connected._db.get_by_id.call_count == 2
    assert connected._webui._find_meme_file.call_count == 2


@pytest.mark.parametrize("missing", ["row", "unresolved", "absent", "directory"])
@pytest.mark.parametrize(
    "method", ["qq_favorites_upload_selected", "qq_favorites_upload_preview"]
)
def test_upload_rejects_entire_batch_when_any_file_missing(
    connected, tmp_path, missing, method
):
    # 任一条目不存在或不是文件时整批拒绝，不能部分上传。
    valid = tmp_path / "valid.png"
    valid.write_bytes(b"valid")
    rows = {1: {"filename": valid.name}, 2: {"filename": "missing.png"}}
    paths = {valid.name: valid, "missing.png": tmp_path / "absent.png"}
    if missing == "row":
        rows.pop(2)
    elif missing == "unresolved":
        paths["missing.png"] = None
    elif missing == "directory":
        paths["missing.png"] = tmp_path
    connected._db.get_by_id.side_effect = rows.get
    connected._webui._find_meme_file.side_effect = paths.get

    result = getattr(connected, method)([1, 2])
    assert result["ok"] is False
    assert "files" not in result
    assert "items" not in result
    assert str(tmp_path) not in json.dumps(result)
    assert connected._qq_token not in json.dumps(result)
    connected._qq_favorites.start_upload.assert_not_called()


def test_upload_preview_deduplicates_and_returns_only_display_fields(connected, tmp_path):
    # 预览按首次选中顺序去重，仅暴露显示字段，不调用任何远端服务。
    first = tmp_path / "first.png"
    second = tmp_path / "second.gif"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    rows = {
        1: {
            "filename": first.name,
            "original_name": "原始名称.png",
            "file_hash": "a" * 64,
            "path": str(first),
            "token": connected._qq_token,
        },
        2: {"filename": second.name, "original_name": "", "file_hash": "B" * 64},
    }
    connected._db.get_by_id.side_effect = rows.get
    connected._find_meme_file.side_effect = {first.name: first, second.name: second}.get

    result = connected.qq_favorites_upload_preview([2, 1, 2])

    assert result == {
        "ok": True,
        "account": "123456789",
        "total": 2,
        "items": [
            {"id": 2, "name": second.name, "thumbnail_url": "/api/thumb/" + "B" * 64},
            {"id": 1, "name": "原始名称.png", "thumbnail_url": "/api/thumb/" + "a" * 64},
        ],
    }
    assert [call.args[0] for call in connected._db.get_by_id.call_args_list] == [2, 1]
    assert connected._find_meme_file.call_count == 2
    assert str(tmp_path) not in json.dumps(result)
    assert connected._qq_token not in json.dumps(result)
    assert connected._qq_favorites.mock_calls == []
    assert connected._cfg.writes == []
    assert rows[1]["path"] == str(first)
    assert rows[2]["original_name"] == ""


@pytest.mark.parametrize(
    "file_hash", [None, "", "a" * 63, "a" * 65, "g" * 64, "../private", "a" * 64 + "\n"]
)
def test_upload_preview_invalid_hash_has_no_thumbnail(connected, tmp_path, file_hash):
    # 非法哈希不得拼入缩略图路由，名称缺失时使用数据库文件名。
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    connected._db.get_by_id.return_value = {
        "filename": image.name, "file_hash": file_hash
    }
    connected._find_meme_file.return_value = image

    result = connected.qq_favorites_upload_preview([1])

    assert result["ok"] is True
    assert result["items"] == [{"id": 1, "name": image.name, "thumbnail_url": ""}]
    connected._qq_favorites.start_upload.assert_not_called()


@pytest.mark.parametrize(
    "method", ["qq_favorites_upload_selected", "qq_favorites_upload_preview"]
)
def test_upload_rejects_shutdown_before_reading_database(connected, method):
    # 关机开始后不能继续预览或启动上传，也不再读取本地条目。
    connected._qq_shutting_down = True

    assert getattr(connected, method)([1])["ok"] is False

    connected._db.get_by_id.assert_not_called()
    connected._find_meme_file.assert_not_called()
    assert connected._qq_favorites.mock_calls == []


def test_upload_revalidates_file_after_preview(connected, tmp_path):
    # 预览成功后原图消失，确认上传必须重新检查并整批拒绝。
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    connected._db.get_by_id.return_value = {"filename": image.name}
    connected._find_meme_file.return_value = image
    assert connected.qq_favorites_upload_preview([1])["ok"] is True
    image.unlink()

    assert connected.qq_favorites_upload_selected([1])["ok"] is False

    assert connected._db.get_by_id.call_count == 2
    connected._qq_favorites.start_upload.assert_not_called()


def test_upload_preview_accepts_real_database_rows_without_writes(connected, tmp_path):
    # 真实数据库返回 dict；SQLite 只读守卫阻止预览隐式修改任何表。
    from src.database import MemeDB

    database = MemeDB(tmp_path / "memes.db")
    try:
        image = tmp_path / "image.png"
        image.write_bytes(b"image")
        meme_id = database.add_meme(
            image.name, file_hash="c" * 64, original_name="真实数据库.png"
        )
        assert isinstance(database.get_by_id(meme_id), dict)
        connection = database._get_conn()
        before = list(connection.iterdump())
        changes = connection.total_changes
        connection.execute("PRAGMA query_only=ON")
        connected._db = database
        connected._find_meme_file.return_value = image

        result = connected.qq_favorites_upload_preview([meme_id, meme_id])

        assert result == {
            "ok": True,
            "account": "123456789",
            "total": 1,
            "items": [{
                "id": meme_id,
                "name": "真实数据库.png",
                "thumbnail_url": "/api/thumb/" + "c" * 64,
            }],
        }
        assert connection.total_changes == changes
        assert list(connection.iterdump()) == before
        assert connected._qq_favorites.mock_calls == []
        assert connected._cfg.writes == []
    finally:
        database.close()


def test_delete_uses_saved_account_and_resource_ids(connected):
    # 删除传递 QQ 资源 ID，不接受客户端提供连接参数。
    resources = ["resource-one", "resource-two"]
    assert connected.qq_favorites_delete(resources)["ok"] is True
    connected._qq_favorites.start_delete.assert_called_once_with(
        "http://127.0.0.1:3000", "test-private-token", "123456789", resources
    )


def test_progress_and_cancel_delegate_without_connection_change(connected):
    # 轮询和取消只操作当前服务任务。
    progress = {"status": "running", "running": True, "done": 1, "total": 3}
    connected._qq_favorites.progress.return_value = progress
    assert connected.qq_favorites_progress() == progress
    assert connected.qq_favorites_cancel() == {"ok": True}
    connected._qq_favorites.cancel.assert_called_once_with()
    assert connected._qq_token == "test-private-token"


def test_config_bulk_update_preserves_local_qq_connection(tmp_path):
    # 普通配置同步不能覆盖本机连接，但其他设置仍能正常保存。
    from src.config import Config

    path = tmp_path / "config.json"
    cfg = Config(path)
    cfg.set("qq_favorites_endpoint", "http://127.0.0.1:3001")
    cfg.set("qq_favorites_account", "123456789")
    cfg.set("napcat_directory", "local-package")
    cfg.set("napcat_mode", "framework")
    cfg.set("theme", "dark")
    cfg.update_from_dict(
        {
            "qq_favorites_endpoint": "http://127.0.0.1:9999",
            "qq_favorites_account": "987654321",
            "napcat_directory": "remote-package",
            "napcat_mode": "shell",
            "theme": "light",
        }
    )
    cfg.save()

    restored = Config(path)
    assert restored.get("qq_favorites_endpoint") == "http://127.0.0.1:3001"
    assert restored.get("qq_favorites_account") == "123456789"
    assert restored.get("napcat_directory") == "local-package"
    assert restored.get("napcat_mode") == "framework"
    assert restored.get("theme") == "light"


def test_dedicated_bridge_can_save_real_local_connection(api, tmp_path):
    # 专用连接入口仍可通过 set 保存本机参数，令牌不进入配置文件。
    from src.config import Config

    path = tmp_path / "config.json"
    api._cfg = Config(path)
    result = api.qq_favorites_save_connection(
        "http://127.0.0.1:3001", "memory-only-secret", "123456789"
    )

    assert result["ok"] is True
    restored = Config(path)
    assert restored.get("qq_favorites_endpoint") == "http://127.0.0.1:3001"
    assert restored.get("qq_favorites_account") == "123456789"
    assert api._qq_token == "memory-only-secret"
    assert "memory-only-secret" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("allow_secret", [False, True])
def test_lan_export_always_excludes_local_qq_connection(
    tmp_path, monkeypatch, allow_secret
):
    # 无论是否允许密钥同步，LAN 均不得导出本机 QQ 连接参数。
    from src import lan
    from src.config import Config

    cfg = Config(tmp_path / "config.json")
    cfg.set("qq_favorites_endpoint", "http://127.0.0.1:3001")
    cfg.set("qq_favorites_account", "123456789")
    cfg.set("napcat_directory", "local-package")
    cfg.set("napcat_mode", "framework")
    cfg.set("theme", "light")
    cfg.set("ftp_password", "lan-test-secret")
    monkeypatch.setattr(lan, "get_config", lambda: cfg)
    monkeypatch.setattr(lan, "_transfer_update", Mock())
    monkeypatch.setattr(lan, "_lan_state", {"allow_secret_config": allow_secret})
    server = lan.LanServer.__new__(lan.LanServer)

    result = server._cmd_get_config({})

    assert result["ok"] is True
    exported = result["config"]
    assert "qq_favorites_endpoint" not in exported
    assert "qq_favorites_account" not in exported
    assert "napcat_directory" not in exported
    assert "napcat_mode" not in exported
    assert exported["theme"] == "light"
    if allow_secret:
        assert exported["ftp_password"] == "lan-test-secret"
    else:
        assert "ftp_password" not in exported
    assert cfg.get("qq_favorites_endpoint") == "http://127.0.0.1:3001"
    assert cfg.get("qq_favorites_account") == "123456789"
    assert cfg.get("napcat_directory") == "local-package"
    assert cfg.get("napcat_mode") == "framework"
