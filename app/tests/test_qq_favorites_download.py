import hashlib
import io
import json
import socket
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image
from test_qq_favorites import ENDPOINT, finished
from test_qq_favorites import fake as qq_fake

from src import qq_favorites as qq

fake = qq_fake


@pytest.fixture
def content():
    # 使用真实可解码图像，避免仅 magic 校验掩盖坏文件。
    output = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def resource(content):
    # CDN 和摘要均来自隔离测试数据。
    return {
        "resId": "one",
        "md5": hashlib.md5(content).hexdigest(),
        "url": "https://p.qpic.cn/qq_expression/image",
    }


@pytest.fixture
def network(monkeypatch, content):
    # 所有 DNS 和 HTTP 都被替换，不访问真实 QQ 或公网。
    response = io.BytesIO(content)
    response.headers = {"Content-Length": str(len(content))}
    response.read1 = response.read
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(
        qq.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(qq.urllib.request, "build_opener", lambda *a: opener)
    return opener


# 默认下载最近一次列表快照全部，准确区分新增和重复，且不提交 QQ 写动作。
def test_download_snapshot_import_and_repeat(
    fake, resource, network, tmp_path, monkeypatch
):
    from src import webui
    from src.database import MemeDB

    db = MemeDB(tmp_path / "test.db")
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(webui, "get_config", lambda: Mock(cache_dir=cache))
    monkeypatch.setattr(webui, "get_db", lambda: db)
    monkeypatch.setattr(webui, "build_manifest", Mock())
    monkeypatch.setattr(webui, "exceeds_import_limit", lambda *a: False)
    ui = webui.WebUI.__new__(webui.WebUI)
    fake.favorites = [resource, {**resource, "resId": "two"}]
    service = qq.QQFavoritesService()
    assert service.list_favorites(ENDPOINT, "secret", "12345")["ok"]
    fake.favorites = []
    paths = []

    def importer(path, name):
        paths.append(path)
        return ui._do_import([path], [name])

    # 每个请求使用独立响应流。
    payload = network.open.return_value.getvalue()

    def response(*a, **k):
        value = io.BytesIO(payload)
        value.headers = {}
        value.read1 = value.read
        return value

    network.open.side_effect = response
    assert service.start_download(ENDPOINT, "secret", "12345", None, importer)["ok"]
    state = finished(service)
    assert [x["state"] for x in state["items"]] == ["imported", "already_exists"]
    assert state["kind"] == "download"
    assert state["scope"] == "observed_snapshot"
    assert state["completeness"] == "unknown"
    assert state["done"] == state["total"] == 2
    assert len(db.get_all()) == 1
    assert len(list(cache.iterdir())) == 1
    assert all(not Path(path).exists() for path in paths)
    assert all(
        action in ("get_login_info", "fetch_custom_face_detail")
        for action, _ in fake.calls
    )
    assert "secret" not in json.dumps(state)
    db.close()


# 未读取快照、未知资源、不同连接都不能直接下载任意 URL。
def test_snapshot_and_selection_required(fake, resource):
    service = qq.QQFavoritesService()
    importer = Mock()
    assert (
        service.start_download(ENDPOINT, "s", "12345", None, importer)["error"]
        == "snapshot_required"
    )
    fake.favorites = [resource]
    service.list_favorites(ENDPOINT, "s", "12345")
    assert (
        service.start_download(ENDPOINT, "s", "12345", ["missing"], importer)["error"]
        == "resource_not_in_snapshot"
    )
    assert (
        service.start_download(ENDPOINT, "s", "54321", None, importer)["error"]
        == "snapshot_required"
    )
    importer.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://p.qpic.cn/image",
        "https://127.0.0.1/image",
        "https://p.qpic.cn.evil.test/image",
        "https://user:secret@p.qpic.cn/image",
        "https://p.qpic.cn:444/image",
        "file:///private",
        "https://p.qpic.cn/image\n",
    ],
)
# 不可信 URL 在 DNS 和网络连接之前拒绝。
def test_unsafe_url(resource, network, tmp_path, url):
    with pytest.raises(qq.QQFavoritesError, match="unsafe_download_url"):
        qq._download_image({**resource, "url": url}, tmp_path, lambda: False, Mock())
    network.open.assert_not_called()


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
# 白名单域名解析到非公网目标时仍拒绝。
def test_private_dns(resource, network, monkeypatch, tmp_path, address):
    monkeypatch.setattr(
        qq.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (address, 443))]
    )
    with pytest.raises(qq.QQFavoritesError, match="unsafe_download_address"):
        qq._download_image(resource, tmp_path, lambda: False, Mock())
    network.open.assert_not_called()


@pytest.mark.parametrize(
    "case,error",
    [
        ("hash", "md5_mismatch"),
        ("size", "download_too_large"),
        ("cancel", "cancelled"),
        ("invalid", "invalid_image"),
        ("short", "download_incomplete"),
        ("timeout", "download_timeout"),
    ],
)
# 损坏、超大、取消、超时与截断响应均不得送入导入流程。
def test_download_validation(resource, network, tmp_path, monkeypatch, case, error):
    response = network.open.return_value
    if case == "hash":
        resource["md5"] = "0" * 32
    elif case == "size":
        monkeypatch.setattr(qq, "_MAX_DOWNLOAD", 8)
    elif case == "invalid":
        response = io.BytesIO(b"not-image")
        response.headers = {}
        response.read1 = response.read
        network.open.return_value = response
        resource["md5"] = hashlib.md5(b"not-image").hexdigest()
    elif case == "short":
        response.headers["Content-Length"] = "999"
    elif case == "timeout":
        monkeypatch.setattr(qq, "_DOWNLOAD_SECONDS", -1)
    with pytest.raises(qq.QQFavoritesError, match=error):
        qq._download_image(resource, tmp_path, lambda: case == "cancel", Mock())


# 账号切换在首次下载前阻断；下载任务也占用已有全局互斥锁。
def test_download_cancel_mutex_and_account(fake, resource, monkeypatch):
    fake.favorites = [resource, {**resource, "resId": "two"}]
    service = qq.QQFavoritesService()
    service.list_favorites(ENDPOINT, "s", "12345")
    entered, release = threading.Event(), threading.Event()
    importer = Mock(return_value={"ids": [1]})

    def download(resource, directory, cancelled, progress):
        entered.set()
        assert release.wait(5)
        return str(Path(directory) / "image.png")

    monkeypatch.setattr(qq, "_download_image", download)
    assert service.start_download(ENDPOINT, "s", "12345", None, importer)["ok"]
    try:
        assert entered.wait(5)
        assert service.start_delete(ENDPOINT, "s", "12345", ["one"])["error"] == "busy"
        assert (
            service.start_upload(ENDPOINT, "s", "12345", ["image"])["error"] == "busy"
        )
        service.cancel()
    finally:
        release.set()
    assert [x["state"] for x in finished(service)["items"]] == [
        "cancelled",
        "cancelled",
    ]
    importer.assert_not_called()
    fake.account = "999"
    assert service.start_download(ENDPOINT, "s", "12345", None, importer)["ok"]
    assert all(x["error"] == "account_mismatch" for x in finished(service)["items"])


# 固定地址连接保持 TLS 的 CDN 主机名，防止第二次 DNS 解析。
def test_https_connection_pins_public_address(monkeypatch):
    handler = qq._PinnedHTTPS("8.8.8.8")
    connected = Mock()
    monkeypatch.setattr(qq.socket, "create_connection", connected)

    def open_connection(factory, request, **kwargs):
        connection = factory("p.qpic.cn", timeout=10)
        assert connection.host == "p.qpic.cn"
        connection._create_connection(("p.qpic.cn", 443), 10)
        return "opened"

    monkeypatch.setattr(handler, "do_open", open_connection)
    assert handler.https_open(Mock()) == "opened"
    connected.assert_called_once_with(("8.8.8.8", 443), 10, None)


# 重定向不跟随，错误响应内的凭据不会回显。
def test_download_redirect_rejected(resource, network, tmp_path):
    import urllib.error

    network.open.side_effect = urllib.error.HTTPError(
        resource["url"], 302, "secret", {}, io.BytesIO(b"secret")
    )
    with pytest.raises(qq.QQFavoritesError, match="^download_http_error$"):
        qq._download_image(resource, tmp_path, lambda: False, Mock())
    assert network.open.call_count == 1


# 指定子集只下载一次，当前文件进度可查询。
def test_selected_download_progress(fake, resource, network):
    fake.favorites = [resource, {**resource, "resId": "two"}]
    service = qq.QQFavoritesService()
    service.list_favorites(ENDPOINT, "s", "12345")
    importer = Mock(return_value={"ids": [7]})
    assert service.start_download(ENDPOINT, "s", "12345", ["two", "two"], importer)[
        "ok"
    ]
    state = finished(service)
    assert state["total"] == state["done"] == 1
    assert state["items"][0]["item"] == "two"
    assert state["current_item"] == "two"
    assert state["bytes_done"] == state["bytes_total"] > 0
    importer.assert_called_once()


# 下载完成后账号已切换时不导入，也不开始剩余文件。
def test_account_switch_before_import(fake, resource, monkeypatch):
    fake.favorites = [resource, {**resource, "resId": "two"}]
    service = qq.QQFavoritesService()
    service.list_favorites(ENDPOINT, "s", "12345")
    importer = Mock()

    def changed_account(*args):
        fake.account = "999"
        return "not-imported"

    download = Mock(side_effect=changed_account)
    monkeypatch.setattr(qq, "_download_image", download)
    assert service.start_download(ENDPOINT, "s", "12345", None, importer)["ok"]
    state = finished(service)
    assert [item["state"] for item in state["items"]] == ["failed", "failed"]
    assert all(item["error"] == "account_mismatch" for item in state["items"])
    assert download.call_count == 1
    importer.assert_not_called()


@pytest.mark.parametrize("md5", [None, "", "0" * 31, "z" * 32])
# 缺失或非法摘要不能绕开完整性验证。
def test_download_requires_valid_hash(resource, network, tmp_path, md5):
    with pytest.raises(qq.QQFavoritesError, match="^invalid_md5$"):
        qq._download_image({**resource, "md5": md5}, tmp_path, lambda: False, Mock())
    network.open.assert_not_called()


# 没有 Content-Length 的流仍受字节上限保护。
def test_stream_size_limit(resource, network, tmp_path, monkeypatch):
    network.open.return_value.headers = {}
    monkeypatch.setattr(qq, "_MAX_DOWNLOAD", 8)
    with pytest.raises(qq.QQFavoritesError, match="^download_too_large$"):
        qq._download_image(resource, tmp_path, lambda: False, Mock())
