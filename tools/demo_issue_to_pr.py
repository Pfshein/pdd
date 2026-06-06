"""Offline product smoke: Jira-like issue JSON -> run -> report -> publish.

This uses a stubbed model/test runner so it is safe for local demos and CI:

  PYTHONPATH=. python tools/demo_issue_to_pr.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import config, jira, publish, report, runner, state as state_mod, testrun
from orchestrator import run as run_mod


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "demo@pdd"], repo)
    _git(["config", "user.name", "pdd-demo"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "initial buggy repo"], repo)
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


def main():
    with tempfile.TemporaryDirectory(prefix="pdd_issue_to_pr_") as td:
        root = Path(td)
        config.RUNS_DIR = root / "runs"
        config.WORKTREES_DIR = root / "worktrees"
        runner.run_qwen_stage = _fake_run_qwen_stage
        testrun.run_tests = _fake_run_tests

        repo = _make_repo(root)
        issue = {
            "key": "DEMO-18",
            "fields": {
                "summary": "Fix add()",
                "issuetype": {"name": "Bug"},
                "labels": ["math"],
                "description": "calc.add returns subtraction instead of addition.",
                "customfield_10016": 1,
            },
        }
        intake = jira.write_intake(issue, root / "intake")
        final = run_mod.run_pipeline(
            "DEMO-18",
            repo,
            task_md=(root / "intake" / "task.md").read_text(encoding="utf-8"),
            task_meta=json.loads((root / "intake" / "task_meta.json").read_text(encoding="utf-8")),
            test_command="python -m pytest -q",
        )
        rep = report.build_report("DEMO-18")
        pub = publish.publish("DEMO-18", push=False)
        print("repo:", repo)
        print("intake:", intake)
        print("final:", final["node"])
        print("published:", pub)
        print(rep)


if __name__ == "__main__":
    main()
