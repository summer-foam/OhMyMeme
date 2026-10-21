"""云存储读探测与资源释放回归，不访问外部服务。"""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import sync
from src.config import Config


def s3_backend():
    # 注入 SDK 边界，避免读取本机 AWS 凭证。
    backend = sync._S3Backend({})
    backend.bucket = "test-bucket"
    backend.prefix = "library/"
    backend.client = Mock()
    return backend


def storage_error(code):
    # 模拟 SDK 异常提供的结构化服务错误码。
    error = RuntimeError("storage request failed")
    error.response = {"Error": {"Code": code}}
    return error


def test_s3_connection_probe_reads_configured_prefix():
    backend = s3_backend()
    backend.test_connection()
    backend.client.list_objects_v2.assert_called_once_with(
        Bucket="test-bucket", Prefix="library/", MaxKeys=1
    )
    backend.client.put_object.assert_not_called()


def test_s3_connection_probe_reports_permission_failure():
    backend = s3_backend()
    backend.client.list_objects_v2.side_effect = storage_error("AccessDenied")
    with pytest.raises(sync.SyncError, match="S3 连接测试失败"):
        backend.test_connection()


@pytest.mark.parametrize("code", ["AccessDenied", "503", ""])
def test_s3_manifest_probe_does_not_treat_failure_as_missing(code):
    backend = s3_backend()
    backend.client.head_object.side_effect = storage_error(code)
    with pytest.raises(sync.SyncError):
        backend.file_exists("/meme-index.json")
    backend.client.get_object.assert_not_called()


def test_s3_missing_manifest_returns_false():
    backend = s3_backend()
    backend.client.head_object.side_effect = storage_error("404")
    assert backend.file_exists("/meme-index.json") is False


def test_s3_unsupported_head_falls_back_and_closes_body():
    backend = s3_backend()
    backend.client.head_object.side_effect = storage_error("MethodNotAllowed")
    body = io.BytesIO(b"index")
    backend.client.get_object.return_value = {"Body": body}
    assert backend.file_exists("/meme-index.json") is True
    assert body.closed


def test_s3_download_closes_stream_on_write_failure(tmp_path):
    backend = s3_backend()
    body = io.BytesIO(b"image")
    backend.client.get_object.return_value = {"Body": body}
    target = tmp_path / "directory"
    target.mkdir()
    assert backend.download_file("/memes/a.png", target) is False
    assert body.closed


def test_sync_test_closes_backend_after_failed_probe(monkeypatch):
    backend = Mock()
    backend.test_connection.side_effect = sync.SyncError("permission denied")
    monkeypatch.setattr(sync, "_get_backend", lambda: backend)
    assert sync.sync_test() == "permission denied"
    backend.close.assert_called_once()


def test_remote_index_ignores_non_object_entries(monkeypatch, tmp_path):
    # 不合法条目不能阻止安全的清单项参与差异对比。
    backend = Mock()
    backend.file_exists.return_value = True
    good = {"filename": "a.png", "sha256": "a" * 64}

    def download(remote, target):
        target.write_text(json.dumps({"memes": [None, "bad", good]}))
        return True

    backend.download_file.side_effect = download
    monkeypatch.setattr(sync, "get_config", lambda: SimpleNamespace(data_dir=tmp_path))
    assert sync._fetch_remote_memes(backend, "") == {"a.png": good}
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("signature", [None, "s3v4", "s3", "invalid"])
def test_s3_sdk_connection_respects_signature_and_addressing(signature):
    # 使用虚构凭据创建真实 SDK 客户端，不发网络请求。
    cfg = {
        "s3_endpoint": "https://storage.example.invalid",
        "s3_bucket": "test-bucket",
        "s3_access_key": "test-access",
        "s3_secret_key": "test-secret",
        "s3_region": "us-east-1",
        "s3_path": "/library/",
        "s3_addressing_style": "path",
    }
    if signature is not None:
        cfg["s3_signature_version"] = signature
    backend = sync._S3Backend(cfg)
    backend.connect()
    try:
        assert backend.client.meta.config.signature_version == (
            "s3" if signature == "s3" else "s3v4"
        )
        assert backend.client.meta.config.s3["addressing_style"] == "path"
        assert backend.prefix == "library/"
        assert Config.DEFAULTS["s3_signature_version"] == "s3v4"
    finally:
        backend.client.close()
        backend.close()


def test_s3_sdk_put_get_list_and_permission_errors(tmp_path):
    # Stubber 校验真实 botocore 操作参数及响应结构，禁止实际外部调用。
    backend = sync._S3Backend(
        {
            "s3_endpoint": "https://storage.example.invalid",
            "s3_bucket": "test-bucket",
            "s3_access_key": "test-access",
            "s3_secret_key": "test-secret",
            "s3_region": "us-east-1",
            "s3_path": "library",
        }
    )
    backend.connect()
    source = tmp_path / "source.png"
    source.write_bytes(b"image-content")
    target = tmp_path / "download.png"
    stream = StreamingBody(io.BytesIO(b"image-content"), 13)
    with Stubber(backend.client) as stub:
        stub.add_response(
            "list_objects_v2",
            {},
            {"Bucket": "test-bucket", "Prefix": "library/", "MaxKeys": 1},
        )
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "test-bucket",
                "Key": "library/memes/a.png",
                "Body": b"image-content",
                "ContentType": "application/octet-stream",
            },
        )
        stub.add_response(
            "get_object",
            {"Body": stream},
            {"Bucket": "test-bucket", "Key": "library/memes/a.png"},
        )
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "library/memes/a.png"}],
                "IsTruncated": True,
                "NextContinuationToken": "page2",
            },
            {"Bucket": "test-bucket", "Prefix": "library/memes/"},
        )
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "library/memes/b.png"}], "IsTruncated": False},
            {
                "Bucket": "test-bucket",
                "Prefix": "library/memes/",
                "ContinuationToken": "page2",
            },
        )
        stub.add_client_error(
            "head_object",
            service_error_code="AccessDenied",
            http_status_code=403,
            expected_params={"Bucket": "test-bucket", "Key": "library/meme-index.json"},
        )
        backend.test_connection()
        assert backend.upload_file(source, "/memes/a.png")
        assert backend.download_file("/memes/a.png", target)
        assert target.read_bytes() == b"image-content"
        assert stream._raw_stream.closed
        assert backend.list_files("/memes") == ["a.png", "b.png"]
        with pytest.raises(sync.SyncError, match="AccessDenied"):
            backend.file_exists("/meme-index.json")
        stub.assert_no_pending_responses()
    backend.client.close()
    backend.close()
