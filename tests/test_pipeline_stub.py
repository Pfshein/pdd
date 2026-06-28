"""Deterministic integration test of the real pipeline with a stubbed model.

Exercises stages.py + run_pipeline + worktree + testrun end-to-end (no network):
a fake run_qwen_stage edits the worktree like a coder and returns a passing
verdict like a reviewer. Proves the wiring reaches DONE on a real git repo.
"""
import json
import subprocess
from pathlib import Path

import pytest

from orchestrator import config, runner, state as state_mod, testrun
from orchestrator import run as run_mod


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_repo(tmp_path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


def _events(result_event, assistant_text=None):
    evs = []
    if assistant_text:
        evs.append({"type": "assistant", "message": {"content": [{"type": "text", "text": assistant_text}]}})
    evs.append(result_event)
    return {"exit_code": 0, "stdout": json.dumps(evs), "stderr": "", "timed_out": False, "argv": []}


def _fake_run_qwen_stage(prompt, *, cwd, json_schema=None, **kwargs):
    # Reviewer: return a passing verdict.
    if json_schema and "verdict.json" in str(json_schema):
        return _events({"type": "result", "is_error": False, "structured_result": {"issues": []}})
    # Architect: return a trivial plan (not reached for a 'bug', but be safe).
    if json_schema and "plan.json" in str(json_schema):
        return _events({"type": "result", "is_error": False, "structured_result": {"plan": "fix add()"}})
    # Editor stage (coder/tester): fix the bug in the worktree.
    calc = Path(cwd) / "calc.py"
    if calc.exists():
        calc.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return _events({"type": "result", "is_error": False}, assistant_text="applied fix")


def _fake_run_tests(job, worktree, command=None, setup_command=None):
    result = {
        "status": "green",
        "command": command or "stub",
        "setup_command": setup_command,
        "exit_code": 0,
        "log_tail": "",
    }
    (state_mod.job_dir(job) / "test_result.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    return result


def test_pipeline_reaches_done(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "wt")
    monkeypatch.setattr(runner, "run_qwen_stage", _fake_run_qwen_stage)
    monkeypatch.setattr(testrun, "run_tests", _fake_run_tests)

    repo = _make_repo(tmp_path)
    final = run_mod.run_pipeline(
        "JOB-X", repo,
        task_md="Fix add() to return a + b.",
        task_meta={"issue_type": "bug", "labels": [], "description_chars": 30, "estimate": 1},
        test_command="python -m pytest -q",
        setup_command="pip install -r requirements.txt",
        keep_worktree=False,
    )

    assert final["node"] == "DONE"
    # artifacts were written
    jd = state_mod.job_dir("JOB-X")
    job_meta = json.loads((jd / "job_meta.json").read_text(encoding="utf-8"))
    assert job_meta["branch"] == "pdd/JOB-X"
    assert job_meta["base_sha"]
    assert job_meta["setup_command"] == "pip install -r requirements.txt"
    assert job_meta["loop_profile"] == "standard"  # default profile recorded
    assert (jd / "diff.patch").read_text(encoding="utf-8").find("a + b") != -1
    assert json.loads((jd / "test_result.json").read_text(encoding="utf-8"))["status"] == "green"
