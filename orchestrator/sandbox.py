"""Security boundary for executing stages: run the agent inside a Docker container.

The agent runs with --yolo and can execute arbitrary shell. worktree isolates
the *files* of the change, not the *process*; review is a quality gate, not a
security control. The container is the actual boundary:

  - mounts ONLY the job worktree (host $HOME, creds, other repos are invisible),
  - inherits NO host env except the OPENAI_* creds we pass by name,
  - `--cap-drop ALL`, `--read-only` rootfs, `--security-opt no-new-privileges`,
  - resource limits, and egress restricted to an allowlist proxy.

Invariant is fail-closed: no Docker and no explicit override -> executing stages
refuse to start (raise SandboxUnavailable).
"""
import os
import shutil
import subprocess
import time

from . import config


class SandboxUnavailable(RuntimeError):
    """Raised when isolation is required but unavailable and not overridden."""


def record_unsandboxed_override(job: str, stage: str) -> None:
    """Append a loud artifact when a dangerous stage runs on the host."""
    from . import state as state_mod  # lazy: avoid import cycle

    path = state_mod.job_dir(job) / "SECURITY.txt"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {stage} ran UNSANDBOXED\n"
            "PDD_ALLOW_UNSANDBOXED=1 or PDD_REQUIRE_SANDBOX=0 allowed a stage "
            "that can execute arbitrary shell/project code to run with host "
            "user privileges. Use only for trusted local debugging.\n\n"
        )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=20
        )
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_ready() -> str:
    """Return the isolation mode for executing stages.

    'docker'       -> run inside a container (the real boundary).
    'UNSANDBOXED'  -> explicit, loud opt-out (PDD_ALLOW_UNSANDBOXED=1) or sandbox
                      not required by config.
    Raises SandboxUnavailable otherwise (fail-closed).
    """
    if docker_available():
        return "docker"
    if config.ALLOW_UNSANDBOXED or not config.REQUIRE_SANDBOX:
        return "UNSANDBOXED"
    raise SandboxUnavailable(
        "No running Docker daemon and PDD_ALLOW_UNSANDBOXED is not set. Executing "
        "stages (coder/tester/tests) run arbitrary shell under --yolo and refuse to "
        "start without isolation. Start Docker, or set PDD_ALLOW_UNSANDBOXED=1 for "
        "trusted local debugging (insecure)."
    )


# Credentials forwarded into the container BY NAME (docker reads the value from
# this process's env), so the secret value never appears in any argv.
DEFAULT_ENV_PASSTHROUGH = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def docker_run_argv(container_cmd, worktree, *, env_passthrough=DEFAULT_ENV_PASSTHROUGH,
                    network=None, extra=None):
    """Assemble a locked-down `docker run` argv. No secret values embedded."""
    network = network or config.SANDBOX_NETWORK
    argv = [
        "docker", "run", "--rm", "-i",
        "-v", f"{os.path.abspath(str(worktree))}:/work",
        "-w", "/work",
        "--read-only", "--tmpfs", "/tmp:exec",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(config.SANDBOX_PIDS_LIMIT),
        "--memory", config.SANDBOX_MEMORY,
        "--cpus", str(config.SANDBOX_CPUS),
        "--network", network,
        "-e", "HOME=/tmp/pdd-home",
        "-e", "XDG_CACHE_HOME=/tmp/pdd-cache",
        "-e", "NPM_CONFIG_CACHE=/tmp/npm-cache",
        "-e", "PIP_CACHE_DIR=/tmp/pip-cache",
    ]
    for key in env_passthrough:
        argv += ["-e", key]  # value taken from the docker process env, not argv
    if config.SANDBOX_HTTPS_PROXY:
        argv += [
            "-e", f"HTTPS_PROXY={config.SANDBOX_HTTPS_PROXY}",
            "-e", f"HTTP_PROXY={config.SANDBOX_HTTPS_PROXY}",
        ]
    if extra:
        argv += list(extra)
    argv += [config.SANDBOX_IMAGE]
    argv += list(container_cmd)
    return argv


def run_in_sandbox(container_cmd, worktree, *, stdin=None, timeout=None,
                   env_passthrough=DEFAULT_ENV_PASSTHROUGH) -> dict:
    """Run container_cmd inside the sandbox container; return run_process dict."""
    from .runner import run_process, stage_env  # lazy: avoid import cycle

    argv = docker_run_argv(container_cmd, worktree, env_passthrough=env_passthrough)
    return run_process(argv, env=stage_env(), timeout_s=timeout, stdin_input=stdin)
