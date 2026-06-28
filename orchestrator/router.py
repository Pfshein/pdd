"""Deterministic router: (node, stage_result, state) -> (next_node, reason, new_state).

Pure function: returns a NEW state (deep-copied), never mutates the input.
This is the single place that owns routing, budgets, the no-progress detector,
and the escalation ladder. The LLM never names a stage here.

stage_result is a plain dict produced by a node:
  status:    "ok" | "error"                  (INTAKE / generic)
  triage:    "simple" | "complex"            (TRIAGE)
  verdict:   {"issues": [...]}               (CODE_REVIEW / FINAL_REVIEW)
  test:      {"status": "green" | "red"}     (TEST_RUN)
  signature: str | None                      (reason fingerprint for no-progress)
"""
from copy import deepcopy

from . import config
from .graph import (
    INTAKE, TRIAGE, ARCHITECT, CODER, CODE_REVIEW, TESTER, TEST_RUN,
    FINAL_REVIEW, DONE, NEEDS_HUMAN, RETURN_TARGETS, BLOCKING_CLASSES,
    CLASS_TO_STAGE, highest_priority_class,
    REASON_DONE, REASON_STAGE_ERROR, REASON_GLOBAL_STEP_CAP,
    REASON_NO_PROGRESS, REASON_BUDGET_EXHAUSTED, REASON_UNKNOWN,
)


def decide_next(node: str, result: dict, state: dict):
    """Return (next_node, reason, new_state)."""
    s = deepcopy(state)
    s["global_steps"] += 1

    intended, reason, complaint = _intended_next(node, result, s)

    if intended == DONE:
        nxt, rsn = DONE, reason
    elif intended == NEEDS_HUMAN:
        # A natural terminal from a stage (status:error / intake failed).
        nxt, rsn = NEEDS_HUMAN, reason
        s["terminal_reason"] = REASON_STAGE_ERROR
    elif s["global_steps"] >= s["global_step_cap"]:
        # Global ceiling: a natural terminal above was already allowed through.
        nxt, rsn = NEEDS_HUMAN, f"global_step_cap reached ({s['global_step_cap']})"
        s["terminal_reason"] = REASON_GLOBAL_STEP_CAP
    elif intended in RETURN_TARGETS:
        # _enter_return_target / _escalate set terminal_reason on their NEEDS_HUMAN exits.
        nxt, rsn = _enter_return_target(intended, result, s, reason, complaint)
    else:
        nxt, rsn = intended, reason

    s["node"] = nxt
    if nxt == DONE:
        s["terminal_reason"] = REASON_DONE
    elif nxt == NEEDS_HUMAN and not s.get("terminal_reason"):
        s["terminal_reason"] = REASON_UNKNOWN
    return nxt, rsn, s


# --- Transition table (intended next, before budget/escalation) -----------
# Each branch returns (next_node, reason, complaint).
# complaint=True marks a loop-back caused by a problem (blocking review / red
# test). Only complaints feed the no-progress detector; forward progress does
# not, even when it enters a return-target stage.
def _intended_next(node: str, result: dict, s: dict):
    if result.get("status") == "error":
        return NEEDS_HUMAN, f"{node} failed", False

    if node == INTAKE:
        if result.get("status") == "ok":
            return TRIAGE, "intake ok", False
        return NEEDS_HUMAN, "intake failed", False
    if node == TRIAGE:
        if result.get("triage") == "complex":
            return ARCHITECT, "triage: complex", False
        return CODER, "triage: simple", False
    if node == ARCHITECT:
        s["has_plan"] = True
        return CODER, "architect produced plan", False
    if node == CODER:
        return CODE_REVIEW, "coder produced diff", False
    if node == CODE_REVIEW:
        return _route_review(result, s, on_pass=TESTER)
    if node == TESTER:
        return TEST_RUN, "tester updated tests", False
    if node == TEST_RUN:
        status = (result.get("test") or {}).get("status")
        if status == "green":
            return FINAL_REVIEW, "tests green", False
        return CODER, "tests red -> coder (deterministic, bypass reviewer)", True
    if node == FINAL_REVIEW:
        return _route_review(result, s, on_pass=DONE)
    raise ValueError(f"unknown node {node!r}")


def _route_review(result: dict, s: dict, on_pass: str):
    """Classify the verdict; code (not the LLM) maps class -> stage."""
    verdict = result.get("verdict") or {}
    issues = verdict.get("issues", [])
    for issue in issues:
        if issue.get("class") == "nit":
            s["nits"].append(issue.get("summary", ""))
    blocking = [i for i in issues if i.get("class") in BLOCKING_CLASSES]
    if not blocking:
        return on_pass, "review pass (no blocking issues)", False
    cls = highest_priority_class(i["class"] for i in blocking)
    return CLASS_TO_STAGE[cls], f"review blocking: {cls}", True


# --- Budgets / no-progress / escalation -----------------------------------
def _enter_return_target(target: str, result: dict, s: dict, reason: str, complaint: bool):
    """Charge budget on entry to a return target; on complaints detect stalls."""
    hist = s["signatures"].setdefault(target, [])
    sig = result.get("signature")

    # No-progress detector applies only to complaint loop-backs.
    if complaint and sig and sig in hist:
        s["terminal_reason"] = REASON_NO_PROGRESS
        return NEEDS_HUMAN, f"no progress on {target} (repeated signature)"

    budget = s["budgets"].get(target)
    if budget is None:
        return target, reason  # unbudgeted stage: pass through

    if budget["used"] >= budget["max"]:
        return _escalate(target, result, s, complaint)

    budget["used"] += 1
    if complaint and sig:
        hist.append(sig)
        del hist[: -config.SIGNATURE_HISTORY]  # keep only the last N
    return target, reason


def _escalate(target: str, result: dict, s: dict, complaint: bool):
    """Escalation ladder. Coder -> architect replan -> human."""
    if target == CODER:
        return _enter_return_target(
            ARCHITECT, result, s, "coder budget exhausted -> architect replan", complaint
        )
    s["terminal_reason"] = REASON_BUDGET_EXHAUSTED
    if target == ARCHITECT:
        return NEEDS_HUMAN, "architect budget exhausted"
    if target == TESTER:
        return NEEDS_HUMAN, "tester budget exhausted"
    return NEEDS_HUMAN, f"{target} budget exhausted"
