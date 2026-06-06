"""Environment self-check for onboarding: `doctor`.

Read-only probes of the tools and config PDD needs, with a fix hint per failure.
Split into "critical" (PDD can't run) and "sandbox" (sandboxed stages can't run).
"""
import shutil
import subprocess
import sys

from . import config, sandbox


def _which(name: str):
    return shutil.which(name)


def _run(args, timeout: int = 30):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


def _check(name, status, detail="", hint=""):
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def run_checks() -> list:
    checks = [_check("python", "ok", sys.version.split()[0])]

    if _which("git"):
        checks.append(_check("git", "ok", _run(["git", "--version"])[1]))
    else:
        checks.append(_check("git", "fail", "not found", "install git"))

    qwen_path = _which("qwen")
    if qwen_path:
        # Use the resolved path: on Windows qwen is a .CMD and bare "qwen" does
        # not resolve via CreateProcess (only .exe is auto-appended).
        rc, out = _run([qwen_path, "--version"])
        checks.append(_check("qwen", "ok", out) if rc == 0
                      else _check("qwen", "warn", "present but --version failed"))
    else:
        checks.append(_check("qwen", "fail", "not on PATH", "install qwen-code and add it to PATH"))

    rc, _o = _run([sys.executable, "-m", "pytest", "--version"])
    checks.append(_check("pytest", "ok") if rc == 0
                  else _check("pytest", "warn", "not importable", "pip install pytest (host test runs)"))

    creds = config.model_env()
    missing = [k for k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL") if not creds.get(k)]
    if missing:
        checks.append(_check("model creds", "fail", f"missing: {', '.join(missing)}",
                             "set them in .qwen/.env (see .qwen/.env.example)"))
    else:
        checks.append(_check("model creds", "ok",
                             f"model={creds['OPENAI_MODEL']} base={creds['OPENAI_BASE_URL']}"))

    # --- sandbox stack ---
    if not _which("docker"):
        checks.append(_check("docker", "fail", "not installed",
                             "install Docker (or PDD_ALLOW_UNSANDBOXED=1 for trusted local-only)"))
        return checks
    if not sandbox.docker_available():
        checks.append(_check("docker daemon", "fail", "not running", "start Docker Desktop"))
        return checks
    checks.append(_check("docker daemon", "ok", "up"))

    rc, _o = _run(["docker", "image", "inspect", config.SANDBOX_IMAGE])
    checks.append(_check("sandbox image", "ok", config.SANDBOX_IMAGE) if rc == 0
                  else _check("sandbox image", "warn", "not built",
                              "python -m orchestrator.cli sandbox-build"))

    internal = sandbox.network_is_internal()
    if internal is True:
        checks.append(_check("sandbox network", "ok", f"{config.SANDBOX_NETWORK} (internal)"))
    elif internal is False:
        checks.append(_check("sandbox network", "fail",
                             f"{config.SANDBOX_NETWORK} exists but is NOT internal",
                             f"docker network rm {config.SANDBOX_NETWORK} && sandbox-network"))
    else:
        checks.append(_check("sandbox network", "warn", "missing",
                             "python -m orchestrator.cli sandbox-network"))

    _rc, out = _run(sandbox.proxy_status_argv())
    if config.SANDBOX_PROXY_NAME in (out or ""):
        checks.append(_check("egress proxy", "ok", f"{config.SANDBOX_PROXY_NAME} running"))
    else:
        checks.append(_check("egress proxy", "warn", "not running",
                             "python -m orchestrator.cli proxy-up"))

    return checks


def has_failures(checks: list) -> bool:
    return any(c["status"] == "fail" for c in checks)


def format_checks(checks: list) -> str:
    sym = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
    lines = []
    for c in checks:
        line = f"{sym[c['status']]} {c['name']}"
        if c.get("detail"):
            line += f": {c['detail']}"
        if c["status"] != "ok" and c.get("hint"):
            line += f"  -> {c['hint']}"
        lines.append(line)
    return "\n".join(lines)
