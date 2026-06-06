"""Configuration: budgets, timeouts, model credentials, paths.

Plain data + tiny helpers. No classes.
"""
import os
import tempfile
from pathlib import Path

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
# Whole-job TTL; reaper kills anything still alive past this.
JOB_TTL_S = 3600

# --- Test command (deterministic TEST_RUN) --------------------------------
# `python -m pytest` instead of bare `pytest`: robust when pytest.exe is not on PATH.
TEST_COMMAND = os.environ.get("PIPELINE_TEST_COMMAND", "python -m pytest -q")
TEST_TIMEOUT_S = 300

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
SANDBOX_NETWORK = os.environ.get("PDD_SANDBOX_NETWORK", "pdd-egress")
SANDBOX_PIDS_LIMIT = int(os.environ.get("PDD_SANDBOX_PIDS", "512"))
SANDBOX_MEMORY = os.environ.get("PDD_SANDBOX_MEMORY", "2g")
SANDBOX_CPUS = os.environ.get("PDD_SANDBOX_CPUS", "2")
# HTTPS proxy reachable from SANDBOX_NETWORK; the egress allowlist lives there.
SANDBOX_HTTPS_PROXY = os.environ.get("PDD_SANDBOX_HTTPS_PROXY", "")


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
