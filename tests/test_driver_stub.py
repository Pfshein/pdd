"""End-to-end graph runs on a scripted stub (no model, no processes)."""
import pytest

from orchestrator import graph as g
from orchestrator.driver import run_job
from orchestrator.state import new_state


def scripted_run_node(script):
    """Return a run_node that pops the next scripted result for each node."""
    queues = {node: list(results) for node, results in script.items()}

    def run_node(node, state):
        q = queues.get(node)
        if not q:
            raise AssertionError(f"no scripted result left for node {node}")
        return q.pop(0)

    return run_node


def review(issues, signature="sig"):
    return {"status": "ok", "verdict": {"issues": issues}, "signature": signature}


def test_happy_path_reaches_done():
    script = {
        g.INTAKE: [{"status": "ok"}],
        g.TRIAGE: [{"triage": "simple"}],
        g.CODER: [{"status": "ok", "signature": "c1"}],
        g.CODE_REVIEW: [review([])],
        g.TESTER: [{"status": "ok"}],
        g.TEST_RUN: [{"test": {"status": "green"}}],
        g.FINAL_REVIEW: [review([])],
    }
    final = run_job(new_state("DEMO-1"), scripted_run_node(script), persist=False)
    assert final["node"] == g.DONE
    assert final["budgets"]["CODER"]["used"] == 1
    assert final["budgets"]["TESTER"]["used"] == 1


def test_complex_path_runs_architect_then_red_test_loop():
    # complex triage -> architect -> coder -> review pass -> tester -> red test
    # -> coder -> review pass -> tester -> green -> final pass -> DONE
    script = {
        g.INTAKE: [{"status": "ok"}],
        g.TRIAGE: [{"triage": "complex"}],
        g.ARCHITECT: [{"status": "ok"}],
        g.CODER: [{"status": "ok", "signature": "c1"}, {"status": "ok", "signature": "c2"}],
        g.CODE_REVIEW: [review([]), review([])],
        g.TESTER: [{"status": "ok"}, {"status": "ok"}],
        g.TEST_RUN: [{"test": {"status": "red"}, "signature": "t1"}, {"test": {"status": "green"}}],
        g.FINAL_REVIEW: [review([])],
    }
    final = run_job(new_state("DEMO-2"), scripted_run_node(script), persist=False)
    assert final["node"] == g.DONE
    assert final["has_plan"] is True
    assert final["budgets"]["CODER"]["used"] == 2  # initial + red-test retry


def test_no_progress_escalates_to_human():
    script = {
        g.INTAKE: [{"status": "ok"}],
        g.TRIAGE: [{"triage": "simple"}],
        g.CODER: [{"status": "ok"}, {"status": "ok"}],
        g.CODE_REVIEW: [
            review([{"class": "logic_bug", "summary": "same"}], signature="dup"),
            review([{"class": "logic_bug", "summary": "same"}], signature="dup"),
        ],
    }
    final = run_job(new_state("DEMO-3"), scripted_run_node(script), persist=False)
    assert final["node"] == g.NEEDS_HUMAN


def test_budget_ladder_escalates_to_human():
    script = {
        g.INTAKE: [{"status": "ok"}],
        g.TRIAGE: [{"triage": "simple"}],
        g.CODER: [{"status": "ok"}],
        g.CODE_REVIEW: [review([{"class": "logic_bug", "summary": "x"}], signature="s1")],
        g.ARCHITECT: [{"status": "ok"}],
    }
    state = new_state("DEMO-4", budgets={"ARCHITECT": 1, "CODER": 1, "TESTER": 3})
    final = run_job(state, scripted_run_node(script), persist=False)
    assert final["node"] == g.NEEDS_HUMAN


def test_intake_failure_escalates():
    script = {g.INTAKE: [{"status": "error"}]}
    final = run_job(new_state("DEMO-5"), scripted_run_node(script), persist=False)
    assert final["node"] == g.NEEDS_HUMAN


def test_persistence_writes_artifacts(tmp_path, monkeypatch):
    from orchestrator import config, events, state as state_mod
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    script = {
        g.INTAKE: [{"status": "ok"}],
        g.TRIAGE: [{"triage": "simple"}],
        g.CODER: [{"status": "ok"}],
        g.CODE_REVIEW: [review([])],
        g.TESTER: [{"status": "ok"}],
        g.TEST_RUN: [{"test": {"status": "green"}}],
        g.FINAL_REVIEW: [review([])],
    }
    run_job(new_state("PERSIST-1"), scripted_run_node(script), persist=True)
    jd = tmp_path / "runs" / "PERSIST-1"
    assert (jd / "state.json").exists()
    assert (jd / "transitions.jsonl").exists()
    assert (jd / "attempts.jsonl").exists()
    rows = events.read("PERSIST-1")
    assert rows[0]["event"] == "job_start"
    assert any(r["event"] == "stage_end" and r.get("stage") == g.TEST_RUN for r in rows)
    assert rows[-1]["event"] == "job_end"
