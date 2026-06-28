"""Live console progress for `run`/`resume`/`retry`.

Subscribes to the events stream (orchestrator/events.py) and prints one readable,
ASCII-only line per stage so a blocking run is not a silent wait. Goes to stderr
so stdout stays clean for the final summary.
"""
import sys

from . import artifacts

_FIELDS = ("status", "error", "test_status", "test_exit_code", "issues", "limit", "sandbox", "budget")
_SNIPPET_CHARS = 2000
_SNIPPET_LINES = 40


def _snippet(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    truncated = False
    lines = text.splitlines()
    if len(lines) > _SNIPPET_LINES:
        lines = lines[:_SNIPPET_LINES]
        truncated = True
    out = "\n".join(lines)
    if len(out) > _SNIPPET_CHARS:
        out = out[:_SNIPPET_CHARS].rstrip()
        truncated = True
    if truncated:
        out += "\n... <truncated>"
    return out


def artifact_log_lines(row: dict) -> list[str]:
    """Extra live-only log lines for artifacts that explain the next stage."""
    if row.get("event") != "stage_end":
        return []
    job = row.get("job")
    stage = row.get("stage")
    if not job:
        return []

    if stage == "ARCHITECT" and row.get("next") == "CODER":
        text = _snippet(artifacts.read_text(job, "plan.md"))
        if not text:
            return []
        lines = ["   log ARCHITECT -> CODER (plan.md):"]
        lines += [f"      | {line}" for line in text.splitlines()]
        return lines
    return []


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
    if ev == "worker_started":
        return f"== {job}: worker started =="
    if ev == "worker_finished":
        return f"== {job}: worker finished -> {row.get('status')} =="
    if ev == "worker_failed":
        return f"!! {job}: worker failed: {row.get('error')}"
    if ev == "worker_publish_start":
        return f"   .. publish running"
    if ev == "worker_published":
        bits = []
        if row.get("branch"):
            bits.append(f"branch={row['branch']}")
        if "committed" in row:
            bits.append(f"committed={row['committed']}")
        if "pushed" in row:
            bits.append(f"pushed={row['pushed']}")
        tail = ("  [" + " ".join(bits) + "]") if bits else ""
        return f"   ok publish{tail}"
    if ev == "worker_publish_failed":
        return f"   !! publish failed: {row.get('error')}"
    return None  # 'transition' is redundant with stage_end's arrow


def console_printer(stream=None):
    stream = stream or sys.stderr

    def printer(row):
        line = format_event(row)
        if line:
            print(line, file=stream, flush=True)
        for extra in artifact_log_lines(row):
            print(extra, file=stream, flush=True)

    return printer
