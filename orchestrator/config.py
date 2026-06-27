"""Configuration: budgets, timeouts, model credentials, paths.

Plain data + tiny helpers. No classes.
"""
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

# --- Paths ----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
# Worktrees live OUTSIDE the project tree: a worktree nested under ROOT would let
# a tool run inside it (e.g. pytest) discover ROOT/pytest.ini as an ancestor
# config and hijack rootdir/testpaths.
WORKTREES_DIR = Path(tempfile.gettempdir()) / "pdd-worktrees"
SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# --- Loop control ---------------------------------------------------------
GLOBAL_STEP_CAP = 30
# Max total invocations per return-target stage (initial run + retries).
DEFAULT_BUDGETS = {
    "ARCHITECT": 2,
    "CODER": 4,
    "TESTER": 3,
}
# How many recent signatures to remember per target for the no-progress detector.
SIGNATURE_HISTORY = 3

# --- Process lifecycle ----------------------------------------------------
# Inner guard handed to qwen (--max-wall-time, seconds).
STAGE_WALL_TIME_S = 600
# Outer watchdog: external kill margin on top of the inner guard.
STAGE_KILL_MARGIN_S = 60
# Cumulative tool-call cap per stage (--max-tool-calls).
STAGE_MAX_TOOL_CALLS = 40
# Non-editing stages (architect, reviewer) don't roam the filesystem; cheaper.
STAGE_EXPLORE_MAX_TOOL_CALLS = 6
# wall-time budget exceeded means "slow", not "stuck": retry once, bigger budget.
STAGE_WALL_RETRY_FACTOR = 2
STAGE_WALL_MAX_S = 1200
# Whole-job TTL; reaper kills anything still alive past this.
JOB_TTL_S = 3600

# --- Job queue (durable file queue under runs/queue) ----------------------
# One JSON record per job under RUNS_DIR/queue/<job>.json. The dir is derived
# from RUNS_DIR at call time (queue.queue_dir) so tests can monkeypatch RUNS_DIR.
# A lease older than this (seconds) is considered stale and reclaimable.
QUEUE_LEASE_TTL_S = int(os.environ.get("PDD_QUEUE_LEASE_TTL_S", str(JOB_TTL_S)))

# --- Test command (deterministic TEST_RUN) --------------------------------
# `python -m pytest` instead of bare `pytest`: robust when pytest.exe is not on PATH.
TEST_COMMAND = os.environ.get("PIPELINE_TEST_COMMAND", "python -m pytest -q")
SETUP_COMMAND = os.environ.get("PIPELINE_SETUP_COMMAND", "")
TEST_TIMEOUT_S = 300
SETUP_TIMEOUT_S = int(os.environ.get("PDD_SETUP_TIMEOUT_S", "600"))

# --- Per-stage wall-time budgets (seconds, inner qwen guard) --------------
STAGE_WALL_TIME = {
    "ARCHITECT": 180,
    "CODER": 420,
    "TESTER": 300,
    "CODE_REVIEW": 180,
    "FINAL_REVIEW": 180,
    "INTAKE": 120,
}

# --- Sandbox (the security boundary for executing stages) -----------------
# Executing stages (CODER/TESTER edits, TEST_RUN) run with --yolo and can run
# arbitrary shell. The ONLY real boundary is an OS-level sandbox: a Docker
# container with just the worktree mounted, no host env, egress allowlisted.
# worktree = file isolation (not a boundary); review = quality gate (not a boundary).
REQUIRE_SANDBOX = os.environ.get("PDD_REQUIRE_SANDBOX", "1") == "1"
ALLOW_UNSANDBOXED = os.environ.get("PDD_ALLOW_UNSANDBOXED") == "1"
SANDBOX_IMAGE = os.environ.get("PDD_SANDBOX_IMAGE", "pdd-sandbox:latest")
# Agent containers join this INTERNAL network (no direct route to the internet).
# Their only way out is the allowlist proxy below.
SANDBOX_NETWORK = os.environ.get("PDD_SANDBOX_NETWORK", "pdd-internal")
# The proxy also attaches here to actually reach the allowlisted model host.
SANDBOX_EXTERNAL_NETWORK = os.environ.get("PDD_SANDBOX_EXTERNAL_NETWORK", "bridge")
SANDBOX_PIDS_LIMIT = int(os.environ.get("PDD_SANDBOX_PIDS", "512"))
SANDBOX_MEMORY = os.environ.get("PDD_SANDBOX_MEMORY", "2g")
SANDBOX_CPUS = os.environ.get("PDD_SANDBOX_CPUS", "2")
# Run the agent container as this non-root user (uid:gid). Empty disables --user.
SANDBOX_USER = os.environ.get("PDD_SANDBOX_USER", "1000:1000")
# Optional custom seccomp profile. Empty keeps Docker's built-in default profile.
SANDBOX_SECCOMP_PROFILE = os.environ.get("PDD_SECCOMP_PROFILE", "")

# --- Egress allowlist proxy ----------------------------------------------
# Agents have NO direct egress; they reach ONLY the model endpoint, via a squid
# sidecar that allowlists the model host(s). The agent container gets HTTPS_PROXY.
SANDBOX_PROXY_NAME = os.environ.get("PDD_SANDBOX_PROXY_NAME", "pdd-proxy")
SANDBOX_PROXY_IMAGE = os.environ.get("PDD_SANDBOX_PROXY_IMAGE", "ubuntu/squid:latest")
SANDBOX_PROXY_PORT = int(os.environ.get("PDD_SANDBOX_PROXY_PORT", "3128"))
SANDBOX_PROXY_CONF = Path(
    os.environ.get("PDD_SANDBOX_PROXY_CONF", str(Path(tempfile.gettempdir()) / "pdd-proxy-squid.conf"))
)
SANDBOX_HTTPS_PROXY = os.environ.get(
    "PDD_SANDBOX_HTTPS_PROXY", f"http://{SANDBOX_PROXY_NAME}:{SANDBOX_PROXY_PORT}"
)

# --- Dependency setup sandbox --------------------------------------------
# Project dependency installs are intentionally separate from TEST_RUN:
# setup may need package-registry egress; tests run with --network none.
SANDBOX_SETUP_NETWORK = os.environ.get("PDD_SETUP_NETWORK", SANDBOX_NETWORK)
SANDBOX_SETUP_PROXY_NAME = os.environ.get("PDD_SETUP_PROXY_NAME", "pdd-setup-proxy")
SANDBOX_SETUP_PROXY_CONF = Path(
    os.environ.get(
        "PDD_SETUP_PROXY_CONF",
        str(Path(tempfile.gettempdir()) / "pdd-setup-proxy-squid.conf"),
    )
)
SANDBOX_SETUP_HTTPS_PROXY = os.environ.get(
    "PDD_SETUP_HTTPS_PROXY", f"http://{SANDBOX_SETUP_PROXY_NAME}:{SANDBOX_PROXY_PORT}"
)


def setup_host_allowlist() -> list:
    """Hosts the dependency setup proxy may reach."""
    override = os.environ.get("PDD_SETUP_HOST_ALLOWLIST")
    if override:
        return [h.strip() for h in override.split(",") if h.strip()]
    return [
        "pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "github.com",
        "objects.githubusercontent.com",
    ]


def model_host_allowlist() -> list:
    """Hosts the sandbox proxy may reach. Defaults to the model endpoint host."""
    override = os.environ.get("PDD_MODEL_HOST_ALLOWLIST")
    if override:
        return [h.strip() for h in override.split(",") if h.strip()]
    base = model_env().get("OPENAI_BASE_URL", "")
    host = urlparse(base).hostname
    return [host] if host else []


# --- Model credentials ----------------------------------------------------
def load_env_file(path: Path) -> dict:
    """Parse a simple KEY="value" .env file. Tolerant of quotes/blank lines."""
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def model_env() -> dict:
    """Resolve OPENAI_* creds: real environment wins, else .qwen/.env."""
    file_env = load_env_file(ROOT / ".qwen" / ".env")
    env = {}
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        env[key] = os.environ.get(key) or file_env.get(key, "")
    return env
