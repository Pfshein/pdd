"""Main loop: while not terminal: run node -> route -> persist.

run_node is injected so the same loop drives stubs (tests) and real qwen.
    run_node(node: str, state: dict) -> result: dict
"""
import time

from . import config, events, state as state_mod, usage
from .graph import is_terminal, NEEDS_HUMAN, RETURN_TARGETS, REASON_COST_BUDGET
from .router import decide_next


def _cost_over_budget(job: str):
    """Return (spent, cap) if the job exceeded its cost cap, else None.

    Disabled by default (cap is None). Needs configured rates to have a cost.
    """
    cap = config.MAX_JOB_COST_USD
    if cap is None:
        return None
    spent = usage.cost_summary(job).get("cost_usd")
    if spent is not None and spent > cap:
        return spent, cap
    return None


def _event_summary(result: dict) -> dict:
    """Small, stable summary for events.jsonl; full payloads stay in artifacts."""
    out = {}
    for key in ("status", "limit", "signature", "sandbox"):
        if result.get(key) is not None:
            out[key] = result.get(key)
    if result.get("error"):
        out["error"] = str(result["error"])[-240:]
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

        # Cost guardrail: after the stage's usage is recorded, stop before
        # spending more if the job is over its (optional) cost cap.
        if not is_terminal(nxt):
            over = _cost_over_budget(job)
            if over is not None:
                spent, cap = over
                nxt = NEEDS_HUMAN
                reason = f"cost budget exhausted (${spent:.4f} > ${cap})"
                job_state["node"] = NEEDS_HUMAN
                job_state["terminal_reason"] = REASON_COST_BUDGET

        if persist:
            summary = _event_summary(result)
            if nxt in RETURN_TARGETS:  # show the budget the loop is spending on this target
                b = job_state["budgets"].get(nxt)
                if b:
                    summary["budget"] = f"{b['used']}/{b['max']}"
            events.record(
                job, "stage_end", stage=node, duration_ms=duration_ms,
                next=nxt, reason=reason, **summary,
            )
            events.record(job, "transition", frm=node, to=nxt, reason=reason)
            state_mod.record_transition(job, node, nxt, reason)
            state_mod.record_attempt(
                job, node, reason, result.get("signature"),
                status=result.get("status"), limit=result.get("limit"),
                error=result.get("error"),
            )
            state_mod.save_state(job_state)

        node = nxt

    if persist:
        events.record(job, "job_end", node=job_state.get("node"), global_steps=job_state.get("global_steps"))
    return job_state
