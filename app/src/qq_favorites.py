import copy
import hashlib
import ipaddress
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

_IO_LOCK = threading.Lock()
_MAX_RESPONSE = 16 * 1024 * 1024


class QQFavoritesError(Exception):
    pass


# 仅接受数字形式的本机回环地址，拒绝凭据、查询和重定向入口。
def _endpoint(value):
    try:
        if not isinstance(value, str) or any(c.isspace() for c in value):
            raise ValueError
        parsed = urllib.parse.urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        if (
            parsed.scheme != "http"
            or not address.is_loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "%" in parsed.netloc
        ):
            raise ValueError
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        host = f"[{address}]" if address.version == 6 else str(address)
        return f"http://{host}" + (f":{port}" if port is not None else "")
    except (ValueError, TypeError):
        raise QQFavoritesError("invalid_endpoint") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # 任何重定向均不携带认证信息继续请求。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class NapCatClient:
    # 不使用系统代理，不保存或输出认证信息。
    def __init__(self, endpoint, token, timeout=15):
        self.endpoint = _endpoint(endpoint)
        if not isinstance(token, str) or "\r" in token or "\n" in token:
            raise QQFavoritesError("invalid_token")
        self._token = token
        self._timeout = timeout
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    # 校验 OneBot 外层结果；远端错误文本永不回显。
    def call(self, action, params=None):
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = urllib.request.Request(
            self.endpoint + "/" + action,
            data=json.dumps(params or {}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise QQFavoritesError("response_too_large")
            result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise QQFavoritesError(f"http_{code}") from None
        except (OSError, ValueError):
            raise QQFavoritesError("transport_or_invalid_response") from None
        if not isinstance(result, dict):
            raise QQFavoritesError("invalid_response")
        if result.get("status") != "ok" or type(result.get("retcode")) is not int:
            raise QQFavoritesError("onebot_failed")
        if result["retcode"] != 0:
            raise QQFavoritesError("onebot_failed")
        return result.get("data")


# 错误统一为可序列化的安全代码。
def _failure(code, **fields):
    return {"ok": False, "error": code, **fields}


# 读取同一份字节用于 magic 校验与 MD5；绝不改写源路径。
def _image(path):
    try:
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_file() or str(resolved).startswith("\\\\"):
            raise QQFavoritesError("invalid_file")
        with resolved.open("rb") as stream:
            header = stream.read(16)
            valid = (
                header.startswith(b"\x89PNG\r\n\x1a\n")
                or header.startswith(b"\xff\xd8\xff")
                or header.startswith((b"GIF87a", b"GIF89a", b"BM"))
                or (header[:4] == b"RIFF" and header[8:12] == b"WEBP")
            )
            if not valid:
                raise QQFavoritesError("unsupported_image")
            digest = hashlib.md5(header)
            size = len(header)
            while chunk := stream.read(65536):
                digest.update(chunk)
                size += len(chunk)
        return str(resolved), digest.hexdigest().upper(), size
    except (OSError, ValueError, TypeError):
        raise QQFavoritesError("invalid_file") from None


class QQFavoritesService:
    # 任务状态与取消标志在同一锁下更新。
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "ok": True,
            "error": "",
            "active": False,
            "task_id": "",
            "status": "idle",
            "account": "",
            "operation": "",
            "total": 0,
            "done": 0,
            "items": [],
            "cancel_requested": False,
        }
        self._unconfirmed = set()

    # 校验当前登录账号，写任务必须传入预期账号。
    def _account(self, client, expected):
        data = client.call("get_login_info")
        value = data.get("user_id") if isinstance(data, dict) else None
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise QQFavoritesError("invalid_account")
        account = str(value)
        if not account or not account.isdecimal():
            raise QQFavoritesError("invalid_account")
        if expected and account != expected:
            raise QQFavoritesError("account_mismatch")
        return account

    # 返回原生资源字段，不推断清单是否完整。
    def _favorites(self, client):
        data = client.call("fetch_custom_face_detail", {"count": 5000})
        if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
            raise QQFavoritesError("invalid_favorites")
        ids = [x.get("resId") for x in data]
        if any(not isinstance(value, str) or not value.strip() for value in ids):
            raise QQFavoritesError("invalid_favorites")
        if len(set(ids)) != len(ids):
            raise QQFavoritesError("invalid_favorites")
        return data

    # 读接口与整个写任务互斥，忙时立即返回。
    def _read(self, endpoint, token, expected_account, include_list):
        if not _IO_LOCK.acquire(blocking=False):
            return _failure("busy", account="")
        try:
            client = NapCatClient(endpoint, token)
            account = self._account(client, str(expected_account or ""))
            result = {"ok": True, "error": "", "account": account}
            if include_list:
                result.update(favorites=self._favorites(client), completeness="unknown")
                self._account(client, account)
            return result
        except QQFavoritesError as exc:
            return _failure(str(exc), account="")
        except Exception:
            return _failure("unexpected_error", account="")
        finally:
            _IO_LOCK.release()

    # 查询连接及登录账号。
    def status(self, endpoint, token, expected_account=""):
        return self._read(endpoint, token, expected_account, False)

    # 查询最多 5000 个收藏，完整性始终未知。
    def list_favorites(self, endpoint, token, expected_account=""):
        return self._read(endpoint, token, expected_account, True)

    # 上传文件列表必须由调用方显式提供。
    def start_upload(self, endpoint, token, expected_account, files):
        return self._start(endpoint, token, expected_account, files, "upload")

    # 删除资源列表必须由调用方显式提供。
    def start_delete(self, endpoint, token, expected_account, res_ids):
        return self._start(endpoint, token, expected_account, res_ids, "delete")

    # 先占用读写互斥锁，再启动唯一后台任务。
    def _start(self, endpoint, token, expected_account, values, operation):
        try:
            client = NapCatClient(endpoint, token)
            expected = str(expected_account or "")
            if not expected or not expected.isdecimal():
                return _failure("expected_account_required")
            if not isinstance(values, (list, tuple)) or not values:
                return _failure("empty_or_invalid_items")
            if any(not isinstance(v, str) or not v for v in values):
                return _failure("invalid_item")
            values = list(values)
        except QQFavoritesError as exc:
            return _failure(str(exc))
        if not _IO_LOCK.acquire(blocking=False):
            return _failure("busy")
        task_id = uuid.uuid4().hex
        with self._lock:
            self._state = {
                "ok": True,
                "error": "",
                "active": True,
                "task_id": task_id,
                "status": "running",
                "operation": operation,
                "account": expected,
                "total": len(values),
                "done": 0,
                "items": [],
                "cancel_requested": False,
            }
        try:
            threading.Thread(
                target=self._run,
                args=(client, expected, values, operation),
                daemon=True,
            ).start()
        except Exception:
            with self._lock:
                self._state.update(
                    active=False, ok=False, error="thread_start_failed", status="error"
                )
            _IO_LOCK.release()
            return _failure("thread_start_failed")
        return {"ok": True, "error": "", "status": "started", "task_id": task_id}

    # 每项写入前重新读取账号和当前收藏，之后严格复核结果。
    def _item(self, client, expected, value, operation):
        self._account(client, expected)
        favorites = self._favorites(client)
        self._account(client, expected)
        if operation == "upload":
            path, md5, size = _image(value)
            key = (client.endpoint, expected, md5)
            if any(str(x.get("md5", "")).upper() == md5 for x in favorites):
                self._unconfirmed.discard(key)
                return {"ok": True, "status": "duplicate", "md5": md5}
            if key in self._unconfirmed:
                return _failure("previous_submission_unconfirmed", status="blocked")
            action = "add_custom_face"
            params = {"file": path, "md5": md5, "file_size": size}
        else:
            if not any(x.get("resId") == value for x in favorites):
                return _failure("resource_not_in_current_list", status="failed")
            key = (client.endpoint, expected, "delete:" + value)
            if key in self._unconfirmed:
                return _failure("previous_submission_unconfirmed", status="blocked")
            action, params = "delete_custom_face", {"res_id": value}
        self._account(client, expected)
        self._unconfirmed.add(key)
        evidence = {}
        try:
            data = client.call(action, params)
            if (
                not isinstance(data, dict)
                or type(data.get("result")) is not int
                or data["result"] != 0
            ):
                if isinstance(data, dict) and type(data.get("result")) is int:
                    self._unconfirmed.discard(key)
                    return _failure("native_failed", status="failed")
                return _failure("native_result_missing", status="submitted_unconfirmed")
            if operation == "delete":
                before_ids = {x["resId"] for x in favorites}
                expected_ids = before_ids - {value}
                evidence = {
                    "verification_scope": "observed_list",
                    "evidence": {
                        "native_result": 0,
                        "before_res_ids": sorted(before_ids),
                        "observations": [],
                    },
                }
                for _ in range(2):
                    self._account(client, expected)
                    after = self._favorites(client)
                    self._account(client, expected)
                    after_ids = {x["resId"] for x in after}
                    matches = (
                        after_ids == expected_ids and len(after) == len(favorites) - 1
                    )
                    evidence["evidence"]["observations"].append(
                        {
                            "res_ids": sorted(after_ids),
                            "removed": sorted(before_ids - after_ids),
                            "added": sorted(after_ids - before_ids),
                            "count": len(after),
                            "matches_expected": matches,
                        }
                    )
                    if not matches:
                        return _failure(
                            "verification_pending",
                            status="submitted_unconfirmed",
                            **evidence,
                        )
                self._unconfirmed.discard(key)
                return {"ok": True, "status": "verified", "error": "", **evidence}
            self._account(client, expected)
            after = self._favorites(client)
            self._account(client, expected)
            verified = any(str(x.get("md5", "")).upper() == md5 for x in after)
            if verified:
                self._unconfirmed.discard(key)
                return {"ok": True, "status": "verified", "error": ""}
            return _failure("verification_pending", status="submitted_unconfirmed")
        except QQFavoritesError as exc:
            return _failure(str(exc), status="submitted_unconfirmed", **evidence)
        except Exception:
            return _failure(
                "unexpected_error", status="submitted_unconfirmed", **evidence
            )

    # 批量串行执行；取消只跳过尚未开始的项。
    def _run(self, client, expected, values, operation):
        fatal = ""
        try:
            for value in values:
                with self._lock:
                    cancelled = self._state["cancel_requested"]
                if cancelled:
                    result = _failure("cancelled", status="cancelled")
                elif fatal:
                    result = _failure(fatal, status="blocked")
                else:
                    try:
                        result = self._item(client, expected, value, operation)
                    except QQFavoritesError as exc:
                        result = _failure(str(exc), status="failed")
                    except Exception:
                        result = _failure("unexpected_error", status="failed")
                    if result.get("error") == "account_mismatch":
                        fatal = "account_mismatch"
                with self._lock:
                    self._state["items"].append(
                        {
                            "item": value,
                            "name": (
                                Path(value).name if operation == "upload" else value
                            ),
                            "state": result["status"],
                            **result,
                        }
                    )
                    self._state["done"] += 1
                    if not result["ok"]:
                        self._state.update(ok=False, error=result["error"])
        finally:
            with self._lock:
                self._state["active"] = False
                self._state["status"] = (
                    "cancelled"
                    if self._state["cancel_requested"]
                    else "done" if self._state["ok"] else "error"
                )
            _IO_LOCK.release()

    # 返回深拷贝，调用方不能改变线程内部状态。
    def progress(self):
        with self._lock:
            return {**copy.deepcopy(self._state), "running": self._state["active"]}

    # 不取消已提交请求或正在核验的项。
    def cancel(self):
        with self._lock:
            if self._state["active"]:
                self._state["cancel_requested"] = True
            return {"ok": True, "error": "", "active": self._state["active"]}
