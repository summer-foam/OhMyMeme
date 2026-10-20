import hashlib
import io
import json
import os
import threading
import time
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import napcat_manager as nm


class FakeJob:
    # 测试只模拟自有进程树，不访问 Windows 内核。
    def __init__(self):
        self.count = 2
        self.closed = False
        self.terminated = False
        self.attached = None

    # 模拟挂起启动器成功归属 Job。
    def attach_and_resume(self, process):
        self.attached = process

    # 即使启动器退出，仍返回子进程数量。
    def active_count(self):
        return self.count

    # 仅标记本 Job 已停止。
    def terminate(self):
        self.terminated = True
        self.count = 0

    # 模拟释放 Job 句柄。
    def close(self):
        self.closed = True


@pytest.fixture
def package(tmp_path):
    # 构造虚拟发布包和 QQ 路径，绝不引用机器上的真实 QQ。
    source = tmp_path / "release"
    source.mkdir()
    for name in nm._FILES.values():
        (source / name).write_text("fixture", encoding="utf-8")
    for name in ("config", "logs", "cache"):
        folder = source / name
        folder.mkdir()
        (folder / "private.txt").write_text("original-private", encoding="utf-8")
    qq = tmp_path / "Tencent/QQNT/QQ.exe"
    qq.parent.mkdir(parents=True)
    qq.write_bytes(b"fake-qq")
    return source, qq


@pytest.fixture
def framework_package(package):
    # Framework 夹具不含 Shell 文件和可选 napcat.cjs。
    source, qq = package
    for name in nm._FILES.values():
        if name != "napcat.mjs":
            (source / name).unlink()
    for name in ("napimain.exe", "napiloader.dll", "nativeLoader.cjs"):
        (source / name).write_text("framework-fixture", encoding="utf-8")
    return source, qq


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # 默认封锁所有真实子进程启动和外部网络。
    monkeypatch.setattr(nm.sys, "platform", "win32")
    monkeypatch.setattr(nm, "_WindowsJob", FakeJob)
    process = Mock(pid=987654)
    process.poll.return_value = None
    process.wait.return_value = 0
    monkeypatch.setattr(nm.subprocess, "Popen", Mock(return_value=process))
    instance = nm.NapCatManager(tmp_path / "private-runtime")
    instance._opener = Mock()
    instance._opener.open.side_effect = AssertionError("real HTTP forbidden")
    return instance


# 检查完整发布包并从 ProgramFiles 自动发现虚拟 QQ。
def test_inspect_autodetect_and_missing(manager, package, monkeypatch):
    source, qq = package
    monkeypatch.setenv("ProgramFiles", str(qq.parents[2]))
    result = manager.inspect(str(source))
    assert result["ok"] and result["qq_path"] == str(qq)
    assert result["mode"] == "shell"
    assert all(result["files"].values())
    (source / "loadNapCat.js").unlink()
    result = manager.inspect(str(source), str(qq))
    assert result["error"] == "missing_package_files"
    assert result["missing"] == ["loadNapCat.js"]


@pytest.mark.parametrize(
    "filename", ["napimain.exe", "napiloader.dll", "nativeLoader.cjs", "napcat.mjs"]
)
# Framework 使用独立文件清单，缺少任一入口即拒绝启动。
def test_inspect_framework_required_files(manager, framework_package, filename):
    source, qq = framework_package
    result = manager.inspect(str(source), str(qq), "framework")
    assert result["ok"] and result["mode"] == "framework"
    assert set(result["files"]) == {"launcher", "hook", "loader", "main"}
    assert manager.inspect(str(source), str(qq))["error"] == "missing_package_files"
    (source / filename).unlink()
    result = manager.start(str(source), str(qq), "framework")
    assert result["error"] == "missing_package_files"
    assert result["missing"] == [filename]
    nm.subprocess.Popen.assert_not_called()


# Shell 包不会误当作 Framework 包。
def test_shell_package_rejected_as_framework(manager, package):
    result = manager.inspect(*map(str, package), mode="framework")
    assert result["error"] == "missing_package_files"
    assert result["missing"] == ["napimain.exe", "napiloader.dll", "nativeLoader.cjs"]


@pytest.mark.parametrize("mode", ["", "Framework", "other", None, [], {}])
# 非法模式必须在创建运行目录和 Job 前拒绝。
def test_invalid_mode(manager, package, monkeypatch, mode):
    job = Mock()
    monkeypatch.setattr(nm, "_WindowsJob", job)
    for action in (manager.inspect, manager.start):
        result = action(*map(str, package), mode=mode)
        assert result == {"ok": False, "error": "invalid_mode", "mode": mode}
    assert manager.status()["mode"] == "shell"
    assert not manager._root.exists()
    job.assert_not_called()
    nm.subprocess.Popen.assert_not_called()


# Framework 遵循官方 argv/env，在创建 Job 前记录模式且保留自有树清理。
def test_start_framework_arguments_environment_and_lifecycle(
    manager, framework_package, monkeypatch
):
    for key in ("NAPCAT_PATCH_PACKAGE", "NAPCAT_LOAD_PATH", "NAPCAT_WINDOW_BYPASS"):
        monkeypatch.setenv(key, "must-not-inherit")

    # Job 构造失败路径也必须能够识别当前模式。
    def create_job():
        assert manager.status()["mode"] == "framework"
        return FakeJob()

    monkeypatch.setattr(nm, "_WindowsJob", create_job)
    source, qq = framework_package
    result = manager.start(str(source), str(qq), "framework")
    assert result["ok"] and result["mode"] == "framework" and result["running"]
    call = nm.subprocess.Popen.call_args
    runtime = Path(call.kwargs["cwd"])
    assert call.args[0] == [
        str(runtime / "napimain.exe"),
        str(qq),
        str(runtime / "napiloader.dll"),
        (runtime / "nativeLoader.cjs").as_posix(),
    ]
    env = call.kwargs["env"]
    assert {key: value for key, value in env.items() if key.startswith("NAPCAT_")} == {
        "NAPCAT_INJECT_PATH": str(runtime / "napiloader.dll"),
        "NAPCAT_LAUNCHER_PATH": str(runtime / "napimain.exe"),
        "NAPCAT_MAIN_PATH": str(runtime / "nativeLoader.cjs"),
    }
    assert call.kwargs["creationflags"] & 4
    assert manager._job.attached is manager._process
    assert not (runtime / "config/private.txt").exists()
    manager._process.poll.return_value = 0
    assert manager.status()["running"]
    job = manager._job
    assert manager.stop()["mode"] == "framework"
    assert job.terminated and job.closed and not runtime.exists()
    assert (source / "config/private.txt").read_text() == "original-private"


@pytest.mark.parametrize(
    "package_name,mode,next_mode",
    [("package", "shell", "framework"), ("framework_package", "framework", "shell")],
)
# 运行中的实例直接拒绝切换模式，不替换 Job 或现有运行副本。
def test_running_instance_rejects_mode_change(
    manager, request, package_name, mode, next_mode
):
    package = request.getfixturevalue(package_name)
    assert manager.start(*map(str, package), mode=mode)["ok"]
    job, runtime = manager._job, manager._runtime
    result = manager.start(*map(str, package), mode=next_mode)
    assert result["error"] == "already_managed" and result["mode"] == mode
    assert manager._job is job and manager._runtime == runtime
    assert not job.terminated
    assert nm.subprocess.Popen.call_count == 1


# 私有副本不含原配置缓存，启动参数无 bat/提权/自动登录，状态无密钥。
def test_start_isolated_copy_and_environment(manager, package, monkeypatch):
    source, qq = package
    for key in (
        "NAPCAT_QUICK_ACCOUNT",
        "NAPCAT_PASSWORD",
        "NAPCAT_PASSWORD_MD5",
        "NAPCAT_WEBUI_SECRET",
        "WEBUI_SECRET",
    ):
        monkeypatch.setenv(key, "must-not-inherit")
    result = manager.start(str(source), str(qq))
    assert result["ok"] and result["running"] and result["managed"]
    assert result["mode"] == "shell"
    call = nm.subprocess.Popen.call_args
    args = call.args[0]
    options = call.kwargs
    runtime = Path(options["cwd"])
    assert runtime.is_relative_to(manager._root)
    assert args == [
        str(runtime / "NapCatWinBootMain.exe"),
        str(qq),
        str(runtime / "NapCatWinBootHook.dll"),
    ]
    assert options["creationflags"] & 4
    assert options["stdout"] == nm.subprocess.DEVNULL
    assert not any(v == "must-not-inherit" for v in options["env"].values())
    assert options["env"]["NAPCAT_MAIN_PATH"] == str(runtime / "napcat.mjs")
    assert not (runtime / "logs").exists()
    assert not (runtime / "config/private.txt").exists()
    assert not (runtime / "cache/private.txt").exists()
    config = json.loads((runtime / "config/webui.json").read_text())
    assert config["host"] == "127.0.0.1"
    assert config["autoLoginAccount"] == ""
    assert len(config["token"]) >= 32
    assert config["token"] not in json.dumps(result)
    for folder in ("config", "logs", "cache"):
        assert (source / folder / "private.txt").read_text() == "original-private"


# 启动器早退仍保持管理态，stop 仅回收自有 Job。
def test_loader_exit_does_not_lose_children(manager, package):
    assert manager.start(*map(str, package))["ok"]
    job = manager._job
    manager._process.poll.return_value = 0
    state = manager.status()
    assert state["running"] and state["launcher_exited"]
    assert manager.start(*map(str, package))["error"] == "already_managed"
    assert nm.subprocess.Popen.call_count == 1
    assert manager.stop()["phase"] == "stopped"
    assert job.terminated and job.closed
    manager.stop()
    manager._opener.open.assert_not_called()


@pytest.mark.parametrize("relationship", ["same", "source_parent", "runtime_parent"])
# 原始包与运行根目录不得重叠。
def test_reject_overlapping_directories(manager, package, relationship):
    source, qq = package
    manager._root = {
        "same": source,
        "source_parent": source / "runtime",
        "runtime_parent": source.parent,
    }[relationship]
    assert manager.start(str(source), str(qq))["error"] == "runtime_source_overlap"
    nm.subprocess.Popen.assert_not_called()


# 挂起进程归属失败必须回收，不能恢复未受管理的启动器。
def test_assignment_failure_cleans_suspended_process(manager, package, monkeypatch):
    job = FakeJob()
    job.attach_and_resume = Mock(side_effect=nm._ManagerError("job_assign_failed"))
    monkeypatch.setattr(nm, "_WindowsJob", lambda: job)
    result = manager.start(*map(str, package))
    assert result["error"] == "job_assign_failed"
    assert job.closed
    nm.subprocess.Popen.return_value.kill.assert_called_once()
    nm.subprocess.Popen.return_value.wait.assert_called_once()


# Job 查询失败不得将未知状态当作安全停止并允许再次启动。
def test_job_query_failure_blocks_restart(manager, package):
    manager.start(*map(str, package))
    manager._job.active_count = Mock(side_effect=nm._ManagerError("job_query_failed"))
    assert manager.status()["phase"] == "unknown"
    assert manager.start(*map(str, package))["error"] == "already_managed"
    assert manager.login_status()["error"] == "job_query_failed"


# WebUI 使用官方 hash 登录，普通结果不泄漏原始错误或凭据。
def test_webui_auth_and_safe_login_output(manager, package, monkeypatch):
    manager.start(*map(str, package))
    request = Mock(
        side_effect=[
            {"code": 0, "data": {"Credential": "private-credential"}},
            {
                "code": 0,
                "data": {
                    "isLogin": True,
                    "loginError": "raw-token-or-log",
                    "token": "raw-token",
                },
            },
        ]
    )
    monkeypatch.setattr(manager, "_request", request)
    state = manager.login_status()
    assert state["phase"] == "ready" and state["is_login"]
    expected = hashlib.sha256((manager._token + ".napcat").encode()).hexdigest()
    assert request.call_args_list[0].args[2] == {"hash": expected}
    assert request.call_args_list[1].args[3] == "private-credential"
    assert "private-credential" not in json.dumps(state)
    assert "raw-token" not in json.dumps(state)


# 使用私有 PNG 输出二维码，过期或刷新后绝不继续返回旧图片。
def test_qr_image_expiry_and_refresh(manager, package, monkeypatch):
    manager.start(*map(str, package))
    qr = manager._runtime / "cache/qrcode.png"
    qr.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    now = time.time() + 1
    os.utime(qr, (now, now))
    webui = Mock(
        return_value={"isLogin": False, "qrcodeurl": "https://txz.qq.com/p?k=x"}
    )
    monkeypatch.setattr(manager, "_webui", webui)
    state = manager.login_status()
    assert state["phase"] == "qr_ready"
    assert state["qr_image"].startswith("data:image/png;base64,")
    old = time.time() - nm._QR_TTL - 10
    os.utime(qr, (old, old))
    state = manager.login_status()
    assert state["qr_expired"] and state["qrcode_url"] == ""
    assert state["qr_image"] == ""
    assert manager.refresh_qr()["qr_image"] == ""
    assert any(c.args[0] == "/QQLogin/RefreshQRcode" for c in webui.call_args_list)


@pytest.mark.parametrize(
    "url",
    [
        "http://txz.qq.com/a",
        "https://txz.qq.com.evil.test/a",
        "https://user@txz.qq.com/a",
        "https://txz.qq.com:8443/a",
        "file:///secret",
        "https://txz.qq.com/\\evil",
    ],
)
# 扫码入口不允许任意主机、端口和凭据。
def test_qr_url_allowlist(manager, url):
    assert manager._qr_url(url) == ""


# 拒绝 oversized 和非 PNG 图片。
def test_qr_rejects_invalid_and_large_images(manager, package, monkeypatch):
    manager.start(*map(str, package))
    monkeypatch.setattr(manager, "_webui", lambda *a: {"isLogin": False})
    qr = manager._runtime / "cache/qrcode.png"
    for raw in (b"not-png", b"\x89PNG\r\n\x1a\n" + b"x" * nm._MAX_QR):
        qr.write_bytes(raw)
        future = time.time() + 1
        os.utime(qr, (future, future))
        assert manager.login_status()["phase"] == "qr_invalid"
        assert not manager.login_status()["qr_image"]


# 连接仅追加专用服务并复用，原配置和其他服务不受影响。
@pytest.mark.parametrize(
    "package_name,mode", [("package", "shell"), ("framework_package", "framework")]
)
def test_connect_preserves_config_reuses_server_and_verifies_account(
    manager, request, monkeypatch, package_name, mode
):
    package = request.getfixturevalue(package_name)
    manager.start(*map(str, package), mode=mode)
    original = {
        "network": {
            "httpServers": [{"name": "other", "port": 1234}],
            "websocketServers": [{"enable": True}],
        },
        "extra": {"keep": True},
    }
    saved = []

    # 模拟 WebUI 登录、账号和配置端点。
    def webui(path, payload=None):
        if path.endswith("CheckLoginStatus"):
            return {"isLogin": True}
        if path.endswith("GetQQLoginInfo"):
            return {"uin": "12345"}
        if path.endswith("GetConfig"):
            return original
        if path.endswith("SetConfig"):
            saved.append(json.loads(payload["config"]))
            return None
        raise AssertionError(path)

    monkeypatch.setattr(manager, "_webui", webui)
    onebot = Mock(
        return_value={"status": "ok", "retcode": 0, "data": {"user_id": 12345}}
    )
    monkeypatch.setattr(manager, "_request", onebot)
    first = manager.connect_favorites("12345")
    assert first["ok"] and first["account"] == "12345"
    assert len(saved) == 1
    assert len(original["network"]["httpServers"]) == 1
    assert saved[0]["extra"] == original["extra"]
    assert saved[0]["network"]["websocketServers"] == [{"enable": True}]
    server = saved[0]["network"]["httpServers"][1]
    assert server["host"] == "127.0.0.1" and not server["enableCors"]
    assert server["token"] == first["token"]
    assert manager.connect_favorites("12345") == first
    assert len(saved) == 1
    assert onebot.call_count == 2
    assert first["token"] not in json.dumps(manager.status())
    assert manager.connect_favorites("99999")["error"] == "account_mismatch"
    onebot.return_value["data"]["user_id"] = 99999
    result = manager.connect_favorites("12345")
    assert result["error"] == "onebot_account_mismatch" and "token" not in result


# 未启动和未登录时不创建 HTTP 服务，不执行自动登录。
def test_connect_requires_owned_online_instance(manager, package, monkeypatch):
    assert manager.connect_favorites("12345")["error"] == "not_running"
    assert manager.connect_favorites(12345)["error"] == "expected_account_required"
    manager.start(*map(str, package))
    webui = Mock(return_value={"isLogin": False})
    monkeypatch.setattr(manager, "_webui", webui)
    assert manager.connect_favorites("12345")["error"] == "login_not_ready"
    assert [call.args[0] for call in webui.call_args_list] == [
        "/QQLogin/CheckLoginStatus"
    ]


# HTTP 错误只回安全代码，重定向和超大响应不继续处理。
def test_http_errors_and_bounds(manager):
    manager._opener.open.side_effect = urllib.error.HTTPError(
        "http://127.0.0.1", 302, "secret-token", {}, io.BytesIO(b"secret")
    )
    with pytest.raises(nm._ManagerError, match="^http_302$"):
        manager._request("http://127.0.0.1", "/api/test", {})
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"x" * (nm._MAX_RESPONSE + 1)
    manager._opener.open.side_effect = None
    manager._opener.open.return_value = response
    with pytest.raises(nm._ManagerError, match="response_too_large"):
        manager._request("http://127.0.0.1", "/api/test", {})
    assert manager._opener.open.call_args.kwargs["timeout"] == 2
    assert nm._NoRedirect().redirect_request(None, None, 302, "", {}, "") is None


# 连接中的配置更新持有生命周期锁，stop 必须等待连接退出。
def test_stop_serializes_with_connection_config(manager, package, monkeypatch):
    manager.start(*map(str, package))
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    results = []
    job = manager._job

    # 在配置读取阶段挂起，检验 stop 不能提前终止进程树。
    def webui(path, payload=None):
        if path.endswith("CheckLoginStatus"):
            return {"isLogin": True}
        if path.endswith("GetQQLoginInfo"):
            return {"uin": "12345"}
        if path.endswith("GetConfig"):
            entered.set()
            assert release.wait(3)
            return {"network": {"httpServers": []}}
        if path.endswith("SetConfig"):
            assert not job.terminated
            return None
        raise AssertionError(path)

    # stop 返回后通知测试线程。
    def stop():
        results.append(manager.stop())
        stopped.set()

    monkeypatch.setattr(manager, "_webui", webui)
    monkeypatch.setattr(
        manager,
        "_request",
        lambda *a: {"status": "ok", "retcode": 0, "data": {"user_id": 12345}},
    )
    connection = threading.Thread(
        target=lambda: results.append(manager.connect_favorites("12345"))
    )
    stopper = threading.Thread(target=stop)
    connection.start()
    assert entered.wait(3)
    stopper.start()
    try:
        assert not stopped.wait(0.05)
        assert not job.terminated
    finally:
        release.set()
        connection.join(3)
        stopper.join(3)
    assert not connection.is_alive() and not stopper.is_alive()
    assert len(results) == 2 and all(result["ok"] for result in results)
    assert job.terminated and not manager.status()["managed"]


# 构造 HTTP opener 时强制禁用代理，并安装重定向拒绝器。
def test_http_opener_disables_system_proxy(tmp_path, monkeypatch):
    build = Mock()
    monkeypatch.setattr(nm.urllib.request, "build_opener", build)
    nm.NapCatManager(tmp_path / "runtime")
    proxy, redirect = build.call_args.args
    assert proxy.proxies == {}
    assert isinstance(redirect, nm._NoRedirect)


# 正常停止只删除本次 session，发布源和其他私有目录保持原样。
def test_stop_removes_only_owned_session(manager, package):
    manager.start(*map(str, package))
    runtime = manager._runtime
    unrelated = manager._root / "session-not-owned"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    assert manager.stop()["ok"]
    assert not runtime.exists()
    assert (unrelated / "keep").read_text() == "keep"
    assert (package[0] / "config/private.txt").read_text() == "original-private"


# 复制发布包中途失败，无进程时立即回收已复制的局部副本。
def test_copy_failure_removes_partial_session(manager, package, monkeypatch):
    # 构造确实留下文件的复制失败。
    def fail_copy(source, destination, **kwargs):
        (destination / "partial").write_text("partial")
        raise OSError("copy failure")

    monkeypatch.setattr(nm.shutil, "copytree", fail_copy)
    assert manager.start(*map(str, package))["error"] == "start_failed"
    assert not list(manager._root.glob("session-*"))
    assert manager._runtime is None
    nm.subprocess.Popen.assert_not_called()


# 删除失败必须明确暴露 pending，保留目录并允许 stop 重试。
def test_cleanup_failure_is_visible_and_retryable(manager, package, monkeypatch):
    manager.start(*map(str, package))
    runtime = manager._runtime
    remove = nm.shutil.rmtree
    mocked = Mock(side_effect=PermissionError("busy"))
    monkeypatch.setattr(nm.shutil, "rmtree", mocked)
    result = manager.stop()
    assert result["error"] == "cleanup_pending"
    assert manager.status()["cleanup_pending"]
    assert runtime.exists() and manager._runtime == runtime
    mocked.assert_called_once_with(runtime)
    monkeypatch.setattr(nm.shutil, "rmtree", remove)
    assert manager.stop()["ok"]
    assert not runtime.exists() and manager._runtime is None


# 启动失败后删除失败也需保留 session 供 stop 重试。
def test_start_failure_cleanup_pending(manager, package, monkeypatch):
    remove = nm.shutil.rmtree
    monkeypatch.setattr(nm.shutil, "copytree", Mock(side_effect=OSError("copy")))
    monkeypatch.setattr(nm.shutil, "rmtree", Mock(side_effect=PermissionError("busy")))
    assert manager.start(*map(str, package))["error"] == "cleanup_pending"
    runtime = manager._runtime
    assert runtime.exists()
    monkeypatch.setattr(nm.shutil, "rmtree", remove)
    assert manager.stop()["ok"] and not runtime.exists()


# 自有进程树尚未退出时不得删除 session。
def test_live_process_tree_never_deleted(manager, package, monkeypatch):
    manager.start(*map(str, package))
    runtime = manager._runtime
    manager._job.terminate = Mock()
    monkeypatch.setattr(nm.time, "monotonic", Mock(side_effect=[0, 4]))
    remove = Mock()
    monkeypatch.setattr(nm.shutil, "rmtree", remove)
    assert manager.stop()["error"] == "stop_failed"
    assert runtime.exists() and manager._runtime == runtime
    remove.assert_not_called()


# Job 计数归零但 launcher 尚未成功 wait 时仍保留副本，之后可以重试。
def test_launcher_wait_timeout_keeps_session(manager, package, monkeypatch):
    manager.start(*map(str, package))
    runtime = manager._runtime
    manager._process.wait.side_effect = nm.subprocess.TimeoutExpired("fake", 1)
    remove = nm.shutil.rmtree
    mocked = Mock()
    monkeypatch.setattr(nm.shutil, "rmtree", mocked)
    assert manager.stop()["error"] == "stop_failed"
    assert runtime.exists()
    mocked.assert_not_called()
    manager._process.wait.side_effect = None
    monkeypatch.setattr(nm.shutil, "rmtree", remove)
    assert manager.stop()["ok"] and not runtime.exists()


# 删除前拒绝 session 或内部成员被替换为链接/junction。
@pytest.mark.parametrize("linked", ["session", "child"])
def test_cleanup_rejects_links(manager, package, monkeypatch, linked):
    manager.start(*map(str, package))
    runtime = manager._runtime
    target = runtime if linked == "session" else runtime / "napcat.mjs"
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda path: path == target or original(path)
    )
    remove = Mock()
    monkeypatch.setattr(nm.shutil, "rmtree", remove)
    assert manager.stop()["error"] == "cleanup_pending"
    remove.assert_not_called()
