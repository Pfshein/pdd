"""Main loop: while not terminal: run node -> route -> persist.

run_node is injected so the same loop drives stubs (tests) and real qwen.
    run_node(node: str, state: dict) -> result: dict
"""
from . import state as state_mod
from .graph import is_terminal
from .router import decide_next


def run_job(job_state: dict, run_node, persist: bool = True) -> dict:
    """Drive a job to a terminal node. Returns the final state."""
    job = job_state["job"]
    node = job_state["node"]
    if persist:
        state_mod.save_state(job_state)

    # Hard safety net independent of the in-state cap (guards misconfiguration).
    safety = job_state["global_step_cap"] + 5

    while not is_terminal(node):
        safety -= 1
        if safety <= 0:
            raise RuntimeError(f"driver exceeded hard safety bound on job {job}")

        result = run_node(node, job_state)
        nxt, reason, job_state = decide_next(node, result, job_state)

        if persist:
            state_mod.record_transition(job, node, nxt, reason)
            state_mod.record_attempt(job, node, reason, result.get("signature"))
            state_mod.save_state(job_state)

        node = nxt

    return job_state
