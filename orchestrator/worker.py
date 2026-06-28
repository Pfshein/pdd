"""Single worker loop: process queued jobs one at a time.

Acquire one queued job, mark it running, run the pipeline, then release the queue
record with a terminal status that matches the pipeline's final node. An
infrastructure exception marks the job `failed` (never leaves it leased) and
stores a short error summary. Worker outcome is mirrored into the job's
events.jsonl.

Note: run_pipeline resets per-run artifacts (including events.jsonl) at its start,
so worker events are recorded AFTER the run — otherwise they would be wiped.
"""
import time
from pathlib import Path

from . import artifacts, events, queue, run as run_mod
from .graph import DONE, NEEDS_HUMAN

DEFAULT_POLL_INTERVAL = 5.0

# A terminal pipeline node maps to a terminal queue status.
NODE_TO_STATUS = {DONE: queue.DONE, NEEDS_HUMAN: queue.NEEDS_HUMAN}


def _run_record(rec: dict) -> dict:
    """Read the task files referenced by the queue record and run the pipeline."""
    task_md = Path(rec["task"]).read_text(encoding="utf-8")
    task_meta = artifacts.read_user_json(rec["meta"])
    return run_mod.run_pipeline(
        rec["job"],
        rec["repo"],
        task_md=task_md,
        task_meta=task_meta,
        test_command=rec.get("test_command"),
        setup_command=rec.get("setup_command"),
        base_ref=rec.get("base_ref", "HEAD"),
    )


def _publish(job: str, push: bool = False) -> dict:
    """Publish a DONE job. A publish failure is reported, never undoes DONE."""
    from . import publish as publish_mod

    events.record(job, "worker_publish_start", push=push)
    try:
        res = publish_mod.publish(job, push=push)
        events.record(job, "worker_published", branch=res.get("branch"),
                      committed=bool(res.get("committed")), pushed=res.get("pushed"))
        return {"ok": True, "committed": bool(res.get("committed")), "pushed": res.get("pushed")}
    except Exception as exc:
        summary = f"{type(exc).__name__}: {exc}"
        events.record(job, "worker_publish_failed", error=summary)
        return {"ok": False, "error": summary}


def process_one(worker: str | None = None, publish: bool = False,
                push: bool = False) -> dict | None:
    """Acquire and process a single queued job.

    Return a result dict ({job, status, node?}), or None if the queue is idle.
    With publish=True, a DONE job is published; a publish failure is recorded on
    the queue record (and events) but does not change the DONE queue status.
    """
    rec = queue.acquire(worker=worker)
    if rec is None:
        return None
    job = rec["job"]
    queue.mark_running(job)
    events.record(job, "worker_started", worker=worker, publish=publish, push=push)
    try:
        final = _run_record(rec)
    except Exception as exc:  # infra failure: release so the job is never stuck leased
        summary = f"{type(exc).__name__}: {exc}"
        queue.release(job, queue.FAILED)
        artifacts.write_text(job, "worker_error.txt", summary)
        events.record(job, "worker_failed", error=summary)
        return {"job": job, "status": queue.FAILED, "error": summary}

    status = NODE_TO_STATUS.get(final["node"], queue.FAILED)
    queue.release(job, status)
    events.record(job, "worker_finished", node=final["node"], status=status)
    result = {"job": job, "status": status, "node": final["node"]}

    if publish and final["node"] == DONE:
        pub = _publish(job, push=push)
        queue.annotate(job, publish=pub)  # visible in the queue; status stays done
        result["publish"] = pub
    return result


def run_worker(once: bool = False, poll_interval: float = DEFAULT_POLL_INTERVAL,
               worker: str | None = None, publish: bool = False, push: bool = False) -> int:
    """Process queued jobs. `--once` does at most one; otherwise poll until interrupted."""
    def _tick() -> dict | None:
        result = process_one(worker=worker, publish=publish, push=push)
        if result is None:
            print("worker: no queued work")
        else:
            line = f"worker: {result['job']} -> {result['status']}"
            pub = result.get("publish")
            if pub is not None:
                line += " (published)" if pub.get("ok") else " (publish failed)"
            print(line)
        return result

    if once:
        _tick()
        return 0
    try:
        while True:
            if _tick() is None:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        return 0
