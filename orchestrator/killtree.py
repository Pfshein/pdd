"""Process-group spawn + whole-tree kill, cross-platform.

The #1 source of hangs is a headless agent (or a tool it spawned) that never
exits. We must kill the *entire* tree, not just the root pid.
"""
import os
import signal
import subprocess

IS_WINDOWS = os.name == "nt"


def popen_kwargs() -> dict:
    """Kwargs that put the child into its own group/session for tree-kill."""
    if IS_WINDOWS:
        # New process group so we can taskkill /T the whole tree.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    # New session (setsid) so os.killpg hits the agent + its tool children.
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill proc and every descendant. Idempotent; never raises."""
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def is_pid_alive(pid: int) -> bool:
    """Best-effort liveness check, cross-platform (for tests/reaper)."""
    if IS_WINDOWS:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k = ctypes.windll.kernel32
        handle = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
        k.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
