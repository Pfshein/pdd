"""resume/retry from persisted state (PDD-08), with stubbed stages."""
import json

import pytest

from orchestrator import artifacts, config, graph as g
from orchestrator import run as run_mod
from orchestrator import state as state_mod


def _scripted(script):
    queues = {k: list(v) for k, v in script.items()}

    def run_node(node, state):
        return queues[node].pop(0)

    return run_node


def _review(issues=()):
    return {"status": "ok", "verdict": {"issues": list(issues)}, "signature": "s"}


FORWARD = {
    g.CODER: [{"status": "ok"}],
    g.CODE_REVIEW: [_review()],
    g.TESTER: [{"status": "ok"}],
    g.TEST_RUN: [{"test": {"status": "green"}}],
    g.FINAL_REVIEW: [_review()],
}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "wt")
    (tmp_path / "wt" / "JOB").mkdir(parents=True)  # worktree must exist for resume

    def seed(node):
        st = state_mod.new_state("JOB")
        st["node"] = node
        state_mod.save_state(st)
        artifacts.write_json("JOB", "job_meta.json", {
            "job": "JOB", "repo": str(tmp_path / "repo"), "base_ref": "HEAD",
            "base_sha": "BASE", "branch": "pdd/JOB",
            "worktree": str(tmp_path / "wt" / "JOB"), "test_command": "pytest",
        })
        artifacts.write_text("JOB", "task.md", "# t")
        artifacts.write_json("JOB", "task_meta.json", {"issue_type": "bug"})

    return seed


def test_resume_continues_to_done(seeded, monkeypatch):
    seeded(g.CODER)
    monkeypatch.setattr(run_mod.stages, "make_run_node", lambda ctx: _scripted(dict(FORWARD)))

    final = run_mod.resume_pipeline("JOB")
    assert final["node"] == g.DONE
    # attempts log carries diagnostics (status)
    attempts = state_mod.read_attempts("JOB")
    assert any(a.get("status") == "ok" for a in attempts)


def test_resume_noop_when_terminal(seeded, monkeypatch):
    seeded(g.DONE)
    called = []
    monkeypatch.setattr(run_mod.stages, "make_run_node", lambda ctx: called.append(1) or (lambda n, s: {}))

    final = run_mod.resume_pipeline("JOB")
    assert final["node"] == g.DONE
    assert called == []  # no stages run for an already-terminal job


def test_retry_rewinds_to_stage(seeded, monkeypatch):
    seeded(g.DONE)  # finished job
    monkeypatch.setattr(run_mod.stages, "make_run_node", lambda ctx: _scripted(dict(FORWARD)))

    final = run_mod.retry_pipeline("JOB", "coder")  # case-insensitive
    assert final["node"] == g.DONE
    assert json.loads((state_mod.job_dir("JOB") / "state.json").read_text())["node"] == g.DONE


def test_retry_rejects_unknown_stage(seeded):
    seeded(g.DONE)
    with pytest.raises(run_mod.ResumeError):
        run_mod.retry_pipeline("JOB", "NOPE")


def test_resume_without_worktree_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "wt")  # not created
    st = state_mod.new_state("JOB-NW")
    st["node"] = g.CODER
    state_mod.save_state(st)
    artifacts.write_json("JOB-NW", "job_meta.json", {
        "job": "JOB-NW", "repo": str(tmp_path), "base_sha": "B",
        "branch": "pdd/JOB-NW", "test_command": "pytest",
    })
    with pytest.raises(run_mod.ResumeError):
        run_mod.resume_pipeline("JOB-NW")
