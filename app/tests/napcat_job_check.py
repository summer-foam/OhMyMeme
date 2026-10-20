"""Verify native Windows ownership using disposable Python processes, not QQ."""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.napcat_manager import _WindowsJob


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows only")
    job = _WindowsJob()
    process = None
    try:
        script = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            creationflags=0x00000004 | 0x08000000,
        )
        job.attach_and_resume(process)
        process.wait(timeout=5)
        assert process.returncode == 0
        assert job.active_count() >= 1, "Child must remain owned after parent exits"
        job.terminate()
        deadline = time.monotonic() + 5
        while job.active_count() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert job.active_count() == 0
    finally:
        job.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    print("Windows Job check passed: suspended launch, parent exit, child ownership, tree cleanup")


if __name__ == "__main__":
    main()
