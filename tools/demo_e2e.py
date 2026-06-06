"""Full end-to-end demo on a throwaway target repo (uses the real model).

Creates a tiny git repo with a bug, then drives the whole PDD pipeline:
INTAKE -> TRIAGE(simple) -> CODER -> CODE_REVIEW -> TESTER -> TEST_RUN -> FINAL_REVIEW.

Run:  PYTHONPATH=. python tools/demo_e2e.py
"""
import subprocess
import tempfile
from pathlib import Path

from orchestrator import artifacts, run as run_mod, state as state_mod, worktree

CALC_BUGGY = "def add(a, b):\n    return a - b\n"
TEST_CALC = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"

TASK_MD = """\
# Fix add() in calc.py

The `add` function in `calc.py` is wrong: it returns `a - b` but it must return
the sum `a + b`. Fix the implementation so that `add(2, 3) == 5`.
"""

TASK_META = {
    "issue_type": "bug",
    "labels": ["math"],
    "description_chars": len(TASK_MD),
    "estimate": 1,
    "summary": "Fix add() in calc.py",
}


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def make_target_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="pdd_target_"))
    (repo / "calc.py").write_text(CALC_BUGGY, encoding="utf-8")
    (repo / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "demo@pdd"], repo)
    _git(["config", "user.name", "pdd-demo"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "initial (buggy)"], repo)
    return repo


def main():
    job = "PDD-1"
    repo = make_target_repo()
    print(f"target repo: {repo}")

    final = run_mod.run_pipeline(
        job, repo,
        task_md=TASK_MD, task_meta=TASK_META,
        test_command="python -m pytest -q",
    )

    print("\n========== RESULT ==========")
    print("terminal node:", final["node"])
    print("budgets:", final["budgets"])
    print("global_steps:", final["global_steps"])

    print("\n--- final diff.patch ---")
    print(artifacts.read_text(job, "diff.patch") or "(none)")
    print("\n--- final verdict.json ---")
    print(artifacts.read_text(job, "verdict.json") or "(none)")
    print("\n--- test_result.json (status) ---")
    tr = artifacts.read_json(job, "test_result.json", {}) or {}
    print("status:", tr.get("status"), "exit:", tr.get("exit_code"))

    print("\n--- transitions ---")
    trans = (state_mod.job_dir(job) / "transitions.jsonl")
    if trans.exists():
        for line in trans.read_text(encoding="utf-8").splitlines():
            import json
            r = json.loads(line)
            print(f"  {r['from']:>13} -> {r['to']:<13} {r['reason']}")

    print(f"\nworktree kept at: {worktree.worktree_path(job)}")


if __name__ == "__main__":
    main()
