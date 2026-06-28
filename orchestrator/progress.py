"""Live console progress for `run`/`resume`/`retry`.

Subscribes to the events stream (orchestrator/events.py) and prints one readable,
ASCII-only line per stage so a blocking run is not a silent wait. Goes to stderr
so stdout stays clean for the final summary.
"""
import sys

_FIELDS = ("status", "test_status", "test_exit_code", "issues", "limit", "sandbox", "budget")


def format_event(row: dict):
    """One progress line for a row, or None to skip (e.g. raw transitions)."""
    ev = row.get("event")
    job = row.get("job")
    if ev == "job_start":
        return f"== {job}: start (node={row.get('node')}) =="
    if ev == "stage_start":
        return f"   .. {row.get('stage')} running"
    if ev == "stage_end":
        dur = (row.get("duration_ms") or 0) / 1000.0
        bits = [f"{k}={row[k]}" for k in _FIELDS if k in row]
        tail = ("  [" + " ".join(bits) + "]") if bits else ""
        return f"   ok {row.get('stage')} {dur:.1f}s -> {row.get('next')}{tail}"
    if ev == "job_end":
        return f"== {job}: finished -> {row.get('node')} =="
    return None  # 'transition' is redundant with stage_end's arrow


def console_printer(stream=None):
    stream = stream or sys.stderr

    def printer(row):
        line = format_event(row)
        if line:
            print(line, file=stream, flush=True)

    return printer
