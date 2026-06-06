"""Job state: plain dict + file persistence (JSON + JSONL).

state.json   - current node, budgets, counters, signatures
transitions.jsonl - append-only trace of node transitions
attempts.jsonl    - append-only compressed "what we already tried" log
"""
import json
import re
import time
from pathlib import Path

from . import config, graph

JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def validate_job_id(job: str) -> str:
    """Job ids become artifact paths and branch suffixes; keep them path-safe."""
    if not isinstance(job, str) or not JOB_ID_RE.fullmatch(job):
        raise ValueError(
            "job id must be 1-128 chars of letters, numbers, dot, underscore, "
            "or hyphen, and must not contain path separators"
        )
    return job


def new_state(job: str, budgets: dict | None = None, global_step_cap: int | None = None) -> dict:
    """Create a fresh job state. Pure data."""
    job = validate_job_id(job)
    budgets = budgets or config.DEFAULT_BUDGETS
    return {
        "job": job,
        "node": graph.INTAKE,
        "global_steps": 0,
        "global_step_cap": global_step_cap or config.GLOBAL_STEP_CAP,
        "budgets": {stage: {"used": 0, "max": mx} for stage, mx in budgets.items()},
        "signatures": {stage: [] for stage in budgets},
        "has_plan": False,
        "nits": [],
    }


# --- Persistence ----------------------------------------------------------
def job_dir(job: str) -> Path:
    job = validate_job_id(job)
    d = config.RUNS_DIR / job
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_state(state: dict) -> None:
    path = job_dir(state["job"]) / "state.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def load_state(job: str) -> dict:
    path = job_dir(job) / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_transition(job: str, frm: str, to: str, reason: str) -> None:
    _append_jsonl(
        job_dir(job) / "transitions.jsonl",
        {"ts": time.time(), "from": frm, "to": to, "reason": reason},
    )


def record_attempt(job: str, stage: str, note: str, signature: str | None = None, **extra) -> None:
    record = {"ts": time.time(), "stage": stage, "note": note, "signature": signature}
    record.update({k: v for k, v in extra.items() if v is not None})  # e.g. status, limit
    _append_jsonl(job_dir(job) / "attempts.jsonl", record)


def read_attempts(job: str) -> list[dict]:
    path = job_dir(job) / "attempts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
