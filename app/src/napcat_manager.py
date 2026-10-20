import base64
import copy
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

_FILES = {
    "launcher": "NapCatWinBootMain.exe",
    "hook": "NapCatWinBootHook.dll",
    "loader": "loadNapCat.js",
    "package": "qqnt.json",
    "main": "napcat.mjs",
}
_FRAMEWORK_FILES = {
    "launcher": "napimain.exe",
    "hook": "napiloader.dll",
    "loader": "nativeLoader.cjs",
    "main": "napcat.mjs",
}
_MAX_RESPONSE = 2 * 1024 * 1024
_MAX_QR = 512 * 1024
_QR_TTL = 120


class _ManagerError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 禁止将凭据带到重定向目标。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _WindowsJob:
    # 配置关闭句柄时终止整个自有进程树，禁止子进程脱离。
    def __init__(self):
        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("minimum", ctypes.c_size_t),
                ("maximum", ctypes.c_size_t),
                ("active_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "AssignProcessToJobObject": (
                [wintypes.HANDLE, wintypes.HANDLE],
                wintypes.BOOL,
            ),
            "QueryInformationJobObject": (
                [
                    wintypes.HANDLE,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    wintypes.DWORD,
                    ctypes.c_void_p,
                ],
                wintypes.BOOL,
            ),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes = args
            function.restype = result
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise _ManagerError("job_create_failed")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000
        if not self._kernel.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise _ManagerError("job_config_failed")

    # Popen 创建挂起进程后先加入 Job，再恢复，消除子进程逃逸窗口。
    def attach_and_resume(self, process):
        handle = wintypes.HANDLE(int(process._handle))
        if not self._kernel.AssignProcessToJobObject(self._handle, handle):
            raise _ManagerError("job_assign_failed")
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = wintypes.LONG
        if resume(handle) < 0:
            raise _ManagerError("process_resume_failed")

    # Job 统计包含启动器退出后仍存活的 QQ 子进程。
    def active_count(self):
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_int64 * 4),
                ("faults", wintypes.DWORD),
                ("total", wintypes.DWORD),
                ("active", wintypes.DWORD),
                ("terminated", wintypes.DWORD),
            ]

        value = Accounting()
        if not self._kernel.QueryInformationJobObject(
            self._handle, 1, ctypes.byref(value), ctypes.sizeof(value), None
        ):
            raise _ManagerError("job_query_failed")
        return value.active

    # 只终止属于该 Job 的进程。
    def terminate(self):
        if not self._kernel.TerminateJobObject(self._handle, 1):
            raise _ManagerError("job_stop_failed")

    # 释放句柄触发内核兜底回收。
    def close(self):
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise _ManagerError("job_close_failed")
            self._handle = None

    # 应用对象被释放时仍回收自有树。
    def __del__(self):
        if getattr(self, "_handle", None):
            self._kernel.CloseHandle(self._handle)


# 预留随机回环端口；实际监听失败由后续 HTTP 探测报告。
def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# 拒绝复制符号链接和 Windows junction，避免私有副本写入外部目录。
def _copy_ignore(directory, names):
    ignored = []
    for name in names:
        path = Path(directory) / name
        if name.lower() in {"config", "logs", "log", "cache"}:
            ignored.append(name)
        elif path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise _ManagerError("package_contains_links")
    return ignored


class NapCatManager:
    # runtime_dir 必须由应用传入 data_dir 下的私有父目录，测试传入临时目录。
    def __init__(self, runtime_dir):
        self._root = Path(runtime_dir).resolve()
        self._lock = threading.RLock()
        self._job = None
        self._mode = "shell"
        self._process = None
        self._runtime = None
        self._session_identity = None
        self._endpoint = ""
        self._token = ""
        self._credential = ""
        self._favorites = None
        self._qr_after = 0
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    # 按模式只读检查原始发布目录和 QQ 程序路径。
    def inspect(self, directory, qq_path="", mode="shell"):
        if mode not in ("shell", "framework"):
            return {"ok": False, "error": "invalid_mode", "mode": mode}
        required = _FRAMEWORK_FILES if mode == "framework" else _FILES
        try:
            if not directory:
                raise ValueError
            source = Path(directory).resolve(strict=True)
            if not source.is_dir() or str(source).startswith("\\\\"):
                raise ValueError
            files = {key: (source / name).is_file() for key, name in required.items()}
            missing = [name for key, name in required.items() if not files[key]]
            qq = None
            if qq_path:
                qq = Path(qq_path).resolve(strict=True)
            else:
                for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
                    base = os.environ.get(variable)
                    candidate = Path(base) / "Tencent/QQNT/QQ.exe" if base else None
                    if candidate and candidate.is_file():
                        qq = candidate.resolve()
                        break
            valid_qq = bool(
                qq
                and qq.is_file()
                and qq.name.lower() == "qq.exe"
                and not str(qq).startswith("\\\\")
            )
            error = "missing_package_files" if missing else ""
            if not valid_qq and not error:
                error = "qq_not_found"
            return {
                "ok": not error,
                "mode": mode,
                "error": error,
                "directory": str(source),
                "qq_path": str(qq) if valid_qq else "",
                "files": files,
                "missing": missing,
                "supported": sys.platform == "win32",
            }
        except (OSError, ValueError, TypeError):
            return {
                "ok": False,
                "error": "invalid_directory_or_qq_path",
                "mode": mode,
            }

    # 复制发布包并先创建受控 WebUI 配置，再启动挂起的 launcher。
    def start(self, directory, qq_path="", mode="shell"):
        with self._lock:
            if self._job or self._runtime:
                current = self.status()
                if self._job and (current["running"] or not current["ok"]):
                    return {**current, "ok": False, "error": "already_managed"}
                stopped = self.stop()
                if not stopped["ok"]:
                    return stopped
            checked = self.inspect(directory, qq_path, mode)
            if not checked["ok"]:
                return checked
            if sys.platform != "win32":
                return {"ok": False, "error": "unsupported_platform", "mode": mode}
            self._mode = mode
            required = _FRAMEWORK_FILES if mode == "framework" else _FILES
            process = None
            job = None
            try:
                source = Path(checked["directory"])
                if (
                    source == self._root
                    or source in self._root.parents
                    or self._root in source.parents
                ):
                    raise _ManagerError("runtime_source_overlap")
                self._root.mkdir(parents=True, exist_ok=True)
                runtime = Path(tempfile.mkdtemp(prefix="session-", dir=self._root))
                self._runtime = runtime
                stat = runtime.stat()
                self._session_identity = (stat.st_dev, stat.st_ino)
                shutil.copytree(
                    source, runtime, dirs_exist_ok=True, ignore=_copy_ignore
                )
                config = runtime / "config"
                config.mkdir(mode=0o700)
                (runtime / "cache").mkdir(mode=0o700)
                port = _free_port()
                token = secrets.token_urlsafe(32)
                (config / "webui.json").write_text(
                    json.dumps(
                        {
                            "host": "127.0.0.1",
                            "port": port,
                            "token": token,
                            "autoLoginAccount": "",
                            "disableWebUI": False,
                        }
                    ),
                    encoding="utf-8",
                )
                (config / "onebot11.json").write_text("{}", encoding="utf-8")
                env = {
                    k: v
                    for k, v in os.environ.items()
                    if not k.upper().startswith("NAPCAT_")
                    and k.upper() != "WEBUI_SECRET"
                }
                paths = (
                    {
                        "NAPCAT_INJECT_PATH": required["hook"],
                        "NAPCAT_LAUNCHER_PATH": required["launcher"],
                        "NAPCAT_MAIN_PATH": required["loader"],
                    }
                    if mode == "framework"
                    else {
                        "NAPCAT_PATCH_PACKAGE": required["package"],
                        "NAPCAT_LOAD_PATH": required["loader"],
                        "NAPCAT_INJECT_PATH": required["hook"],
                        "NAPCAT_LAUNCHER_PATH": required["launcher"],
                        "NAPCAT_MAIN_PATH": required["main"],
                    }
                )
                for key, filename in paths.items():
                    env[key] = str(runtime / filename)
                args = [
                    str(runtime / required["launcher"]),
                    checked["qq_path"],
                    str(runtime / required["hook"]),
                ]
                if mode == "framework":
                    args.append((runtime / required["loader"]).as_posix())
                job = _WindowsJob()
                process = subprocess.Popen(
                    args,
                    cwd=str(runtime),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=0x00000004 | 0x08000000,
                    close_fds=True,
                )
                job.attach_and_resume(process)
                self._process, self._job, self._runtime = process, job, runtime
                self._endpoint = f"http://127.0.0.1:{port}"
                self._token, self._credential, self._favorites = token, "", None
                self._qr_after = time.time()
                return self.status()
            except Exception as exc:
                self._job, self._process = job, process
                try:
                    if process and process.poll() is None:
                        process.kill()
                except (OSError, subprocess.TimeoutExpired, _ManagerError):
                    return {
                        "ok": False,
                        "error": "start_cleanup_failed",
                        "mode": self._mode,
                        "managed": True,
                    }
                stopped = self.stop()
                if not stopped["ok"]:
                    return stopped
                code = str(exc) if isinstance(exc, _ManagerError) else "start_failed"
                return {
                    "ok": False,
                    "error": code,
                    "managed": False,
                    "mode": self._mode,
                }

    # 仅删除本对象创建且身份未变的单个 session，拒绝链接和目录跳转。
    def _cleanup_session(self):
        path = self._runtime
        if path is None:
            return True
        try:
            if (
                path.parent != self._root
                or not path.name.startswith("session-")
                or self._session_identity is None
                or self._root.resolve() != self._root
            ):
                return False
            for ancestor in (self._root, *self._root.parents):
                if (
                    ancestor.is_symlink()
                    or getattr(ancestor, "is_junction", lambda: False)()
                ):
                    return False
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                return False
            if not path.exists():
                self._runtime = self._session_identity = None
                return True
            stat = path.stat()
            if (stat.st_dev, stat.st_ino) != self._session_identity:
                return False
            for directory, folders, files in os.walk(path, followlinks=False):
                for name in folders + files:
                    child = Path(directory) / name
                    if (
                        child.is_symlink()
                        or getattr(child, "is_junction", lambda: False)()
                    ):
                        return False
            shutil.rmtree(path)
            self._runtime = self._session_identity = None
            return True
        except OSError:
            return False

    # 以 Job 活跃数判断运行态，不用启动器退出码代替子进程状态。
    def status(self):
        with self._lock:
            try:
                count = self._job.active_count() if self._job else 0
                return {
                    "ok": not (self._runtime and not self._job),
                    "mode": self._mode,
                    "error": (
                        "cleanup_pending" if self._runtime and not self._job else ""
                    ),
                    "cleanup_pending": bool(self._runtime and not self._job),
                    "managed": self._job is not None,
                    "running": count > 0,
                    "phase": "running" if count else "stopped",
                    "pid": self._process.pid if self._process else None,
                    "active_processes": count,
                    "launcher_exited": bool(
                        self._process and self._process.poll() is not None
                    ),
                }
            except _ManagerError:
                return {
                    "ok": False,
                    "error": "job_query_failed",
                    "mode": self._mode,
                    "managed": True,
                    "running": True,
                    "phase": "unknown",
                }

    # 停止与连接配置串行，禁止按名字查杀任何 QQ 实例。
    def stop(self):
        with self._lock:
            try:
                if self._job:
                    self._job.terminate()
                    deadline = time.monotonic() + 3
                    while self._job.active_count():
                        if time.monotonic() >= deadline:
                            raise _ManagerError("stop_timeout")
                        time.sleep(0.03)
                    if self._process:
                        self._process.wait(timeout=1)
                    self._job.close()
                elif self._process:
                    self._process.wait(timeout=1)
                self._job = self._process = None
                self._token = self._credential = self._endpoint = ""
                self._favorites = None
                if not self._cleanup_session():
                    return {
                        "ok": False,
                        "error": "cleanup_pending",
                        "mode": self._mode,
                        "cleanup_pending": True,
                        "managed": False,
                        "running": False,
                    }
                return self.status()
            except (OSError, subprocess.TimeoutExpired, _ManagerError):
                return {
                    "ok": False,
                    "error": "stop_failed",
                    "managed": True,
                    "mode": self._mode,
                }

    # HTTP 仅使用本实例生成的回环端点，禁代理、重定向和无限响应。
    def _request(self, endpoint, path, payload, credential=""):
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        request = urllib.request.Request(
            endpoint + path,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=2) as response:
                raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise _ManagerError("response_too_large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise _ManagerError(f"http_{code}") from None
        except (OSError, ValueError):
            raise _ManagerError("webui_unavailable") from None

    # WebUI 凭据只保留在内存，不透传远端错误文本。
    def _webui(self, path, payload=None):
        if not self._credential:
            digest = hashlib.sha256((self._token + ".napcat").encode()).hexdigest()
            auth = self._request(self._endpoint, "/api/auth/login", {"hash": digest})
            data = auth.get("data")
            credential = data.get("Credential") if isinstance(data, dict) else None
            if auth.get("code") != 0 or not isinstance(credential, str):
                raise _ManagerError("webui_auth_failed")
            if not credential or "\r" in credential or "\n" in credential:
                raise _ManagerError("webui_auth_failed")
            self._credential = credential
        result = self._request(
            self._endpoint, "/api" + path, payload or {}, self._credential
        )
        if result.get("code") != 0:
            self._credential = ""
            raise _ManagerError("webui_request_failed")
        return result.get("data")

    # 所有管理请求先确认自有进程树仍活跃。
    def _require_running(self):
        state = self.status()
        if not state["ok"]:
            raise _ManagerError("job_query_failed")
        if not state["managed"] or not state["running"]:
            raise _ManagerError("not_running")

    # 只读私有缓存图片，过期、过大或非法 PNG 均不下发。
    def _qr_image(self):
        path = self._runtime / "cache/qrcode.png"
        try:
            if not path.resolve().is_relative_to(self._runtime):
                return "", "qr_invalid"
            stat = path.stat()
            if stat.st_mtime < self._qr_after or time.time() - stat.st_mtime > _QR_TTL:
                return "", "qr_expired"
            with path.open("rb") as stream:
                raw = stream.read(_MAX_QR + 1)
            if len(raw) > _MAX_QR or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                return "", "qr_invalid"
            return "data:image/png;base64," + base64.b64encode(raw).decode(), ""
        except OSError:
            return "", "qr_pending"

    # URL 只允许 QQ 官方 HTTPS 扫码入口，不发起外部抓取。
    def _qr_url(self, value):
        try:
            parsed = urllib.parse.urlsplit(value if isinstance(value, str) else "")
            if (
                parsed.scheme == "https"
                and parsed.hostname == "txz.qq.com"
                and parsed.port in (None, 443)
                and not parsed.username
                and not parsed.password
                and not any(c.isspace() for c in value)
                and "\\" not in value
            ):
                return value
        except ValueError:
            pass
        return ""

    # 登录信息白名单输出，禁止原始扫码日志和凭据回传。
    def login_status(self):
        with self._lock:
            state = {
                "ok": False,
                "error": "",
                "managed": self._job is not None,
                "is_login": False,
                "phase": "starting",
                "qr_image": "",
                "qrcode_url": "",
                "qr_expired": False,
            }
            try:
                self._require_running()
                data = self._webui("/QQLogin/CheckLoginStatus")
                if not isinstance(data, dict) or type(data.get("isLogin")) is not bool:
                    raise _ManagerError("invalid_login_status")
                state.update(ok=True, is_login=data["isLogin"])
                if data["isLogin"]:
                    state["phase"] = "ready"
                elif data.get("isOffline") is True:
                    state["phase"] = "offline"
                else:
                    image, error = self._qr_image()
                    state.update(
                        phase="qr_ready" if image else error,
                        qr_image=image,
                        qr_expired=error == "qr_expired",
                        qrcode_url=self._qr_url(data.get("qrcodeurl", "")),
                    )
                    if error == "qr_expired":
                        state["qrcode_url"] = ""
                return state
            except _ManagerError as exc:
                return {**state, "error": str(exc)}

    # 刷新后屏蔽旧缓存，等待 NapCat 生成新图片。
    def refresh_qr(self):
        with self._lock:
            try:
                self._require_running()
                current = self.login_status()
                if not current["ok"] or current["is_login"]:
                    return {**current, "ok": False, "error": "login_not_refreshable"}
                self._qr_after = time.time()
                self._webui("/QQLogin/RefreshQRcode")
                return self.login_status()
            except _ManagerError as exc:
                return {"ok": False, "error": str(exc), "qr_image": ""}

    # 仅供桥接层内部使用：成功结果含 token，绝不能原样返回前端。
    def connect_favorites(self, expected_account):
        with self._lock:
            try:
                if (
                    not isinstance(expected_account, str)
                    or not expected_account.isascii()
                    or not expected_account.isdecimal()
                ):
                    raise _ManagerError("expected_account_required")
                self._require_running()
                login = self.login_status()
                if not login["ok"] or not login["is_login"]:
                    raise _ManagerError("login_not_ready")
                info = self._webui("/QQLogin/GetQQLoginInfo")
                if (
                    not isinstance(info, dict)
                    or str(info.get("uin")) != expected_account
                ):
                    raise _ManagerError("account_mismatch")
                if self._favorites and self._favorites["account"] != expected_account:
                    raise _ManagerError("account_mismatch")
                if not self._favorites:
                    config = self._webui("/OB11Config/GetConfig")
                    if not isinstance(config, dict):
                        raise _ManagerError("invalid_ob11_config")
                    config = copy.deepcopy(config)
                    network = config.setdefault("network", {})
                    if not isinstance(network, dict):
                        raise _ManagerError("invalid_ob11_config")
                    servers = network.setdefault("httpServers", [])
                    if not isinstance(servers, list):
                        raise _ManagerError("invalid_ob11_config")
                    port, token = _free_port(), secrets.token_urlsafe(32)
                    servers.append(
                        {
                            "name": "ohmymeme-" + secrets.token_hex(8),
                            "enable": True,
                            "host": "127.0.0.1",
                            "port": port,
                            "token": token,
                            "enableCors": False,
                            "enableWebsocket": False,
                            "messagePostFormat": "array",
                        }
                    )
                    self._webui("/OB11Config/SetConfig", {"config": json.dumps(config)})
                    self._favorites = {
                        "account": expected_account,
                        "endpoint": f"http://127.0.0.1:{port}",
                        "token": token,
                    }
                result = self._request(
                    self._favorites["endpoint"],
                    "/get_login_info",
                    {},
                    self._favorites["token"],
                )
                data = result.get("data")
                if (
                    result.get("status") != "ok"
                    or type(result.get("retcode")) is not int
                    or result["retcode"] != 0
                    or not isinstance(data, dict)
                    or isinstance(data.get("user_id"), bool)
                    or str(data.get("user_id")) != expected_account
                ):
                    raise _ManagerError("onebot_account_mismatch")
                return {"ok": True, **self._favorites}
            except _ManagerError as exc:
                return {"ok": False, "error": str(exc)}
