"""Deterministic test runner. Red test -> coder, with no LLM opinion."""
import hashlib
import subprocess

from . import artifacts, config, sandbox


def _run_setup(job: str, worktree, command: str, mode: str) -> dict:
    """Install project deps before tests, with separate egress from TEST_RUN."""
    if mode == "docker":
        res = sandbox.run_in_sandbox(
            ["sh", "-lc", command],
            worktree,
            timeout=config.SETUP_TIMEOUT_S,
            network=config.SANDBOX_SETUP_NETWORK,
            proxy_url=config.SANDBOX_SETUP_HTTPS_PROXY,
        )
        exit_code = None if res.get("timed_out") else res["exit_code"]
        log = (
            f"setup command timed out after {config.SETUP_TIMEOUT_S}s"
            if res.get("timed_out")
            else (res["stdout"] or "") + "\n" + (res["stderr"] or "")
        )
    else:
        sandbox.record_unsandboxed_override(job, "SETUP_COMMAND")
        try:
            proc = subprocess.run(
                command,
                cwd=str(worktree),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.SETUP_TIMEOUT_S,
            )
            exit_code = proc.returncode
            log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            exit_code = None
            log = f"setup command timed out after {config.SETUP_TIMEOUT_S}s"

    result = {
        "status": "green" if exit_code == 0 else "red",
        "command": command,
        "exit_code": exit_code,
        "log_tail": log[-3000:],
    }
    artifacts.write_json(job, "setup_result.json", result)
    return result


def run_tests(job: str, worktree, command: str | None = None,
              setup_command: str | None = None) -> dict:
    """Run the project's tests. Test code is arbitrary code, so it executes in
    the SAME sandbox as the editor stages (fail-closed via ensure_ready)."""
    command = command or config.TEST_COMMAND
    setup_command = setup_command if setup_command is not None else config.SETUP_COMMAND
    mode = sandbox.ensure_ready()

    if setup_command:
        setup = _run_setup(job, worktree, setup_command, mode)
        if setup["status"] != "green":
            result = {
                "status": "red",
                "phase": "setup",
                "command": command,
                "setup_command": setup_command,
                "setup": setup,
                "exit_code": setup["exit_code"],
                "log_tail": setup["log_tail"],
            }
            artifacts.write_json(job, "test_result.json", result)
            return result

    if mode == "docker":
        # Tests don't call the model -> NO network at all. This is the stage most
        # likely to execute untrusted project code (conftest/tests), so we strip
        # its egress surface entirely.
        res = sandbox.run_in_sandbox(
            ["sh", "-lc", command], worktree, timeout=config.TEST_TIMEOUT_S, network="none"
        )
        if res.get("timed_out"):
            exit_code = None
            log = f"test command timed out after {config.TEST_TIMEOUT_S}s"
        else:
            exit_code = res["exit_code"]
            log = (res["stdout"] or "") + "\n" + (res["stderr"] or "")
    else:
        sandbox.record_unsandboxed_override(job, "TEST_RUN")
        try:
            proc = subprocess.run(
                command,
                cwd=str(worktree),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=config.TEST_TIMEOUT_S,
            )
            exit_code = proc.returncode
            log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            exit_code = None
            log = f"test command timed out after {config.TEST_TIMEOUT_S}s"

    status = "green" if exit_code == 0 else "red"
    result = {
        "status": status,
        "command": command,
        "setup_command": setup_command or None,
        "exit_code": exit_code,
        "log_tail": log[-3000:],
    }
    artifacts.write_json(job, "test_result.json", result)
    return result


def failure_signature(result: dict) -> str | None:
    """Fingerprint of a red run, so a repeated identical failure stalls the loop."""
    if result.get("status") != "red":
        return None
    return hashlib.sha256((result.get("log_tail") or "").encode("utf-8")).hexdigest()[:16]
