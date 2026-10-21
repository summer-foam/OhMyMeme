import hashlib
import io
import json
import stat
import threading
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import napcat_installer as ni


# 内存响应允许模拟短读、取消、错误重定向，不访问真实网络。
class Response(io.BytesIO):
    def __init__(self, raw, url, headers=None, callback=None):
        super().__init__(raw)
        self.url = url
        self.headers = headers or {}
        self.status = 200
        self.callback = callback

    # 模拟响应声明的最终 URL。
    def geturl(self):
        return self.url

    # 在读取边界触发取消或阻塞，支持确定性的并发测试。
    def read(self, count=-1):
        if self.callback:
            self.callback()
        return super().read(count)


# 创建原始官方发布包结构，脚本包含明显副作用但永不执行。
def archive(mode="shell", extra=(), missing=None, compression=ni.zipfile.ZIP_STORED):
    output = io.BytesIO()
    with ni.zipfile.ZipFile(output, "w", compression=compression) as packed:
        for name in ni._REQUIRED[mode] - {missing}:
            packed.writestr(name, b"fixture-do-not-execute")
        for name, value in extra:
            packed.writestr(name, value)
    return output.getvalue()


# 为待下载字节生成独立元数据，摘要固定于响应创建时。
def release(raw, mode="shell"):
    return {
        "tag_name": ni._VERSION,
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": ni._ASSETS[mode],
                "size": len(raw),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "browser_download_url": ni._DOWNLOAD
                + ni._VERSION
                + "/"
                + ni._ASSETS[mode],
            }
        ],
    }


@pytest.fixture
def installer(tmp_path):
    # 隔离全部数据并在默认边界封锁外部网络。
    instance = ni.NapCatInstaller(tmp_path)
    instance._opener = Mock()
    instance._opener.open.side_effect = AssertionError("real HTTP forbidden")
    return instance


# 安装假的 HTTP 服务，支持重复读取并记录所有请求。
def serve(instance, raw=None, metadata=None, mode="shell", callback=None, headers=None):
    raw = archive(mode) if raw is None else raw
    metadata = release(raw, mode) if metadata is None else metadata

    # 按精确地址返回内存中的元数据和发布资产。
    def opened(request, **kwargs):
        if request.full_url == ni._API + ni._VERSION:
            return Response(json.dumps(metadata).encode(), request.full_url)
        assert request.full_url == ni._DOWNLOAD + ni._VERSION + "/" + ni._ASSETS[mode]
        return Response(raw, request.full_url, headers, callback)

    instance._opener.open.side_effect = opened
    return raw


# 等待真实后台任务退出，失败时给出完整状态。
def finish(instance):
    instance._thread.join(5)
    assert not instance._thread.is_alive(), instance.progress()
    state = instance.progress()
    assert not state["active"]
    assert not list(instance._root.glob(".install-*"))
    return state


@pytest.mark.parametrize("mode", ["shell", "framework"])
# 两种模式下载、验证、原子安装及重新核验复用均保留原始字节。
def test_install_and_verified_reuse(installer, mode):
    raw = serve(installer, mode=mode)
    assert installer.start(mode)["ok"]
    state = finish(installer)
    assert state["phase"] == "done" and state["progress"] == 100
    directory = Path(state["directory"])
    assert directory.parent.parent == installer._root
    assert (directory.parent / "release.zip").read_bytes() == raw
    assert {p.name for p in directory.iterdir()} == ni._REQUIRED[mode]
    assert installer._opener.open.call_count == 2
    assert installer.start(mode)["ok"]
    reused = finish(installer)
    assert reused["reused"] and reused["directory"] == state["directory"]
    assert installer._opener.open.call_count == 3
    assert all(
        c.kwargs["timeout"] == ni._TIMEOUT
        for c in installer._opener.open.call_args_list
    )


@pytest.mark.parametrize("mode", [None, [], {}, "", "Shell", "../shell"])
# 不合法输入在创建目录和联网前被拒绝。
def test_invalid_mode(installer, mode):
    assert installer.start(mode)["error"] == "invalid_mode"
    assert not installer._root.exists()
    installer._opener.open.assert_not_called()


@pytest.mark.parametrize("version", [None, "latest", "v4.18.39", "../v4.18.38", []])
# 只有显式审核过的兼容版本可下载。
def test_invalid_version(installer, version):
    assert installer.start(version=version)["error"] == "unsupported_version"
    installer._opener.open.assert_not_called()


@pytest.mark.parametrize("digest", [None, "", "sha256:abc", "md5:" + "a" * 64, 42])
# 官方摘要缺失或不合法时明确拒绝，不请求发布包。
def test_requires_release_sha256(installer, digest):
    raw = archive()
    metadata = release(raw)
    metadata["assets"][0]["digest"] = digest
    serve(installer, raw, metadata)
    installer.start()
    assert finish(installer)["error"] == "unverified_release_digest"
    assert installer._opener.open.call_count == 1
    assert not installer._root.exists()


@pytest.mark.parametrize(
    "change,error",
    [
        ({"tag_name": "v0.0.0"}, "invalid_release"),
        ({"draft": True}, "invalid_release"),
        ({"prerelease": True}, "invalid_release"),
        ({"assets": []}, "compatible_asset_missing"),
        ({"assets": "bad"}, "invalid_release"),
    ],
)
# 错误版本、预发布及缺失资产均不可安装。
def test_release_validation(installer, change, error):
    raw = archive()
    metadata = release(raw)
    metadata.update(change)
    serve(installer, raw, metadata)
    installer.start()
    assert finish(installer)["error"] == error


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("name", "NapCat.Shell.Windows.OneKey.zip", "compatible_asset_missing"),
        (
            "browser_download_url",
            "https://github.com/evil/repo/a.zip",
            "unsafe_download_url",
        ),
        ("size", ni._MAX_DOWNLOAD + 1, "download_too_large"),
        ("size", True, "download_too_large"),
        ("size", -1, "download_too_large"),
    ],
)
# 不能退化到一键安装器、其他仓库或超大下载。
def test_asset_validation(installer, field, value, error):
    raw = archive()
    metadata = release(raw)
    metadata["assets"][0][field] = value
    serve(installer, raw, metadata)
    installer.start()
    assert finish(installer)["error"] == error
    assert installer._opener.open.call_count == 1


# 同名资产歧义不可随机选择。
def test_ambiguous_asset(installer):
    raw = archive()
    metadata = release(raw)
    metadata["assets"] *= 2
    serve(installer, raw, metadata)
    installer.start()
    assert finish(installer)["error"] == "compatible_asset_missing"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a",
        "file:///secret",
        "https://evil.test/a",
        "https://github.com.evil.test/a",
        "https://github.com:444/a",
        "https://user@github.com/a",
        "https://github.com/a\\b",
        "https://github.com/a\nb",
        "https://github.com/a#b",
        "https://github.com:bad/a",
    ],
)
# URL 校验拒绝主机伪装、降级、凭据、非法端口与解析歧义。
def test_url_validation(url):
    with pytest.raises(ni._InstallError, match="unsafe_download_url"):
        ni._check_url(url, ni._HOSTS)


@pytest.mark.parametrize(
    "location",
    [
        "http://github.com/a",
        "https://evil.test/a",
        "https://api.github.com/a",
        "https://github.com:444/a",
        "https://user@github.com/a",
    ],
)
# 重定向目的地必须在每次发起请求前验证。
def test_redirect_rejected_before_request(installer, location):
    installer._opener.open.side_effect = urllib.error.HTTPError(
        "https://github.com/a", 302, "sensitive", {"Location": location}, io.BytesIO()
    )
    with pytest.raises(ni._InstallError, match="unsafe_download_url"):
        installer._open("https://github.com/a")
    assert installer._opener.open.call_count == 1


# 正常官方 CDN 跳转被手动允许，不依赖默认重定向器。
def test_official_redirect(installer):
    target = "https://release-assets.githubusercontent.com/github-production-release-asset/a?sig=x"
    installer._opener.open.side_effect = [
        urllib.error.HTTPError(
            "https://github.com/a", 302, "", {"Location": target}, io.BytesIO()
        ),
        Response(b"zip", target),
    ]
    with installer._open("https://github.com/a") as response:
        assert response.read() == b"zip"
    assert installer._opener.open.call_args.args[0].full_url == target


# 元数据禁止跳转，循环跳转受到跳数限制。
def test_metadata_and_redirect_limits(installer):
    installer._opener.open.side_effect = urllib.error.HTTPError(
        "https://api.github.com/a",
        302,
        "",
        {"Location": "https://github.com/a"},
        io.BytesIO(),
    )
    with pytest.raises(ni._InstallError, match="http_302"):
        installer._open("https://api.github.com/a", metadata=True)
    installer._opener.open.reset_mock()
    with pytest.raises(ni._InstallError, match="too_many_redirects"):
        installer._open("https://github.com/a")
    assert installer._opener.open.call_count == 6


@pytest.mark.parametrize(
    "failure,error",
    [
        ("digest", "sha256_mismatch"),
        ("short", "download_size_mismatch"),
        ("long", "download_too_large"),
        ("header", "download_size_mismatch"),
        ("encoding", "unexpected_content_encoding"),
    ],
)
# 下载体、长度声明及摘要不一致时不留下安装或临时数据。
def test_download_integrity(installer, failure, error):
    original = archive()
    metadata = release(original)
    raw = {
        "digest": b"x" * len(original),
        "short": original[:-1],
        "long": original + b"x",
    }.get(failure, original)
    headers = {"Content-Length": "1"} if failure == "header" else {}
    if failure == "encoding":
        headers["Content-Encoding"] = "gzip"
    serve(installer, raw, metadata, headers=headers)
    installer.start()
    assert finish(installer)["error"] == error
    assert not list(installer._root.iterdir())


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "C:/drive",
        "a\\..\\outside",
        "a/../../x",
        "a//b",
        "a/./b",
        "a./b",
        "a /b",
        "CON.txt",
        "nested/NUL",
        "a:stream",
        "a/../napcat.mjs",
    ],
)
# 路径攻击不允许逃逸 staging，也不能利用 Windows 路径别名。
def test_zip_path_attacks(installer, name):
    raw = archive(extra=[(name, b"bad")])
    serve(installer, raw)
    installer.start()
    state = finish(installer)
    assert state["error"] == "unsafe_zip_path"
    assert not list(installer._root.iterdir())


# 在原始 ZIP 字段注入 NUL，避免 ZipFile 写入时提前截断测试输入。
def test_zip_nul_in_raw_name(installer):
    raw = archive(extra=[("zero_tail", b"bad")]).replace(b"zero_tail", b"zero\x00tail")
    serve(installer, raw)
    installer.start()
    assert finish(installer)["error"] == "unsafe_zip_path"


# 官方摘要匹配但 ZIP 的 CRC 已损坏时仍拒绝安装。
def test_zip_crc_failure(installer):
    raw = archive().replace(b"fixture-do-not-execute", b"changed-do-not-execute", 1)
    serve(installer, raw)
    installer.start()
    assert finish(installer)["error"] == "invalid_zip"
    assert not list(installer._root.iterdir())


# 原子提交失败时只清理本任务 staging，不删除其他版本。
def test_atomic_commit_failure(installer, monkeypatch):
    serve(installer)
    installer._root.mkdir()
    unrelated = installer._root / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"keep")
    monkeypatch.setattr(ni.os, "rename", Mock(side_effect=OSError("failure")))
    installer.start()
    assert finish(installer)["error"] == "install_io_error"
    assert (unrelated / "keep").read_bytes() == b"keep"
    assert list(installer._root.iterdir()) == [unrelated]


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
# ZIP 的链接或特殊文件类型永远不解压。
def test_zip_links_and_special_files(installer, kind):
    member = ni.zipfile.ZipInfo("external-link")
    member.create_system = 3
    member.external_attr = (kind | 0o777) << 16
    serve(installer, archive(extra=[(member, b"../../outside")]))
    installer.start()
    assert finish(installer)["error"] == "zip_contains_links"


@pytest.mark.parametrize(
    "extra,error",
    [
        ([("NAPCAT.MJS", b"x")], "zip_duplicate_path"),
        ([("folder", b"x"), ("folder/child", b"x")], "zip_path_conflict"),
    ],
)
# 大小写重名和文件目录冲突在写盘前拒绝。
def test_zip_conflicts(installer, extra, error):
    serve(installer, archive(extra=extra))
    installer.start()
    assert finish(installer)["error"] == error


@pytest.mark.parametrize(
    "limit", ["_MAX_FILE", "_MAX_UNPACKED", "_MAX_MEMBERS", "_MAX_RATIO"]
)
# 压缩比、单文件、总大小及成员数分别受限。
def test_zip_bomb_limits(installer, monkeypatch, limit):
    raw = archive(extra=[("large", b"0" * 10000)], compression=ni.zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(ni, limit, 2)
    serve(installer, raw)
    installer.start()
    assert finish(installer)["error"] in {"unsafe_zip_size", "zip_member_limit"}
    assert not list(installer._root.iterdir())


# 缺入口或非 ZIP 均不可交给管理器启动。
@pytest.mark.parametrize(
    "raw,error",
    [
        (archive(missing="napcat.mjs"), "incompatible_package"),
        (b"not a zip", "invalid_zip"),
    ],
)
def test_invalid_packages(installer, raw, error):
    serve(installer, raw)
    installer.start()
    assert finish(installer)["error"] == error


# 真实后台线程支持进度查询、互斥启动及流式取消，不暴露半成品。
def test_cancel_busy_and_retry(installer):
    entered, proceed = threading.Event(), threading.Event()

    # 暂停在流式读取边界，主线程仍应立即返回状态。
    def blocked_read():
        entered.set()
        assert proceed.wait(3)

    serve(installer, callback=blocked_read)
    installer.start()
    assert entered.wait(3)
    try:
        state = installer.progress()
        assert state["active"] and state["phase"] == "downloading"
        assert state["directory"] == ""
        assert installer.start("framework")["error"] == "installer_busy"
        assert installer.cancel()["active"]
    finally:
        proceed.set()
    assert finish(installer)["phase"] == "cancelled"
    assert not list(installer._root.iterdir())
    serve(installer)
    installer.start()
    assert finish(installer)["phase"] == "done"


@pytest.mark.parametrize("changed", ["archive", "file", "extra", "missing"])
# 复用时重新验证原 ZIP 和所有解压字节，拒绝篡改但不覆盖现有数据。
def test_reuse_detects_tampering(installer, changed):
    serve(installer)
    installer.start()
    directory = Path(finish(installer)["directory"])
    target = directory / "napcat.mjs"
    if changed == "archive":
        target = directory.parent / "release.zip"
    if changed == "extra":
        target = directory / "extra"
    if changed == "missing":
        target.unlink()
    else:
        target.write_bytes(b"tampered")
    installer.start()
    assert finish(installer)["error"] == "existing_install_invalid"
    assert directory.exists()
    if changed != "missing":
        assert target.read_bytes() == b"tampered"


# 本地目录链接在下载前拒绝，即使路径解析后仍位于数据目录内。
def test_install_root_links_rejected(installer, monkeypatch):
    serve(installer)
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path == installer._root or original(path)
    )
    installer.start()
    assert finish(installer)["error"] == "unsafe_install_directory"
    assert installer._opener.open.call_count == 1


@pytest.mark.parametrize(
    "error,expected",
    [
        (urllib.error.URLError("sensitive-url"), "network_error"),
        (TimeoutError("sensitive"), "network_error"),
        (OSError("private-path"), "install_io_error"),
    ],
)
# 原始异常内容不能回显至桥接状态。
def test_network_errors_sanitized(installer, error, expected):
    installer._opener.open.side_effect = error
    installer.start()
    state = finish(installer)
    assert state["error"] == expected
    assert "sensitive" not in json.dumps(state)
    assert "private-path" not in json.dumps(state)


# 元数据响应有界且无效 JSON 明确失败。
@pytest.mark.parametrize(
    "raw,error",
    [(b"x", "invalid_metadata"), (b" " * (ni._MAX_METADATA + 1), "metadata_too_large")],
    ids=["invalid-json", "too-large"],
)
def test_metadata_bounds(installer, raw, error):
    installer._opener.open.side_effect = lambda request, **kw: Response(
        raw, request.full_url
    )
    installer.start()
    assert finish(installer)["error"] == error


# 网络构造器明确禁用代理和自动重定向。
def test_safe_opener(tmp_path, monkeypatch):
    build = Mock()
    monkeypatch.setattr(ni.urllib.request, "build_opener", build)
    ni.NapCatInstaller(tmp_path)
    proxy, redirect = build.call_args.args
    assert proxy.proxies == {}
    assert isinstance(redirect, ni._NoRedirect)
    assert redirect.redirect_request(None, None, 302, "", {}, "") is None


# 状态快照不能由调用方修改内部运行状态。
def test_progress_snapshot(installer):
    state = installer.progress()
    assert state["running"] == state["active"]
    assert state["state"] == state["phase"]
    assert state["downloaded"] == state["bytes_done"]
    assert state["total"] == state["bytes_total"]
    state["active"] = True
    assert not installer.progress()["active"]
    assert installer.cancel()["phase"] == "idle"


# 桥接兼容别名覆盖启动和取消的运行态。
def test_start_cancel_aliases(installer, monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    for state in (installer.start(), installer.progress(), installer.cancel()):
        assert state["running"] == state["active"] is True
        assert state["state"] == state["phase"] == "metadata"
        assert state["downloaded"] == state["bytes_done"] == 0
        assert state["total"] == state["bytes_total"] == 0


# 解压完成至提交前取消，完整候选包也不能成为可复用安装。
def test_cancel_before_atomic_commit(installer, monkeypatch):
    serve(installer)
    extract = installer._extract

    # 只在所有解压完成后设置取消标记。
    def cancel_after_extract(*args, **kwargs):
        extract(*args, **kwargs)
        installer.cancel()

    monkeypatch.setattr(installer, "_extract", cancel_after_extract)
    installer.start()
    assert finish(installer)["phase"] == "cancelled"
    assert not list(installer._root.iterdir())


# 即使底层 opener 异常地跟随了重定向，也校验最终响应 URL。
def test_unsafe_final_response_url(installer):
    response = Response(b"data", "http://evil.test/payload")
    installer._opener.open.side_effect = None
    installer._opener.open.return_value = response
    with pytest.raises(ni._InstallError, match="unsafe_download_url"):
        installer._open("https://github.com/a")
    assert response.closed
