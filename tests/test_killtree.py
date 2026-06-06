"""Verify the whole process tree is killed on timeout (not just the root)."""
import sys
import time
from pathlib import Path

from orchestrator import runner
from orchestrator.killtree import is_pid_alive

STUB = Path(__file__).resolve().parent.parent / "stubs" / "qwen_stub.py"


def test_tree_is_killed_on_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    argv = [sys.executable, str(STUB), str(pidfile)]

    result = runner.run_process(argv, timeout_s=2)
    assert result["timed_out"] is True

    child_pid = int(pidfile.read_text())
    deadline = time.time() + 5
    while is_pid_alive(child_pid) and time.time() < deadline:
        time.sleep(0.1)
    assert not is_pid_alive(child_pid), "child process survived the tree kill"


def test_normal_process_completes_without_timeout():
    argv = [sys.executable, "-c", "print('ok')"]
    result = runner.run_process(argv, timeout_s=30)
    assert result["timed_out"] is False
    assert result["exit_code"] == 0
    assert "ok" in result["stdout"]
