"""Main loop: while not terminal: run node -> route -> persist.

run_node is injected so the same loop drives stubs (tests) and real qwen.
    run_node(node: str, state: dict) -> result: dict
"""
import time

from . import events, state as state_mod
from .graph import is_terminal
from .router import decide_next


def _event_summary(result: dict) -> dict:
    """Small, stable summary for events.jsonl; full payloads stay in artifacts."""
    out = {}
    for key in ("status", "limit", "signature", "sandbox"):
        if result.get(key) is not None:
            out[key] = result.get(key)
    if isinstance(result.get("test"), dict):
        out["test_status"] = result["test"].get("status")
        out["test_exit_code"] = result["test"].get("exit_code")
    if isinstance(result.get("verdict"), dict):
        out["issues"] = len(result["verdict"].get("issues", []))
    return out


def run_job(job_state: dict, run_node, persist: bool = True) -> dict:
    """Drive a job to a terminal node. Returns the final state."""
    job = job_state["job"]
    node = job_state["node"]
    if persist:
        state_mod.save_state(job_state)
        events.record(job, "job_start", node=node, global_step=job_state.get("global_steps"))

    # Hard safety net independent of the in-state cap (guards misconfiguration).
    safety = job_state["global_step_cap"] + 5

    while not is_terminal(node):
        safety -= 1
        if safety <= 0:
            raise RuntimeError(f"driver exceeded hard safety bound on job {job}")

        if persist:
            events.record(job, "stage_start", stage=node, global_step=job_state.get("global_steps"))
        started = time.perf_counter()
        result = run_node(node, job_state)
        duration_ms = int((time.perf_counter() - started) * 1000)
        nxt, reason, job_state = decide_next(node, result, job_state)

        if persist:
            events.record(
                job, "stage_end", stage=node, duration_ms=duration_ms,
                next=nxt, reason=reason, **_event_summary(result),
            )
            events.record(job, "transition", frm=node, to=nxt, reason=reason)
            state_mod.record_transition(job, node, nxt, reason)
            state_mod.record_attempt(
                job, node, reason, result.get("signature"),
                status=result.get("status"), limit=result.get("limit"),
            )
            state_mod.save_state(job_state)

        node = nxt

    if persist:
        events.record(job, "job_end", node=job_state.get("node"), global_steps=job_state.get("global_steps"))
    return job_state
