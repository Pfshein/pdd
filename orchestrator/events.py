"""Structured per-job event log.

This is the cross-cutting observability stream. Existing artifacts stay useful
for humans, while events.jsonl gives tools one stable timeline to parse.
"""
import json
import time

from . import state as state_mod

EVENTS_FILE = "events.jsonl"

# Live subscribers (e.g. the CLI console printer). Each gets every recorded row.
_subscribers = []


def subscribe(fn) -> None:
    if fn not in _subscribers:
        _subscribers.append(fn)


def unsubscribe(fn) -> None:
    if fn in _subscribers:
        _subscribers.remove(fn)


def _clean(obj):
    """Keep event rows JSON-serializable and compact."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def record(job: str, event: str, **fields) -> dict:
    """Append an event row and return it."""
    row = {"ts": time.time(), "job": state_mod.validate_job_id(job), "event": event}
    row.update({k: _clean(v) for k, v in fields.items() if v is not None})
    path = state_mod.job_dir(job) / EVENTS_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    for fn in list(_subscribers):  # live consumers must never break the run
        try:
            fn(row)
        except Exception:
            pass
    return row


def read(job: str) -> list[dict]:
    path = state_mod.job_dir(job) / EVENTS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

