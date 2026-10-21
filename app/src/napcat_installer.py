import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

_VERSION = "v4.18.38"
_API = "https://api.github.com/repos/NapNeko/NapCatQQ/releases/tags/"
_DOWNLOAD = "https://github.com/NapNeko/NapCatQQ/releases/download/"
_ASSETS = {"shell": "NapCat.Shell.zip", "framework": "NapCat.Framework.zip"}
_REQUIRED = {
    "shell": {
        "NapCatWinBootMain.exe",
        "NapCatWinBootHook.dll",
        "loadNapCat.js",
        "qqnt.json",
        "napcat.mjs",
    },
    "framework": {"napimain.exe", "napiloader.dll", "nativeLoader.cjs", "napcat.mjs"},
}
_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}
_MAX_METADATA = 2 * 1024 * 1024
_MAX_DOWNLOAD = 256 * 1024 * 1024
_MAX_UNPACKED = 1024 * 1024 * 1024
_MAX_FILE = 256 * 1024 * 1024
_MAX_MEMBERS = 20000
_MAX_RATIO = 200
_CHUNK = 64 * 1024
_TIMEOUT = 15
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(10)
}


class _InstallError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 重定向交给调用方逐跳校验。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 限制官方 HTTPS 主机、端口及 URL 解析歧义。
def _check_url(url, hosts):
    try:
        parsed = urllib.parse.urlsplit(url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in hosts
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and not any(ord(c) <= 32 or ord(c) == 127 or c == "\\" for c in url)
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise _InstallError("unsafe_download_url")


# 拒绝私有目录及其祖先上的符号链接或 junction。
def _check_local(path):
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise _InstallError("unsafe_install_directory")


# 统一校验跨平台 ZIP 路径，拒绝 Windows 别名与设备路径。
def _member_path(info):
    name = info.orig_filename
    parts = name.rstrip("/").split("/")
    if (
        not name
        or name != info.filename
        or len(name) > 240
        or "\\" in name
        or any(
            not p
            or p in (".", "..")
            or p.endswith((" ", "."))
            or any(ord(c) < 32 or c in ':<>"|?*' for c in p)
            or p.split(".")[0].upper() in _RESERVED
            for p in parts
        )
    ):
        raise _InstallError("unsafe_zip_path")
    kind = stat.S_IFMT(info.external_attr >> 16)
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
        raise _InstallError("zip_contains_links")
    if kind == stat.S_IFDIR and not info.is_dir():
        raise _InstallError("unsafe_zip_path")
    return Path(*parts)


class NapCatInstaller:
    # 发布包与管理器的运行副本使用独立目录。
    def __init__(self, data_dir):
        self._root = Path(os.path.abspath(data_dir)) / "napcat_packages"
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread = None
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        self._state = {
            "ok": True,
            "active": False,
            "phase": "idle",
            "mode": "shell",
            "version": _VERSION,
            "bytes_done": 0,
            "bytes_total": 0,
            "progress": 0,
            "directory": "",
            "error": "",
            "reused": False,
        }

    # 非阻塞启动一个已知兼容版本的安装任务。
    def start(self, mode="shell", version=_VERSION):
        with self._lock:
            if self._state["active"]:
                return {"ok": False, "error": "installer_busy"}
            if not isinstance(mode, str) or mode not in _ASSETS:
                return {"ok": False, "error": "invalid_mode"}
            if version != _VERSION:
                return {"ok": False, "error": "unsupported_version"}
            self._cancel.clear()
            self._state.update(
                ok=True,
                active=True,
                phase="metadata",
                mode=mode,
                version=version,
                bytes_done=0,
                bytes_total=0,
                progress=0,
                directory="",
                error="",
                reused=False,
            )
            self._thread = threading.Thread(
                target=self._run, args=(mode, version), daemon=True
            )
            try:
                self._thread.start()
            except RuntimeError:
                self._state.update(
                    active=False, ok=False, phase="error", error="start_failed"
                )
            return self.progress()

    # 返回不含下载 URL 和原始网络异常的状态副本。
    def progress(self):
        with self._lock:
            state = dict(self._state)
            state.update(
                running=state["active"],
                state=state["phase"],
                downloaded=state["bytes_done"],
                total=state["bytes_total"],
            )
            return state

    # 取消后由 worker 清理临时目录，阻塞读取至多等待网络超时。
    def cancel(self):
        with self._lock:
            if self._state["active"]:
                self._cancel.set()
            return self.progress()

    # 在耗时操作和最终提交前检查取消。
    def _checkpoint(self):
        if self._cancel.is_set():
            raise _InstallError("cancelled")

    # 更新任务状态时始终持锁。
    def _update(self, **values):
        with self._lock:
            self._state.update(values)

    # 禁止自动重定向，每个目标均限制在官方域名内。
    def _open(self, url, metadata=False):
        hosts = {"api.github.com"} if metadata else _HOSTS
        for _ in range(6):
            self._checkpoint()
            _check_url(url, hosts)
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "OhMyMeme-NapCatInstaller",
                    "Accept": (
                        "application/vnd.github+json"
                        if metadata
                        else "application/octet-stream"
                    ),
                    "Accept-Encoding": "identity",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            try:
                response = self._opener.open(request, timeout=_TIMEOUT)
            except urllib.error.HTTPError as error:
                try:
                    if not metadata and error.code in (301, 302, 303, 307, 308):
                        location = error.headers.get("Location")
                        if not location:
                            raise _InstallError("invalid_redirect")
                        url = urllib.parse.urljoin(url, location)
                        continue
                    raise _InstallError(f"http_{error.code}") from None
                finally:
                    error.close()
            try:
                _check_url(response.geturl(), hosts)
                if response.status != 200:
                    raise _InstallError("unexpected_http_status")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise _InstallError("unexpected_content_encoding")
                return response
            except Exception:
                response.close()
                raise
        raise _InstallError("too_many_redirects")

    # 只选精确命名的正式发布资产，并强制要求官方 SHA-256 digest。
    def _asset(self, mode, version):
        with self._open(_API + version, metadata=True) as response:
            raw = response.read(_MAX_METADATA + 1)
        self._checkpoint()
        if len(raw) > _MAX_METADATA:
            raise _InstallError("metadata_too_large")
        release = json.loads(raw)
        if (
            not isinstance(release, dict)
            or release.get("tag_name") != version
            or release.get("draft") is not False
            or release.get("prerelease") is not False
            or not isinstance(release.get("assets"), list)
        ):
            raise _InstallError("invalid_release")
        matches = [
            asset
            for asset in release["assets"]
            if isinstance(asset, dict) and asset.get("name") == _ASSETS[mode]
        ]
        if len(matches) != 1:
            raise _InstallError("compatible_asset_missing")
        asset = matches[0]
        digest = asset.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(
            r"sha256:[0-9a-fA-F]{64}", digest
        ):
            raise _InstallError("unverified_release_digest")
        size = asset.get("size")
        if type(size) is not int or not 0 < size <= _MAX_DOWNLOAD:
            raise _InstallError("download_too_large")
        url = asset.get("browser_download_url")
        if url != _DOWNLOAD + version + "/" + _ASSETS[mode]:
            raise _InstallError("unsafe_download_url")
        return url, size, digest[7:].lower()

    # 流式下载、取消、长度及 SHA-256 校验均在写入过程中执行。
    def _download(self, url, archive, size, digest):
        self._update(phase="downloading", bytes_total=size)
        checksum = hashlib.sha256()
        done = 0
        with self._open(url) as response, archive.open("xb") as output:
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) != size):
                raise _InstallError("download_size_mismatch")
            while True:
                self._checkpoint()
                chunk = response.read(_CHUNK)
                self._checkpoint()
                if not chunk:
                    break
                done += len(chunk)
                if done > size or done > _MAX_DOWNLOAD:
                    raise _InstallError("download_too_large")
                checksum.update(chunk)
                output.write(chunk)
                self._update(bytes_done=done, progress=min(90, done * 90 // size))
        if done != size:
            raise _InstallError("download_size_mismatch")
        if checksum.hexdigest() != digest:
            raise _InstallError("sha256_mismatch")

    # 在复用原始 ZIP 前重新校验摘要，避免信任本地完成标记。
    def _verify_archive(self, archive, size, digest):
        _check_local(archive)
        if not archive.is_file() or archive.stat().st_size != size:
            raise _InstallError("existing_install_invalid")
        checksum = hashlib.sha256()
        with archive.open("rb") as source:
            while chunk := source.read(_CHUNK):
                self._checkpoint()
                checksum.update(chunk)
        if checksum.hexdigest() != digest:
            raise _InstallError("existing_install_invalid")

    # 先完整检查 ZIP 目录，再有界解压或逐字节核对已有包。
    def _extract(self, archive, directory, mode, verify=False):
        self._update(phase="verifying" if verify else "extracting", progress=95)
        with zipfile.ZipFile(archive) as source:
            infos = source.infolist()
            if not infos or len(infos) > _MAX_MEMBERS:
                raise _InstallError("zip_member_limit")
            names = set()
            members = []
            total = 0
            for info in infos:
                self._checkpoint()
                relative = _member_path(info)
                key = relative.as_posix().casefold()
                if key in names:
                    raise _InstallError("zip_duplicate_path")
                names.add(key)
                total += info.file_size
                if (
                    info.flag_bits & 1
                    or info.compress_type
                    not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or info.file_size > _MAX_FILE
                    or total > _MAX_UNPACKED
                    or info.file_size > max(1, info.compress_size) * _MAX_RATIO
                ):
                    raise _InstallError("unsafe_zip_size")
                members.append((info, relative))
            files = {p.as_posix() for info, p in members if not info.is_dir()}
            folded_files = {name.casefold() for name in files}
            if not _REQUIRED[mode].issubset(files):
                raise _InstallError("incompatible_package")
            for info, relative in members:
                if any(
                    p.as_posix().casefold() in folded_files
                    for p in relative.parents
                    if p != Path(".")
                ):
                    raise _InstallError("zip_path_conflict")
                target = directory / relative
                _check_local(target)
                if info.is_dir():
                    if not verify:
                        target.mkdir(parents=True, exist_ok=True)
                    continue
                if not verify:
                    target.parent.mkdir(parents=True, exist_ok=True)
                elif not target.is_file() or target.stat().st_size != info.file_size:
                    raise _InstallError("existing_install_invalid")
                count = 0
                with source.open(info) as packed, target.open(
                    "rb" if verify else "xb"
                ) as output:
                    while True:
                        self._checkpoint()
                        chunk = packed.read(_CHUNK)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > info.file_size or count > _MAX_FILE:
                            raise _InstallError("unsafe_zip_size")
                        if verify:
                            if output.read(len(chunk)) != chunk:
                                raise _InstallError("existing_install_invalid")
                        else:
                            output.write(chunk)
                if count != info.file_size:
                    raise _InstallError("invalid_zip")
            if verify:
                actual = set()
                for path in directory.rglob("*"):
                    self._checkpoint()
                    _check_local(path)
                    if path.is_file():
                        actual.add(path.relative_to(directory).as_posix())
                if actual != files:
                    raise _InstallError("existing_install_invalid")

    # 临时目录与最终目录同盘，成功提交前保持安装不可见。
    def _run(self, mode, version):
        try:
            url, size, digest = self._asset(mode, version)
            self._checkpoint()
            _check_local(self._root)
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination = self._root / f"{version}-{mode}-{digest}"
            _check_local(destination)
            reused = destination.exists()
            if reused:
                self._verify_archive(destination / "release.zip", size, digest)
                self._extract(
                    destination / "release.zip", destination / "package", mode, True
                )
            else:
                with tempfile.TemporaryDirectory(
                    prefix=".install-", dir=self._root
                ) as temporary:
                    staging = Path(temporary)
                    bundle = staging / "bundle"
                    bundle.mkdir(mode=0o700)
                    self._download(url, bundle / "release.zip", size, digest)
                    self._extract(bundle / "release.zip", bundle / "package", mode)
                    with self._lock:
                        self._checkpoint()
                        _check_local(destination)
                        os.rename(bundle, destination)
            with self._lock:
                if reused:
                    self._checkpoint()
                self._state.update(
                    ok=True,
                    active=False,
                    phase="done",
                    progress=100,
                    bytes_done=size,
                    bytes_total=size,
                    directory=str(destination / "package"),
                    reused=reused,
                )
        except _InstallError as error:
            self._update(
                ok=False,
                active=False,
                phase="cancelled" if str(error) == "cancelled" else "error",
                error=str(error),
            )
        except (json.JSONDecodeError, UnicodeError, TypeError, ValueError):
            self._update(
                ok=False, active=False, phase="error", error="invalid_metadata"
            )
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError):
            self._update(ok=False, active=False, phase="error", error="invalid_zip")
        except (urllib.error.URLError, TimeoutError):
            self._update(ok=False, active=False, phase="error", error="network_error")
        except OSError:
            self._update(
                ok=False, active=False, phase="error", error="install_io_error"
            )
        except Exception:
            self._update(ok=False, active=False, phase="error", error="install_failed")
