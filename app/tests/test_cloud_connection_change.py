import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.webui import _cloud_settings_changed
from src import webui


@pytest.mark.parametrize(
    "key",
    [
        "s3_bucket",
        "s3_endpoint",
        "s3_path",
        "s3_secret_key",
        "webdav_url",
        "webdav_path",
        "r2_bucket",
        "r2_path",
    ],
)
def test_changing_storage_invalidates_cached_library(key):
    values = {key: "original"}
    cfg = SimpleNamespace(get=values.get)
    assert _cloud_settings_changed(cfg, {key: "different"})
    assert not _cloud_settings_changed(cfg, {key: "original"})


def test_unrelated_settings_keep_cached_library():
    cfg = SimpleNamespace(get={"s3_bucket": "library"}.get)
    assert not _cloud_settings_changed(cfg, {"theme": "light"})


def test_stale_cloud_refresh_cannot_replace_new_connection(monkeypatch, tmp_path):
    from unittest.mock import Mock

    cfg = SimpleNamespace(data_dir=tmp_path, get=lambda key, default=None: False)
    monkeypatch.setattr(webui, "get_config", lambda: cfg)
    monkeypatch.setattr(
        webui, "get_db", lambda: SimpleNamespace(get_all_filenames=lambda: [])
    )
    monkeypatch.setattr(webui, "_cloud_generation", 20)
    monkeypatch.setattr(webui, "_cloud_state", {"refreshing": True})
    monkeypatch.setattr(webui, "_sync_status_reset", Mock())
    save, sync_status, notify = Mock(), Mock(), Mock()
    monkeypatch.setattr(webui.sync_module, "save_cloud_manifest", save)
    monkeypatch.setattr(webui.sync_module, "cloud_missing", lambda data, local: [])
    monkeypatch.setattr(webui, "_sync_status_set_manifest", sync_status)
    monkeypatch.setattr(webui, "_notify_cloud_ready", notify)

    def old_request_returns_late():
        webui._cloud_reset()
        webui._cloud_state["refreshing"] = True
        webui._cloud_refresh_worker({"memes": [], "source": "new"}, 21)
        webui._cloud_state["refreshing"] = True
        return {"memes": [], "source": "old"}

    monkeypatch.setattr(webui.sync_module, "download_index", old_request_returns_late)
    webui._cloud_refresh_worker(generation=20)
    assert webui._cloud_state["manifest"]["source"] == "new"
    assert webui._cloud_state["refreshing"] is True
    save.assert_called_once_with({"memes": [], "source": "new"})
    sync_status.assert_called_once_with({"memes": [], "source": "new"})
    notify.assert_called_once_with()
