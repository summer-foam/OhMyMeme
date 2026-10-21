import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.webui import JsApi


@pytest.fixture
def bridge():
    api = JsApi.__new__(JsApi)
    api._qq_connection_lock = threading.RLock()
    api._qq_shutting_down = False
    api._qq_favorites = SimpleNamespace(progress=Mock(return_value={"running": False}))
    api._napcat_runtime = Mock(
        return_value=SimpleNamespace(status=Mock(return_value={"running": False}))
    )
    installer = Mock()
    installer.start.return_value = {"ok": True, "running": True}
    api._napcat_package_installer = Mock(return_value=installer)
    return api, installer


@pytest.mark.parametrize("blocked", ["shutdown", "task", "runtime"])
def test_install_does_not_start_when_busy(bridge, blocked):
    api, installer = bridge
    if blocked == "shutdown":
        api._qq_shutting_down = True
    elif blocked == "task":
        api._qq_favorites.progress.return_value = {"running": True}
    else:
        api._napcat_runtime.return_value.status.return_value = {"running": True}
    assert not api.napcat_install_start()["ok"]
    installer.start.assert_not_called()


def test_install_only_downloads_without_starting_qq(bridge):
    api, installer = bridge
    assert api.napcat_install_start("framework", "v4.18.38")["ok"]
    installer.start.assert_called_once_with("framework", "v4.18.38")
    assert not hasattr(api._napcat_runtime.return_value, "start")


def test_progress_and_cancel_forward_to_same_installer(bridge):
    api, installer = bridge
    installer.progress.return_value = {"running": False, "state": "cancelled"}
    installer.cancel.return_value = {"ok": True}
    assert api.napcat_install_cancel() == {"ok": True}
    assert api.napcat_install_progress()["state"] == "cancelled"
    installer.cancel.assert_called_once_with()
