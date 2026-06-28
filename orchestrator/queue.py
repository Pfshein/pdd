"""Durable file-based job queue (V1).

One JSON record per job under RUNS_DIR/queue/<job>.json. Plain data + functions,
no classes, no SQLite. Writes are atomic-ish: a unique temp file then os.replace,
so a record is never observed half-written.

State only lives in files; this mirrors the rest of PDD (state.py, events.py).
"""
import json
import os
import time
import uuid
from pathlib import Path

from . import config, state

# Lifecycle statuses a queue record can hold.
QUEUED = "queued"
LEASED = "leased"
RUNNING = "running"
DONE = "done"
NEEDS_HUMAN = "needs_human"
FAILED = "failed"

ALLOWED_STATUSES = (QUEUED, LEASED, RUNNING, DONE, NEEDS_HUMAN, FAILED)
# A job still owning a slot (must not be enqueued twice / picked up twice).
ACTIVE_STATUSES = (QUEUED, LEASED, RUNNING)
# A job that has finished one way or another.
TERMINAL_STATUSES = (DONE, NEEDS_HUMAN, FAILED)
# Statuses that hold a worker lease and can therefore go stale.
LEASED_STATUSES = (LEASED, RUNNING)


def queue_dir() -> Path:
    """RUNS_DIR/queue, created on demand. Read RUNS_DIR live so tests can patch it."""
    d = config.RUNS_DIR / "queue"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record_path(job: str) -> Path:
    job = state.validate_job_id(job)
    return queue_dir() / f"{job}.json"


def _write_record(record: dict) -> None:
    """Atomic-ish: write a unique temp file in the same dir, then replace."""
    path = _record_path(record["job"])
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def enqueue(
    job: str,
    repo: str,
    task: str,
    meta: str,
    base_ref: str = "HEAD",
    test_command: str | None = None,
    setup_command: str | None = None,
    now: float | None = None,
) -> dict:
    """Write one queued record for `job`. Reject a job that is still active."""
    job = state.validate_job_id(job)
    now = time.time() if now is None else now
    path = _record_path(job)
    if path.exists():
        existing = _read_record(path)
        if existing.get("status") in ACTIVE_STATUSES:
            raise ValueError(
                f"job {job!r} is already in the queue (status={existing.get('status')})"
            )
    record = {
        "job": job,
        "repo": str(repo),
        "task": str(task),
        "meta": str(meta),
        "base_ref": base_ref,
        "test_command": test_command,
        "setup_command": setup_command,
        "status": QUEUED,
        "created_ts": now,
        "updated_ts": now,
        "lease": None,
    }
    _write_record(record)
    return record


def get(job: str) -> dict | None:
    """Return the record for `job`, or None if it is not queued."""
    path = _record_path(job)
    if not path.exists():
        return None
    return _read_record(path)


def list_jobs() -> list[dict]:
    """All records, oldest first (created_ts, then job id as a stable tiebreak)."""
    d = config.RUNS_DIR / "queue"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(_read_record(p))
        except (OSError, ValueError):
            # A half-written/corrupt record must not break listing the rest.
            continue
    out.sort(key=lambda r: (r.get("created_ts", 0.0), r.get("job", "")))
    return out


def acquire(worker: str | None = None, now: float | None = None) -> dict | None:
    """Lease the oldest queued job. Return the leased record, or None if idle."""
    now = time.time() if now is None else now
    for record in list_jobs():
        if record.get("status") != QUEUED:
            continue
        record["status"] = LEASED
        record["updated_ts"] = now
        record["lease"] = {"token": uuid.uuid4().hex, "worker": worker, "ts": now}
        _write_record(record)
        return record
    return None


def _set_status(job: str, status: str, now: float | None, lease="keep") -> dict:
    now = time.time() if now is None else now
    path = _record_path(job)
    if not path.exists():
        raise FileNotFoundError(f"no queue record for job {job!r}")
    record = _read_record(path)
    record["status"] = status
    record["updated_ts"] = now
    if lease != "keep":
        record["lease"] = lease
    _write_record(record)
    return record


def mark_running(job: str, now: float | None = None) -> dict:
    """Move a leased job to running, keeping its lease."""
    return _set_status(job, RUNNING, now)


def release(job: str, status: str, now: float | None = None) -> dict:
    """Mark a final status and drop the lease."""
    if status not in TERMINAL_STATUSES:
        raise ValueError(
            f"release status must be one of {TERMINAL_STATUSES}, got {status!r}"
        )
    return _set_status(job, status, now, lease=None)


def requeue(job: str, now: float | None = None) -> dict:
    """Return a record to queued and drop its lease (e.g. after a stale lease)."""
    return _set_status(job, QUEUED, now, lease=None)


def is_stale(record: dict, now: float | None = None, ttl: float | None = None) -> bool:
    """True if a leased/running record's lease is older than ttl."""
    now = time.time() if now is None else now
    ttl = config.QUEUE_LEASE_TTL_S if ttl is None else ttl
    if record.get("status") not in LEASED_STATUSES:
        return False
    lease = record.get("lease") or {}
    ts = lease.get("ts", record.get("updated_ts", 0.0))
    return (now - ts) > ttl


def reclaim_stale(now: float | None = None, ttl: float | None = None) -> list[str]:
    """Return stale leased/running jobs to queued. Returns reclaimed job ids."""
    now = time.time() if now is None else now
    reclaimed = []
    for record in list_jobs():
        if is_stale(record, now=now, ttl=ttl):
            record["status"] = QUEUED
            record["updated_ts"] = now
            record["lease"] = None
            _write_record(record)
            reclaimed.append(record["job"])
    return reclaimed
