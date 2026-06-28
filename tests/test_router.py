"""Unit tests for the deterministic router (decide_next)."""
from copy import deepcopy

from orchestrator import graph as g
from orchestrator.router import decide_next
from orchestrator.state import new_state


def base_state(budgets=None):
    s = new_state("TEST-1", budgets=budgets or {"ARCHITECT": 2, "CODER": 4, "TESTER": 3})
    return s


def review(issues, signature="sig"):
    return {"status": "ok", "verdict": {"issues": issues}, "signature": signature}


# --- Forward / happy path -------------------------------------------------
def test_intake_ok_goes_to_triage():
    nxt, _, _ = decide_next(g.INTAKE, {"status": "ok"}, base_state())
    assert nxt == g.TRIAGE


def test_intake_fail_goes_to_human():
    nxt, _, _ = decide_next(g.INTAKE, {"status": "error"}, base_state())
    assert nxt == g.NEEDS_HUMAN


def test_stage_error_goes_to_human():
    nxt, reason, _ = decide_next(g.CODE_REVIEW, {"status": "error", "error": "bad json"}, base_state())
    assert nxt == g.NEEDS_HUMAN
    assert "failed" in reason


def test_triage_simple_skips_architect():
    nxt, _, s = decide_next(g.TRIAGE, {"triage": "simple"}, base_state())
    assert nxt == g.CODER
    assert s["budgets"]["CODER"]["used"] == 1  # entry charges budget


def test_triage_complex_goes_to_architect():
    nxt, _, s = decide_next(g.TRIAGE, {"triage": "complex"}, base_state())
    assert nxt == g.ARCHITECT
    assert s["budgets"]["ARCHITECT"]["used"] == 1


def test_architect_sets_has_plan_and_goes_to_coder():
    nxt, _, s = decide_next(g.ARCHITECT, {"status": "ok"}, base_state())
    assert nxt == g.CODER
    assert s["has_plan"] is True


def test_coder_goes_to_code_review():
    nxt, _, _ = decide_next(g.CODER, {"status": "ok"}, base_state())
    assert nxt == g.CODE_REVIEW


def test_tester_goes_to_test_run():
    nxt, _, _ = decide_next(g.TESTER, {"status": "ok"}, base_state())
    assert nxt == g.TEST_RUN


# --- Deterministic test signal bypasses the reviewer ----------------------
def test_test_run_green_goes_to_final_review():
    nxt, _, _ = decide_next(g.TEST_RUN, {"test": {"status": "green"}}, base_state())
    assert nxt == g.FINAL_REVIEW


def test_test_run_red_goes_straight_to_coder():
    nxt, reason, _ = decide_next(g.TEST_RUN, {"test": {"status": "red"}, "signature": "t1"}, base_state())
    assert nxt == g.CODER
    assert "bypass reviewer" in reason


# --- Review classification → stage ---------------------------------------
def test_review_pass_no_blocking_goes_to_tester():
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([]), base_state())
    assert nxt == g.TESTER


def test_review_nit_only_is_non_blocking():
    nxt, _, s = decide_next(g.CODE_REVIEW, review([{"class": "nit", "summary": "style"}]), base_state())
    assert nxt == g.TESTER
    assert s["nits"] == ["style"]


def test_review_logic_bug_routes_to_coder():
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}]), base_state())
    assert nxt == g.CODER


def test_review_weak_tests_routes_to_tester():
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([{"class": "weak_tests", "summary": "x"}]), base_state())
    assert nxt == g.TESTER


def test_review_wrong_design_routes_to_architect():
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([{"class": "wrong_design", "summary": "x"}]), base_state())
    assert nxt == g.ARCHITECT


def test_review_priority_wrong_design_over_logic_bug():
    issues = [{"class": "logic_bug", "summary": "a"}, {"class": "wrong_design", "summary": "b"}]
    nxt, _, _ = decide_next(g.CODE_REVIEW, review(issues), base_state())
    assert nxt == g.ARCHITECT


def test_final_review_pass_is_done():
    nxt, _, _ = decide_next(g.FINAL_REVIEW, review([]), base_state())
    assert nxt == g.DONE


# --- Budgets / escalation -------------------------------------------------
def test_coder_budget_exhausted_escalates_to_architect():
    s = base_state({"ARCHITECT": 2, "CODER": 1, "TESTER": 3})
    s["budgets"]["CODER"]["used"] = 1  # already at max
    nxt, reason, ns = decide_next(g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}]), s)
    assert nxt == g.ARCHITECT
    assert "architect replan" in reason
    assert ns["budgets"]["ARCHITECT"]["used"] == 1


def test_coder_and_architect_exhausted_goes_to_human():
    s = base_state({"ARCHITECT": 1, "CODER": 1, "TESTER": 3})
    s["budgets"]["CODER"]["used"] = 1
    s["budgets"]["ARCHITECT"]["used"] = 1
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}]), s)
    assert nxt == g.NEEDS_HUMAN


def test_architect_budget_exhausted_goes_to_human():
    s = base_state({"ARCHITECT": 1, "CODER": 4, "TESTER": 3})
    s["budgets"]["ARCHITECT"]["used"] = 1
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([{"class": "wrong_design", "summary": "x"}]), s)
    assert nxt == g.NEEDS_HUMAN


def test_tester_budget_exhausted_goes_to_human():
    s = base_state({"ARCHITECT": 2, "CODER": 4, "TESTER": 1})
    s["budgets"]["TESTER"]["used"] = 1
    nxt, _, _ = decide_next(g.CODE_REVIEW, review([{"class": "weak_tests", "summary": "x"}]), s)
    assert nxt == g.NEEDS_HUMAN


# --- No-progress detector -------------------------------------------------
def test_no_progress_same_signature_escalates():
    s = base_state()
    s["signatures"]["CODER"] = ["dup"]
    nxt, reason, _ = decide_next(
        g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}], signature="dup"), s
    )
    assert nxt == g.NEEDS_HUMAN
    assert "no progress" in reason


def test_different_signature_does_not_trigger_no_progress():
    s = base_state()
    s["signatures"]["CODER"] = ["old"]
    nxt, _, _ = decide_next(
        g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}], signature="new"), s
    )
    assert nxt == g.CODER


# --- Global step cap ------------------------------------------------------
def test_global_step_cap_escalates():
    s = base_state()
    s["global_step_cap"] = 5
    s["global_steps"] = 5  # next decide_next increments to 6 >= cap
    nxt, reason, _ = decide_next(g.CODER, {"status": "ok"}, s)
    assert nxt == g.NEEDS_HUMAN
    assert "global_step_cap" in reason


def test_input_state_not_mutated():
    s = base_state()
    snapshot = deepcopy(s)
    decide_next(g.TRIAGE, {"triage": "simple"}, s)
    assert s == snapshot  # pure function


# --- Machine-readable terminal reasons (PDD-29) ---------------------------
def test_done_sets_terminal_reason_done():
    _, _, s = decide_next(g.FINAL_REVIEW, review([]), base_state())
    assert s["node"] == g.DONE
    assert s["terminal_reason"] == g.REASON_DONE


def test_stage_error_sets_terminal_reason_stage_error():
    _, _, s = decide_next(g.CODE_REVIEW, {"status": "error"}, base_state())
    assert s["terminal_reason"] == g.REASON_STAGE_ERROR


def test_global_step_cap_sets_terminal_reason():
    s = base_state()
    s["global_step_cap"] = 5
    s["global_steps"] = 5
    _, _, ns = decide_next(g.CODER, {"status": "ok"}, s)
    assert ns["terminal_reason"] == g.REASON_GLOBAL_STEP_CAP


def test_no_progress_and_budget_are_distinguishable():
    # no-progress: repeated signature
    s = base_state()
    s["signatures"]["CODER"] = ["dup"]
    _, _, ns = decide_next(
        g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}], signature="dup"), s
    )
    assert ns["terminal_reason"] == g.REASON_NO_PROGRESS

    # budget exhausted: coder + architect both at max
    s2 = base_state({"ARCHITECT": 1, "CODER": 1, "TESTER": 3})
    s2["budgets"]["CODER"]["used"] = 1
    s2["budgets"]["ARCHITECT"]["used"] = 1
    _, _, ns2 = decide_next(g.CODE_REVIEW, review([{"class": "logic_bug", "summary": "x"}]), s2)
    assert ns2["terminal_reason"] == g.REASON_BUDGET_EXHAUSTED
    assert ns["terminal_reason"] != ns2["terminal_reason"]  # acceptance: distinguishable


def test_non_terminal_hop_leaves_terminal_reason_none():
    _, _, s = decide_next(g.CODER, {"status": "ok"}, base_state())
    assert s["node"] == g.CODE_REVIEW
    assert s["terminal_reason"] is None
