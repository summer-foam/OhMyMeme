import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "app/scripts/qq_diagnostics.py"
SPEC = importlib.util.spec_from_file_location("qq_diagnostics", SCRIPT)
diag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diag)


# 同数量不同集合必须被发现；报告不能带回资源和账号。
def test_count_comparison_and_repeat_detect_content_change():
    calls, sleeps, occurrences = [], [], {}

    def call(action, params=None):
        calls.append((action, params))
        if action == "get_login_info":
            return {"user_id": "12345", "nickname": "private"}
        count = params["count"]
        occurrences[count] = occurrences.get(count, 0) + 1
        items = ["A", "B"] if count == 48 else ["A", "B", "C"]
        if count == 5000 and occurrences[count] == 2:
            items = ["A", "B", "D"]
        return [{"md5": item, "resId": "secret-" + item} for item in items]

    report = diag.diagnose(call, "12345", sleep=sleeps.append)
    changed = next(
        item
        for item in report["comparisons"]
        if item["kind"] == "repeat" and item["count"] == 5000
    )
    assert changed["md5"]["same_set"] is False
    assert changed["md5"]["left_only"] == changed["md5"]["right_only"] == 1
    assert len(calls) == 18
    assert len(sleeps) == 17 and set(sleeps) == {1.0}
    assert {action for action, _ in calls} == diag.READ_ACTIONS
    assert report["capacity"]["queried"] is False
    assert report["completeness"] == "not_proven_by_count_or_stability"
    serialized = json.dumps(report)
    assert all(secret not in serialized for secret in ("12345", "private", "secret-"))


# 空集合稳定也不能升级为完整，缺字段和重复单独计数。
def test_snapshot_and_empty_stability():
    _, summary = diag.snapshot(
        [{"md5": "a", "resId": "x"}, {"md5": "A"}, {"md5": None}]
    )
    assert summary["md5"] == {"unique": 1, "missing": 1, "duplicates": 1}
    assert summary["resId"]["missing"] == 2
    report = diag.diagnose(
        lambda action, params=None: (
            {"user_id": 12345} if action == "get_login_info" else []
        ),
        "12345",
        sleep=lambda interval: None,
    )
    assert all(item["md5"]["same_set"] for item in report["comparisons"])
    assert report["completeness"] == "not_proven_by_count_or_stability"


# 账号错误时不读取收藏，读取期间切换账号则丢弃结果。
@pytest.mark.parametrize("switch_after", [0, 2])
def test_account_guard(switch_after):
    calls = []

    def call(action, params=None):
        calls.append(action)
        if action == "get_login_info":
            return {"user_id": "12345" if len(calls) <= switch_after else "999"}
        return []

    with pytest.raises(diag.DiagnosticError, match="account_mismatch"):
        diag.diagnose(call, "12345", sleep=lambda interval: None)
    assert len(calls) == (1 if switch_after == 0 else 3)


# 拒绝写动作、未知分页参数和非本机端点，不建立网络连接。
def test_read_only_allowlist():
    client = diag.ReadOnlyClient("http://127.0.0.1:3000", "private-token")
    for action, params in (
        ("add_custom_face", {}),
        ("delete_custom_face", {}),
        ("get_custom_face_count", {}),
        ("fetch_custom_face_detail", {"count": 48, "offset": 48}),
    ):
        with pytest.raises(diag.DiagnosticError):
            client.call(action, params)


# 认证信息必须显式设置，禁止 URL 查询令牌和重定向。
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com:3000",
        "http://127.0.0.1:3000/?token=secret",
        "http://user:secret@127.0.0.1:3000",
        "http://127.0.0.1",
        "http://127.0.0.1:3000/other",
    ],
)
def test_invalid_endpoint(endpoint):
    with pytest.raises(diag.DiagnosticError, match="invalid_connection_environment"):
        diag.ReadOnlyClient(endpoint, "token")
    assert diag.NoRedirect().redirect_request(None, None, 302, "", {}, "") is None


# 限流立即终止且只输出固定错误码，不转储服务响应或连接信息。
def test_http_429_redaction(monkeypatch, capsys):
    import io
    import urllib.error

    calls = []

    def open_request(self, request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(
            "http://private-url", 429, "private-message", {}, io.BytesIO(b"secret")
        )

    monkeypatch.setattr(diag.urllib.request.OpenerDirector, "open", open_request)
    monkeypatch.setenv("QQ_DIAG_ENDPOINT", "http://127.0.0.1:3000")
    monkeypatch.setenv("QQ_DIAG_ACCOUNT", "12345")
    monkeypatch.setenv("QQ_DIAG_TOKEN", "private-token")
    assert diag.main() == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "http_429"}
    assert len(calls) == 1


# 缺少环境变量时不读取真实配置、不建立连接。
def test_explicit_environment_required(monkeypatch, capsys):
    for name in ("QQ_DIAG_ENDPOINT", "QQ_DIAG_ACCOUNT", "QQ_DIAG_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert diag.main() == 1
    assert json.loads(capsys.readouterr().out)["error"] == (
        "explicit_connection_environment_required"
    )


# 畸形数据不得当成空列表，阻止错误的稳定性结论。
@pytest.mark.parametrize("rows", [{}, None, ["url"], [None]])
def test_invalid_favorites_shape(rows):
    with pytest.raises(diag.DiagnosticError, match="invalid_favorites_shape"):
        diag.snapshot(rows)
