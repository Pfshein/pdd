"""Stage-level behaviour: free-form architect + reviewer salvage fallback (#5)."""
import json

from orchestrator import artifacts, config, runner, stages


def _events(assistant_text=None, is_error=False, error_msg=None):
    evs = []
    if assistant_text is not None:
        evs.append({"type": "assistant",
                    "message": {"content": [{"type": "text", "text": assistant_text}]}})
    result = {"type": "result", "is_error": is_error}
    if error_msg:
        result["error"] = {"message": error_msg}
    evs.append(result)
    return {"exit_code": 1 if is_error else 0, "stdout": json.dumps(evs),
            "stderr": "", "timed_out": False}


def test_architect_writes_freeform_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(stages.worktree, "worktree_path", lambda job: tmp_path)
    artifacts.write_text("J-ARCH", "task.md", "do the thing")
    monkeypatch.setattr(
        runner, "run_qwen_stage",
        lambda *a, **k: _events("Plan:\n- edit a.py\n- add tests for a"),
    )

    res = stages._architect("J-ARCH", {})

    assert res["status"] == "ok"
    plan = artifacts.read_text("J-ARCH", "plan.md")
    assert "edit a.py" in plan  # captured from assistant text, not a schema tool


def test_architect_process_failure_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(stages.worktree, "worktree_path", lambda job: tmp_path)
    artifacts.write_text("J-ARCH2", "task.md", "x")
    monkeypatch.setattr(
        runner, "run_qwen_stage",
        lambda *a, **k: {"exit_code": None, "stdout": "", "stderr": "boom", "timed_out": True},
    )

    res = stages._architect("J-ARCH2", {})
    assert res["status"] == "error"


def test_review_salvages_plain_text_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(stages.worktree, "worktree_path", lambda job: tmp_path)
    monkeypatch.setattr(stages.worktree, "diff", lambda job, base: "some diff")
    artifacts.write_text("J-REV", "task.md", "t")
    # qwen "fails" the schema but the model wrote a valid verdict as plain text.
    monkeypatch.setattr(
        runner, "run_qwen_stage",
        lambda *a, **k: _events('Verdict: {"issues": [{"class": "nit", "summary": "style"}]}',
                                 is_error=True, error_msg="plain text instead of structured_output"),
    )

    res = stages._review("J-REV", {"base_sha": "BASE"}, "CODE_REVIEW")

    assert res["status"] == "ok"
    assert res["verdict"]["issues"][0]["class"] == "nit"
