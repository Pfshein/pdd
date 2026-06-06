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
from pathlib import Path
import shutil
import subprocess
import time
import uuid

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


def docker_build_argv(*, image: str | None = None, dockerfile=None, context=None,
                      qwen_package: str | None = None) -> list:
    """Assemble `docker build` for the sandbox image."""
    image = image or config.SANDBOX_IMAGE
    dockerfile = Path(dockerfile) if dockerfile else config.ROOT / "sandbox" / "Dockerfile"
    context = Path(context) if context else config.ROOT
    argv = ["docker", "build", "-t", image, "-f", str(dockerfile)]
    if qwen_package:
        argv += ["--build-arg", f"QWEN_NPM_PACKAGE={qwen_package}"]
    argv += [str(context)]
    return argv


def docker_network_create_argv(*, network: str | None = None) -> list:
    """Create the sandbox network as INTERNAL (no direct route to the internet)."""
    network = network or config.SANDBOX_NETWORK
    return ["docker", "network", "create", "--internal", network]


def docker_network_inspect_argv(*, network: str | None = None) -> list:
    network = network or config.SANDBOX_NETWORK
    return ["docker", "network", "inspect", network]


def network_is_internal(network: str | None = None) -> bool | None:
    """True/False if the network exists, None if it does not / docker errors."""
    network = network or config.SANDBOX_NETWORK
    try:
        r = subprocess.run(
            ["docker", "network", "inspect", "-f", "{{.Internal}}", network],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip().lower() == "true"


# --- Egress allowlist proxy (squid sidecar) -------------------------------
def render_squid_conf(hosts) -> str:
    """squid.conf that allows CONNECT only to the allowlisted hosts on 443."""
    acls = "\n".join(f"acl allowed_dst dstdomain .{h}" for h in hosts) or \
        "# (no allowlisted hosts configured)"
    return (
        f"acl SSL_ports port 443\n"
        f"acl CONNECT method CONNECT\n"
        f"{acls}\n"
        f"http_access deny CONNECT !SSL_ports\n"
        f"http_access allow allowed_dst\n"
        f"http_access deny all\n"
        f"http_port {config.SANDBOX_PROXY_PORT}\n"
        f"shutdown_lifetime 1 second\n"
    )


def write_squid_conf(path=None, hosts=None) -> str:
    """Render the squid config to disk; return the path. Used by `proxy-up`."""
    path = Path(path or config.SANDBOX_PROXY_CONF)
    hosts = hosts if hosts is not None else config.model_host_allowlist()
    path.write_text(render_squid_conf(hosts), encoding="utf-8")
    return str(path)


def proxy_run_argv(*, conf_path=None, name=None, network=None, image=None) -> list:
    """`docker run -d` for the squid sidecar on the internal network."""
    name = name or config.SANDBOX_PROXY_NAME
    network = network or config.SANDBOX_NETWORK
    image = image or config.SANDBOX_PROXY_IMAGE
    conf_path = os.path.abspath(str(conf_path or config.SANDBOX_PROXY_CONF))
    return [
        "docker", "run", "-d", "--rm", "--name", name,
        "--network", network,
        "-v", f"{conf_path}:/etc/squid/squid.conf:ro",
        image,
    ]


def proxy_connect_external_argv(*, name=None, network=None) -> list:
    """Attach the proxy to an egress-capable network so it can reach the host."""
    name = name or config.SANDBOX_PROXY_NAME
    network = network or config.SANDBOX_EXTERNAL_NETWORK
    return ["docker", "network", "connect", network, name]


def proxy_status_argv(*, name=None) -> list:
    name = name or config.SANDBOX_PROXY_NAME
    return ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}} {{.Status}}"]


def proxy_smoke_argv(host, *, allowed: bool, network=None) -> list:
    """Curl a host THROUGH the proxy from inside the internal network.

    allowed=True expects success (model host); allowed=False expects the proxy to
    deny egress (any other host).
    """
    network = network or config.SANDBOX_NETWORK
    proxy = config.SANDBOX_HTTPS_PROXY
    return [
        "docker", "run", "--rm", "--network", network, config.SANDBOX_IMAGE,
        "sh", "-lc", f"curl -sS -m 10 -o /dev/null -w '%{{http_code}}' -x {proxy} https://{host}/",
    ]


def docker_smoke_argv(worktree, *, network: str | None = None) -> list:
    """A cheap smoke command that proves /work is writable inside the sandbox."""
    return docker_run_argv(
        ["sh", "-lc", "python --version && node --version && qwen --version && touch /work/.pdd-smoke"],
        worktree,
        network=network,
    )


def docker_run_argv(container_cmd, worktree, *, env_passthrough=DEFAULT_ENV_PASSTHROUGH,
                    network=None, extra=None, name=None):
    """Assemble a locked-down `docker run` argv. No secret values embedded.

    --init reaps zombie processes inside the container. A unique --name lets the
    caller stop the container if the outer watchdog has to kill the docker client
    (killing the client does NOT stop the daemon-owned container).
    """
    network = network or config.SANDBOX_NETWORK
    argv = ["docker", "run", "--rm", "-i", "--init"]
    if config.SANDBOX_USER:
        argv += ["--user", config.SANDBOX_USER]  # non-root inside the container
    if name:
        argv += ["--name", str(name)]
    argv += [
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


def _force_remove_container(name: str) -> None:
    """Best-effort stop+remove of a container by name. Never raises."""
    for cmd in (["docker", "kill", name], ["docker", "rm", "-f", name]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass


def run_in_sandbox(container_cmd, worktree, *, stdin=None, timeout=None,
                   env_passthrough=DEFAULT_ENV_PASSTHROUGH, network=None) -> dict:
    """Run container_cmd inside the sandbox container; return run_process dict.

    The container gets a unique --name. If the outer watchdog times out,
    run_process kills the `docker run` CLIENT, but the daemon keeps the container
    running (SIGKILL is not proxied, --rm never fires). We therefore explicitly
    docker kill/rm it, so the hard-timeout guarantee does not silently leak a
    running container for the most dangerous stages.
    """
    from .runner import run_process, stage_env  # lazy: avoid import cycle

    name = f"pdd-{uuid.uuid4().hex[:16]}"
    argv = docker_run_argv(
        container_cmd, worktree, env_passthrough=env_passthrough, network=network, name=name
    )
    result = run_process(argv, env=stage_env(), timeout_s=timeout, stdin_input=stdin)
    if result.get("timed_out"):
        _force_remove_container(name)
    result["container"] = name
    return result
