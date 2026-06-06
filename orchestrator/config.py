"""Configuration: budgets, timeouts, model credentials, paths.

Plain data + tiny helpers. No classes.
"""
import os
from pathlib import Path

# --- Paths ----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
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
TEST_COMMAND = os.environ.get("PIPELINE_TEST_COMMAND", "pytest -q")

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
