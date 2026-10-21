"""通过 Node VM 执行设置窗口云端流程，桥接与 DOM 使用隔离替身。"""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_cloud_settings_save_failure_direction_and_recovery():
    # 检查真实设置脚本的异步调用顺序与错误恢复。
    node = shutil.which("node")
    if not node:
        pytest.fail("云端设置流程回归需要 Node.js")
    script_path = Path(__file__).resolve().parents[1] / "src/webui/settings.js"
    probe = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const document = {
  addEventListener() {},
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', checked: false, disabled: false, textContent: '', style: {},
      classList: {add() {}, remove() {}}, addEventListener() {}
    });
    return elements.get(id);
  }
};
const context = vm.createContext({document, window: {}, console, setTimeout,
  clearTimeout, setInterval, clearInterval});
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
vm.runInContext(`
  showToast = () => {};
  collectSyncSettings = () => ({sync_type: 's3', sync_remove_local: false});
  validateSync = () => true;
  restoreSettingsFocus = () => {};
`, context);
(async () => {
  const calls = [];
  context.api = async (method) => { calls.push(method); return {ok: false}; };
  await context.testSync();
  assert.deepEqual(calls, ['save_settings']);
  assert.equal(document.getElementById('btn-ftp-test').disabled, false);
  assert.match(document.getElementById('s-sync-status').textContent, /保存云端设置失败/);
  calls.length = 0;
  context.api = async (method) => {
    calls.push(method);
    if (method === 'save_settings') return {ok: true};
    throw Error('bridge disconnected');
  };
  await context.testSync();
  assert.equal(document.getElementById('btn-ftp-test').disabled, false);
  assert.match(document.getElementById('s-sync-status').textContent, /bridge disconnected/);
  context.showUploadWarning = () => { throw Error('download used upload warning'); };
  context.api = async (method) => { calls.push(method); return {ok: true, downloaded: 2}; };
  calls.length = 0;
  await context.syncPull();
  assert.deepEqual(calls, ['save_settings', 'sync_pull']);
  assert.match(document.getElementById('s-sync-status').textContent, /成功 2/);
  context.collectSyncSettings = () => ({sync_type: 's3', sync_remove_local: true});
  context.showConfirm = async () => false;
  calls.length = 0;
  await context.syncPull();
  assert.deepEqual(calls, []);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", probe, str(script_path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
