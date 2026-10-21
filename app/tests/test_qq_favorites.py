import copy
import hashlib
import io
import json
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import qq_favorites as qq

ENDPOINT = "http://127.0.0.1:3000"
PNG = b"\x89PNG\r\n\x1a\n" + b"test-image"
MD5 = hashlib.md5(PNG).hexdigest().upper()


class FakeNapCat:
    # 所有网络行为由内存假响应代替。
    def __init__(self):
        self.account = "12345"
        self.favorites = []
        self.calls = []
        self.native = {"result": 0}
        self.confirm = True
        self.fail_after_write = False
        self.wrote = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block_write = False
        self.after_lists = []

    # 记录实际动作与参数，并模拟服务端提交效果。
    def call(self, client, action, params=None):
        self.calls.append((action, copy.deepcopy(params)))
        if action == "get_login_info":
            return {"user_id": self.account}
        if action == "fetch_custom_face_detail":
            if self.fail_after_write and self.wrote:
                raise qq.QQFavoritesError("http_503")
            assert params == {"count": 5000}
            if self.wrote and self.after_lists:
                return copy.deepcopy(self.after_lists.pop(0))
            return copy.deepcopy(self.favorites)
        self.wrote = True
        if self.block_write:
            self.entered.set()
            assert self.release.wait(5)
        if self.confirm and self.native == {"result": 0}:
            if action == "add_custom_face":
                resource = "new" if not self.favorites else "new-" + params["md5"]
                self.favorites.append({"resId": resource, "md5": params["md5"]})
            elif action == "delete_custom_face":
                self.favorites = [
                    x for x in self.favorites if x["resId"] != params["res_id"]
                ]
        return copy.deepcopy(self.native)


@pytest.fixture
def fake(monkeypatch):
    # 默认阻断全部真实 HTTP，即使测试遗漏 mock 也不连接 QQ。
    def forbidden(*args, **kwargs):
        raise AssertionError("real HTTP forbidden")

    monkeypatch.setattr(qq.urllib.request.OpenerDirector, "open", forbidden)
    fake = FakeNapCat()
    monkeypatch.setattr(
        qq.NapCatClient,
        "call",
        lambda client, action, params=None: fake.call(client, action, params),
    )
    return fake


@pytest.fixture
def image(tmp_path):
    # 输入文件只位于测试临时目录。
    path = tmp_path / "image.dat"
    path.write_bytes(PNG)
    return str(path)


# 等待可观察的终态，有界超时防止线程泄漏。
def finished(service):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = service.progress()
        assert state["running"] is state["active"]
        assert isinstance(state["task_id"], str)
        for item in state["items"]:
            assert item["state"] == item["status"]
            assert item["name"] == (
                Path(item["item"]).name
                if state["operation"] == "upload"
                else item["item"]
            )
        if not state["active"]:
            return state
        time.sleep(0.005)
    pytest.fail("task did not finish")


# 测试启动结果和终态结果是不同契约。
def upload(service, image):
    result = service.start_upload(ENDPOINT, "secret", "12345", [image])
    assert result["ok"]
    assert isinstance(result["task_id"], str) and result["task_id"]
    state = finished(service)
    assert state["task_id"] == result["task_id"]
    return state


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:3000",
        "https://127.0.0.1:3000",
        "http://192.168.1.1:3000",
        "http://127.0.0.1.evil:3000",
        "http://user:secret@127.0.0.1:3000",
        "http://127.0.0.1:3000/path",
        "http://127.0.0.1:3000?token=secret",
        "http://[::1%25eth0]:3000",
        "http://2130706433:3000",
        "http://127.0.0.1:0",
    ],
)
# 非 literal loopback 输入不触发任何请求。
def test_endpoint_rejected(fake, endpoint):
    result = qq.QQFavoritesService().status(endpoint, "secret")
    assert result["error"] == "invalid_endpoint"
    assert fake.calls == []


# IPv4 和 IPv6 数字回环均可使用，账号字符串化。
@pytest.mark.parametrize("endpoint", [ENDPOINT, "http://[::1]:3000"])
def test_status_and_list(fake, endpoint):
    fake.account = 12345
    fake.favorites = [{"resId": "r1", "md5": MD5}]
    service = qq.QQFavoritesService()
    assert service.status(endpoint, "secret")["account"] == "12345"
    result = service.list_favorites(endpoint, "secret", "12345")
    assert result["favorites"] == fake.favorites
    assert result["completeness"] == "unknown"
    assert "secret" not in json.dumps(result)


# 错误账号不能读取列表或写入。
def test_wrong_account(fake, image):
    fake.account = "54321"
    service = qq.QQFavoritesService()
    assert service.list_favorites(ENDPOINT, "secret", "12345") == {
        "ok": False,
        "error": "account_mismatch",
        "account": "",
    }
    state = upload(service, image)
    assert state["items"][0]["error"] == "account_mismatch"
    assert all(action == "get_login_info" for action, _ in fake.calls)


# 预期账号不能为空，避免写入当前偶然登录的账号。
def test_write_requires_account(fake, image):
    service = qq.QQFavoritesService()
    result = service.start_upload(ENDPOINT, "secret", "", [image])
    assert result["error"] == "expected_account_required"
    assert not fake.calls


# 只有回查出现相同 MD5 才确认上传，且源文件保持不变。
def test_upload_verified(fake, image):
    state = upload(qq.QQFavoritesService(), image)
    assert state["ok"]
    assert state["items"][0]["status"] == "verified"
    assert Path(image).read_bytes() == PNG
    params = next(p for action, p in fake.calls if action == "add_custom_face")
    assert params == {"file": image, "md5": MD5, "file_size": len(PNG)}


# 同批次同内容仅提交一次，远端已有同内容则完全不上传。
@pytest.mark.parametrize("remote_exists", [False, True])
def test_md5_dedup(fake, image, tmp_path, remote_exists):
    second = tmp_path / "different-name.png"
    second.write_bytes(PNG)
    if remote_exists:
        fake.favorites = [{"resId": "r1", "md5": MD5.lower()}]
    service = qq.QQFavoritesService()
    assert service.start_upload(ENDPOINT, "secret", "12345", [image, str(second)])["ok"]
    state = finished(service)
    assert state["ok"]
    assert state["items"][-1]["status"] == "duplicate"
    assert sum(a == "add_custom_face" for a, _ in fake.calls) == (
        0 if remote_exists else 1
    )


@pytest.mark.parametrize("native", [{"result": 5}, {"result": False}, {}, None])
# 外层成功不能掩盖原生业务失败或未知结果。
def test_native_result_required(fake, image, native):
    fake.native = native
    state = upload(qq.QQFavoritesService(), image)
    assert not state["ok"]
    assert state["items"][0]["status"] != "verified"
    if native == {"result": 5}:
        assert state["items"][0]["error"] == "native_failed"


@pytest.mark.parametrize("fail_read", [False, True])
# 未确认提交保留未知状态，再次运行也不自动重传。
def test_unconfirmed_never_retransmitted(fake, image, fail_read):
    fake.confirm = False
    fake.fail_after_write = fail_read
    service = qq.QQFavoritesService()
    state = upload(service, image)
    assert state["items"][0]["status"] == "submitted_unconfirmed"
    fake.fail_after_write = False
    state = upload(service, image)
    assert state["items"][0]["status"] == "blocked"
    assert sum(a == "add_custom_face" for a, _ in fake.calls) == 1


# 后续读到原先未确认的 MD5 可以安全去重。
def test_unconfirmed_later_visible(fake, image):
    fake.confirm = False
    service = qq.QQFavoritesService()
    upload(service, image)
    fake.favorites = [{"resId": "later", "md5": MD5}]
    assert upload(service, image)["items"][0]["status"] == "duplicate"
    assert sum(a == "add_custom_face" for a, _ in fake.calls) == 1


# 不允许把 MD5 或清单外资源当作删除 ID。
def test_delete_range(fake):
    fake.favorites = [{"resId": "r1", "md5": MD5}]
    service = qq.QQFavoritesService()
    assert service.start_delete(ENDPOINT, "secret", "12345", [MD5, "other"])["ok"]
    state = finished(service)
    assert not state["ok"]
    assert all(x["error"] == "resource_not_in_current_list" for x in state["items"])
    assert not any(a == "delete_custom_face" for a, _ in fake.calls)


@pytest.mark.parametrize("confirm", [False, True])
# 删除仅在原生成功及两次相对列表核验一致时确认。
def test_delete_verification(fake, confirm):
    fake.favorites = [{"resId": "r1", "md5": MD5}, {"resId": "keep"}]
    fake.confirm = confirm
    service = qq.QQFavoritesService()
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    state = finished(service)
    expected = "verified" if confirm else "submitted_unconfirmed"
    assert state["items"][0]["status"] == expected
    assert {"resId": "keep"} in fake.favorites
    assert ("delete_custom_face", {"res_id": "r1"}) in fake.calls
    item = state["items"][0]
    assert item["verification_scope"] == "observed_list"
    assert item["evidence"]["native_result"] == 0
    if confirm:
        observations = item["evidence"]["observations"]
        assert len(observations) == 2
        assert all(x["matches_expected"] for x in observations)
        assert all(x["removed"] == ["r1"] and x["added"] == [] for x in observations)


@pytest.mark.parametrize(
    "favorites",
    [
        [{}],
        [{"resId": ""}],
        [{"resId": " "}],
        [{"resId": 1}],
        [{"resId": "r1"}, {"resId": "r1"}],
    ],
)
# 缺失或重复资源 ID 的清单不能用于展示或发起写入。
def test_invalid_resource_ids_rejected(fake, favorites):
    fake.favorites = favorites
    service = qq.QQFavoritesService()
    assert service.list_favorites(ENDPOINT, "secret")["error"] == "invalid_favorites"
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    assert finished(service)["items"][0]["error"] == "invalid_favorites"
    assert not any(a == "delete_custom_face" for a, _ in fake.calls)


@pytest.mark.parametrize(
    "after",
    [
        [],
        [{"resId": "keep1"}],
        [{"resId": "keep1"}, {"resId": "added"}],
        [{"resId": "keep1"}, {"resId": "keep2"}, {"resId": "added"}],
        [{}],
        [{"resId": "keep1"}, {"resId": "keep1"}],
    ],
)
@pytest.mark.parametrize("bad_query", [0, 1])
# 任一次回查的部分、额外或畸形清单都不能把目标缺席当成功。
def test_delete_partial_or_invalid_after_unconfirmed(fake, after, bad_query):
    before = [{"resId": "r1"}, {"resId": "keep1"}, {"resId": "keep2"}]
    fake.favorites = copy.deepcopy(before)
    expected = before[1:]
    fake.after_lists = [after] if bad_query == 0 else [expected, after]
    service = qq.QQFavoritesService()
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    item = finished(service)["items"][0]
    assert item["status"] == "submitted_unconfirmed"
    assert item["verification_scope"] == "observed_list"
    assert item["evidence"]["native_result"] == 0
    assert (ENDPOINT, "12345", "delete:r1") in service._unconfirmed
    fake.after_lists = []
    fake.favorites = before
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    assert finished(service)["items"][0]["status"] == "blocked"
    assert sum(a == "delete_custom_face" for a, _ in fake.calls) == 1


# 对仅观察到一个资源的列表，两次空列表只确认该观察范围内的删除。
def test_delete_single_observed_resource(fake):
    fake.favorites = [{"resId": "r1"}]
    service = qq.QQFavoritesService()
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    item = finished(service)["items"][0]
    assert item["status"] == "verified"
    assert item["verification_scope"] == "observed_list"
    assert [x["res_ids"] for x in item["evidence"]["observations"]] == [[], []]
    assert service.list_favorites(ENDPOINT, "secret")["completeness"] == "unknown"


# 写任务运行时读写都返回 busy，取消保留正在执行的第一项。
def test_cancel_and_mutex(fake, image, tmp_path):
    fake.block_write = True
    second = tmp_path / "second.gif"
    second.write_bytes(b"GIF89a-other")
    service = qq.QQFavoritesService()
    assert service.start_upload(ENDPOINT, "secret", "12345", [image, str(second)])["ok"]
    try:
        assert fake.entered.wait(5)
        assert service.status(ENDPOINT, "secret")["error"] == "busy"
        assert service.list_favorites(ENDPOINT, "secret")["error"] == "busy"
        assert (
            service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["error"] == "busy"
        )
        assert service.cancel()["ok"]
        snapshot = service.progress()
        assert snapshot["running"] is snapshot["active"] is True
        snapshot["items"].append({"corrupt": True})
        assert not service.progress()["items"]
    finally:
        fake.release.set()
    state = finished(service)
    assert [x["status"] for x in state["items"]] == ["verified", "cancelled"]
    assert state["done"] == state["total"] == 2
    assert sum(a == "add_custom_face" for a, _ in fake.calls) == 1


# 同一实例的连续任务和不同实例的任务均有独立身份。
def test_task_contract_and_unique_ids(fake, image):
    service = qq.QQFavoritesService()
    initial = service.progress()
    assert initial["running"] is initial["active"] is False
    assert initial["task_id"] == ""
    first = service.start_upload(ENDPOINT, "secret", "12345", [image])
    assert first["ok"]
    assert finished(service)["task_id"] == first["task_id"]
    second = service.start_delete(ENDPOINT, "secret", "12345", ["new"])
    assert second["ok"]
    state = finished(service)
    assert state["task_id"] == second["task_id"]
    assert state["items"][0]["name"] == "new"
    third = qq.QQFavoritesService()
    upload(third, image)
    task_ids = [first["task_id"], second["task_id"], third.progress()["task_id"]]
    assert all(isinstance(value, str) and value for value in task_ids)
    assert len(set(task_ids)) == 3


# 线程启动失败也必须返回 running=False 的一致终态。
def test_thread_start_failure_contract(fake, image, monkeypatch):
    def fail_start(thread):
        raise RuntimeError("cannot start")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    service = qq.QQFavoritesService()
    result = service.start_upload(ENDPOINT, "secret", "12345", [image])
    assert result == {"ok": False, "error": "thread_start_failed"}
    state = finished(service)
    assert state["running"] is state["active"] is False
    assert state["status"] == "error"


# magic 不匹配的文件不能发给 NapCat。
def test_invalid_image(fake, tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"not an image")
    state = upload(qq.QQFavoritesService(), str(path))
    assert state["items"][0]["error"] == "unsupported_image"
    assert not any(a == "add_custom_face" for a, _ in fake.calls)


@pytest.mark.parametrize(
    "content",
    [
        PNG,
        b"\xff\xd8\xff-example",
        b"GIF87a-example",
        b"BM-example",
        b"RIFF1234WEBPdata",
    ],
)
# 只根据常见图片 magic 放行，不依赖扩展名。
def test_allowed_magic(fake, tmp_path, content):
    path = tmp_path / "no-extension"
    path.write_bytes(content)
    assert upload(qq.QQFavoritesService(), str(path))["ok"]


# 删除同样要求原生成功，账号不匹配时不会请求删除。
def test_delete_failure_and_wrong_account(fake):
    fake.favorites = [{"resId": "r1", "md5": MD5}]
    fake.native = {"result": 8}
    service = qq.QQFavoritesService()
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    assert finished(service)["items"][0]["error"] == "native_failed"
    fake.calls.clear()
    fake.account = "54321"
    assert service.start_delete(ENDPOINT, "secret", "12345", ["r1"])["ok"]
    assert finished(service)["items"][0]["error"] == "account_mismatch"
    assert not any(a == "delete_custom_face" for a, _ in fake.calls)


# 超时意味着服务端可能已接收，不应归为可安全重传的失败。
def test_write_timeout_is_unconfirmed(fake, image, monkeypatch):
    original = fake.call

    def timeout(client, action, params=None):
        if action == "add_custom_face":
            fake.calls.append((action, params))
            raise qq.QQFavoritesError("transport_or_invalid_response")
        return original(client, action, params)

    monkeypatch.setattr(qq.NapCatClient, "call", timeout)
    service = qq.QQFavoritesService()
    assert upload(service, image)["items"][0]["status"] == "submitted_unconfirmed"
    assert upload(service, image)["items"][0]["status"] == "blocked"
    assert sum(a == "add_custom_face" for a, _ in fake.calls) == 1


# 回查过程账号切换会保留未确认状态并阻止后续写入。
def test_account_switch_after_submission(fake, image, monkeypatch):
    original = fake.call

    def changing(client, action, params=None):
        result = original(client, action, params)
        if action == "add_custom_face":
            fake.account = "54321"
        return result

    monkeypatch.setattr(qq.NapCatClient, "call", changing)
    service = qq.QQFavoritesService()
    assert service.start_upload(ENDPOINT, "secret", "12345", [image, image])["ok"]
    state = finished(service)
    assert state["items"][0]["status"] == "submitted_unconfirmed"
    assert state["items"][1]["status"] == "blocked"
    assert sum(a == "add_custom_face" for a, _ in fake.calls) == 1


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"status": "ok", "retcode": 0, "data": []}, None),
        ({"status": "failed", "retcode": 0, "message": "secret"}, "onebot_failed"),
        ({"status": "ok", "retcode": 1, "data": {}}, "onebot_failed"),
        ({"status": "ok", "retcode": False}, "onebot_failed"),
        ([], "invalid_response"),
    ],
)
# HTTP 客户端使用内存响应验证外层业务结果和认证头。
def test_http_envelope(monkeypatch, payload, error):
    client = qq.NapCatClient(ENDPOINT, "secret")

    def respond(request, timeout):
        assert request.get_header("Authorization") == "Bearer secret"
        assert json.loads(request.data) == {"count": 5000}
        assert timeout == 15
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(client._opener, "open", respond)
    if error:
        with pytest.raises(qq.QQFavoritesError, match=error) as caught:
            client.call("fetch_custom_face_detail", {"count": 5000})
        assert "secret" not in str(caught.value)
    else:
        assert client.call("fetch_custom_face_detail", {"count": 5000}) == []


# 禁止重定向，HTTP 错误响应中的密钥不得出现在结果中。
def test_no_redirect_and_error_redaction(monkeypatch):
    handler = qq._NoRedirect()
    assert handler.redirect_request(None, None, 302, "", {}, "http://evil") is None
    client = qq.NapCatClient(ENDPOINT, "secret")

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(ENDPOINT, 401, "secret", {}, io.BytesIO(b"secret"))

    monkeypatch.setattr(client._opener, "open", fail)
    with pytest.raises(qq.QQFavoritesError, match="^http_401$"):
        client.call("get_login_info")


@pytest.mark.parametrize(
    "action,retries",
    [
        ("get_login_info", 2),
        ("fetch_custom_face_detail", 2),
        ("add_custom_face", 0),
        ("delete_custom_face", 0),
    ],
)
# 只有明确只读动作可以有界重试 429，原生写入永不盲重试。
def test_429_retry_read_only(monkeypatch, action, retries):
    client = qq.NapCatClient(ENDPOINT, "secret")
    attempts, delays = [], []

    def limited(*args, **kwargs):
        attempts.append(1)
        raise urllib.error.HTTPError(
            ENDPOINT, 429, "secret", {"Retry-After": "2"}, io.BytesIO()
        )

    monkeypatch.setattr(client._opener, "open", limited)
    monkeypatch.setattr(qq.time, "sleep", delays.append)
    with pytest.raises(qq.QQFavoritesError, match="^http_429$"):
        client.call(action)
    assert len(attempts) == retries + 1
    assert delays == [2] * retries


@pytest.mark.parametrize("header", ["999999", "-1", "nan", "not-a-delay"])
# 不受信任 Retry-After 不能让桥接无限等待。
def test_429_retry_delay_bounded(monkeypatch, header):
    client = qq.NapCatClient(ENDPOINT, "secret")
    delays = []

    def limited(*args, **kwargs):
        raise urllib.error.HTTPError(
            ENDPOINT, 429, "secret", {"Retry-After": header}, io.BytesIO()
        )

    monkeypatch.setattr(client._opener, "open", limited)
    monkeypatch.setattr(qq.time, "sleep", delays.append)
    with pytest.raises(qq.QQFavoritesError, match="^http_429$"):
        client.call("get_login_info")
    assert delays == [1, 1]


# 5000 项合法批次可取消，超出边界在启动前拒绝，不暗示服务端容量。
def test_batch_5000_boundary_and_cancellable_throttle(fake, monkeypatch):
    service = qq.QQFavoritesService()
    resources = [f"r{i}" for i in range(5000)]
    assert (
        service.start_delete(ENDPOINT, "s", "12345", resources + ["extra"])["error"]
        == "empty_or_invalid_items"
    )
    entered, release = threading.Event(), threading.Event()

    def first_item(*args):
        entered.set()
        assert release.wait(5)
        return {"ok": True, "status": "verified"}

    monkeypatch.setattr(service, "_item", first_item)
    monkeypatch.setattr(qq, "_ITEM_INTERVAL", 60)
    assert service.start_delete(ENDPOINT, "s", "12345", resources)["ok"]
    try:
        assert entered.wait(5)
        service.cancel()
    finally:
        release.set()
    state = finished(service)
    assert state["total"] == state["done"] == 5000
    assert state["items"][0]["state"] == "verified"
    assert all(item["state"] == "cancelled" for item in state["items"][1:])
