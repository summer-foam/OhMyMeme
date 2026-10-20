import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.webui import JsApi


def test_interrupted_notice_uses_marker_presence_without_exposing_contents(tmp_path):
    bridge = JsApi.__new__(JsApi)
    bridge._qq_connection_lock = threading.Lock()
    bridge._qq_token = "private-token"
    bridge._cfg = SimpleNamespace(data_dir=tmp_path, get=lambda key, default: default)
    bridge._qq_favorites = Mock()
    assert bridge.qq_favorites_status()["interrupted_task"] is False
    marker = Path(tmp_path) / "qq-interrupted-task.json"
    marker.write_text('{"sensitive": "do-not-expose"}', encoding="utf-8")
    result = bridge.qq_favorites_status()
    assert result["interrupted_task"] is True
    assert "do-not-expose" not in str(result)
    assert "private-token" not in str(result)
