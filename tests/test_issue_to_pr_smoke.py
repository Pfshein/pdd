"""Product smoke: issue JSON -> task files -> run -> report -> publish commit."""
import json
import subprocess
from pathlib import Path

from orchestrator import config, jira, publish, report, runner, state as state_mod, testrun, worktree
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
    if json_schema and "verdict.json" in str(json_schema):
        return _events({"type": "result", "is_error": False, "structured_result": {"issues": []}})
    calc = Path(cwd) / "calc.py"
    if calc.exists():
        calc.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    return _events({"type": "result", "is_error": False}, assistant_text="fixed calc.py")


def _fake_run_tests(job, worktree, command=None, setup_command=None):
    result = {"status": "green", "command": command or "pytest", "exit_code": 0, "log_tail": ""}
    (state_mod.job_dir(job) / "test_result.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def test_issue_json_to_publish_commit_smoke(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "wt")
    monkeypatch.setattr(runner, "run_qwen_stage", _fake_run_qwen_stage)
    monkeypatch.setattr(testrun, "run_tests", _fake_run_tests)
    repo = _make_repo(tmp_path)
    issue = {
        "key": "PROD-18",
        "fields": {
            "summary": "Fix add()",
            "issuetype": {"name": "Bug"},
            "labels": ["math"],
            "description": "calc.add returns subtraction instead of addition.",
            "customfield_10016": 1,
        },
    }
    intake_dir = tmp_path / "intake"
    intake = jira.write_intake(issue, intake_dir)

    final = run_mod.run_pipeline(
        "PROD-18",
        repo,
        task_md=(intake_dir / "task.md").read_text(encoding="utf-8"),
        task_meta=json.loads((intake_dir / "task_meta.json").read_text(encoding="utf-8")),
        test_command="python -m pytest -q",
    )
    assert final["node"] == "DONE"

    md = report.build_report("PROD-18")
    assert "PROD-18" in md
    assert "## Events" in md
    assert "## Diff summary" in md

    res = publish.publish("PROD-18", push=False)
    assert res["committed"]
    assert res["branch"] == "pdd/PROD-18"
    log = subprocess.run(
        ["git", "log", "--oneline", "-1", "pdd/PROD-18"],
        cwd=worktree.worktree_path("PROD-18"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "PROD-18: PROD-18: Fix add()" in log
    assert "a + b" in (config.WORKTREES_DIR / "PROD-18" / "calc.py").read_text(encoding="utf-8")
    assert intake["task_meta"]["jira_key"] == "PROD-18"

