import ipaddress
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

COUNTS = (48, 2000, 5000)
READ_ACTIONS = frozenset(("get_login_info", "fetch_custom_face_detail"))
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class DiagnosticError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # 禁止认证请求被重定向到其他服务。
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ReadOnlyClient:
    # 只接收明确提供的本机 HTTP 服务和令牌，不读取应用配置。
    def __init__(self, endpoint, token):
        try:
            parsed = urllib.parse.urlsplit(endpoint)
            local = parsed.hostname == "localhost"
            if not local:
                local = ipaddress.ip_address(parsed.hostname).is_loopback
            valid = (
                local
                and parsed.scheme in ("http", "https")
                and parsed.port is not None
                and not parsed.username
                and not parsed.password
                and parsed.path in ("", "/")
                and not parsed.query
                and not parsed.fragment
            )
        except (ValueError, TypeError):
            valid = False
        if not valid or not token or any(c in token for c in "\r\n"):
            raise DiagnosticError("invalid_connection_environment")
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect()
        )

    # 动作白名单、响应大小上限及固定错误码防止写入和敏感响应泄露。
    def call(self, action, params=None):
        if action not in READ_ACTIONS:
            raise DiagnosticError("read_action_required")
        if action == "fetch_custom_face_detail" and params not in [
            {"count": count} for count in COUNTS
        ]:
            raise DiagnosticError("unsupported_read_parameters")
        if action == "get_login_info" and params not in (None, {}):
            raise DiagnosticError("unsupported_read_parameters")
        request = urllib.request.Request(
            self._endpoint + "/" + action,
            data=json.dumps(params or {}).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            raise DiagnosticError(f"http_{code}") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise DiagnosticError("transport_failed") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise DiagnosticError("response_too_large")
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeError):
            raise DiagnosticError("invalid_json") from None
        if (
            not isinstance(envelope, dict)
            or envelope.get("status") != "ok"
            or envelope.get("retcode") != 0
        ):
            raise DiagnosticError("action_failed")
        return envelope.get("data")


# 每次读取前后核对账号，禁止输出账号或服务返回的昵称。
def check_account(call, expected):
    data = call("get_login_info")
    if not isinstance(data, dict) or str(data.get("user_id", "")) != expected:
        raise DiagnosticError("account_mismatch")


# 资源标识仅驻留内存，最终输出只保留计数和集合关系。
def snapshot(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DiagnosticError("invalid_favorites_shape")
    sequences = {}
    summary = {"returned": len(rows)}
    for field in ("md5", "resId"):
        values = []
        for row in rows:
            value = row.get(field)
            if isinstance(value, str) and value.strip():
                value = value.strip()
                values.append(value.upper() if field == "md5" else value)
        sequences[field] = values
        summary[field] = {
            "unique": len(set(values)),
            "missing": len(rows) - len(values),
            "duplicates": len(values) - len(set(values)),
        }
    return sequences, summary


# 比较集合和顺序，避免把相同数量误判为相同内容。
def compare(left, right):
    result = {}
    for field in ("md5", "resId"):
        a, b = set(left[field]), set(right[field])
        result[field] = {
            "same_set": a == b,
            "same_order": left[field] == right[field],
            "left_only": len(a - b),
            "right_only": len(b - a),
            "left_subset": a <= b,
        }
    return result


# 固定小规模串行读取；任何错误立即中止，不重试或探测未知动作。
def diagnose(call, account, rounds=2, interval=1.0, sleep=time.sleep):
    if (
        not isinstance(account, str)
        or not account.isascii()
        or not account.isdigit()
        or type(rounds) is not int
        or not 2 <= rounds <= 5
        or not math.isfinite(interval)
        or not 0.25 <= interval <= 30
    ):
        raise DiagnosticError("invalid_diagnostic_parameters")
    samples, comparisons, previous = [], [], {}
    first_call = True

    # 所有网络动作均受同一串行间隔约束，包括账号核对。
    def paced(action, params=None):
        nonlocal first_call
        if not first_call:
            sleep(interval)
        first_call = False
        return call(action, params)

    for round_index in range(rounds):
        current = {}
        for count in COUNTS:
            check_account(paced, account)
            rows = paced("fetch_custom_face_detail", {"count": count})
            check_account(paced, account)
            values, summary = snapshot(rows)
            samples.append({"round": round_index + 1, "count": count, **summary})
            current[count] = values
            if count in previous:
                comparisons.append(
                    {
                        "kind": "repeat",
                        "round": round_index + 1,
                        "count": count,
                        **compare(previous[count], values),
                    }
                )
        for left, right in ((48, 2000), (2000, 5000), (48, 5000)):
            comparisons.append(
                {
                    "kind": "count",
                    "round": round_index + 1,
                    "left_count": left,
                    "right_count": right,
                    **compare(current[left], current[right]),
                }
            )
        previous = current
    return {
        "ok": True,
        "mode": "read_only",
        "samples": samples,
        "comparisons": comparisons,
        "completeness": "not_proven_by_count_or_stability",
        "server_pagination": "not_exposed_in_inspected_release",
        "capacity": {"queried": False, "reason": "no_verified_read_api"},
    }


# 连接信息只从显式环境变量读取，标准输出仅返回脱敏 JSON。
def main():
    try:
        endpoint, account, token = (
            os.environ.get(name, "")
            for name in ("QQ_DIAG_ENDPOINT", "QQ_DIAG_ACCOUNT", "QQ_DIAG_TOKEN")
        )
        if not all((endpoint, account, token)):
            raise DiagnosticError("explicit_connection_environment_required")
        client = ReadOnlyClient(endpoint, token)
        report = diagnose(client.call, account)
    except DiagnosticError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
