"""Run one job end-to-end: set up the worktree, drive the graph, finalize.

CLI:
  python -m orchestrator.run --job DEMO-1 --repo <path> \
      --task task.md --meta meta.json [--setup-command "pip install -r requirements.txt"]
"""
import argparse
import json
import sys

from . import artifacts, config, driver, events, graph, sandbox, stages, state as state_mod, worktree
from .graph import NEEDS_HUMAN, DONE

PER_RUN_ARTIFACTS = (
    "transitions.jsonl",
    "attempts.jsonl",
    "plan.md",
    "changes.md",
    "diff.patch",
    "verdict.json",
    "test_result.json",
    "setup_result.json",
    "escalation.md",
    "report.md",
    "publish.json",
    "SECURITY.txt",
    "reaped.json",
    "sandbox_audit.jsonl",
    "events.jsonl",
)


def _write_escalation(job: str, final: dict) -> None:
    last_verdict = artifacts.read_json(job, "verdict.json", {}) or {}
    lines = [
        f"# Escalation: {job} -> needs-human",
        "",
        f"Stopped at: {final['node']}",
        f"Global steps: {final['global_steps']}/{final['global_step_cap']}",
        "",
        "## Budgets",
        json.dumps(final["budgets"], indent=2, ensure_ascii=False),
        "",
        "## Last verdict",
        json.dumps(last_verdict, indent=2, ensure_ascii=False),
        "",
        "## What we tried",
        artifacts.compressed_attempts(job, limit=50) or "(none)",
    ]
    artifacts.write_text(job, "escalation.md", "\n".join(lines))


def _reset_job_logs(job: str) -> None:
    """Start a run fresh when reusing the same job id."""
    jd = state_mod.job_dir(job)
    for name in PER_RUN_ARTIFACTS:
        p = jd / name
        if p.exists():
            p.unlink()


def run_pipeline(job, repo, *, task_md, task_meta, test_command=None, setup_command=None,
                 base_ref="HEAD", keep_worktree=True) -> dict:
    _reset_job_logs(job)
    wt, branch, base_sha = worktree.create(repo, job, base_ref)
    events.record(
        job, "run_created", repo=str(repo), base_ref=base_ref,
        base_sha=base_sha, branch=branch, worktree=str(wt),
    )
    artifacts.write_json(job, "job_meta.json", {
        "job": job,
        "repo": str(repo),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "branch": branch,
        "worktree": str(wt),
        "test_command": test_command or config.TEST_COMMAND,
        "setup_command": setup_command if setup_command is not None else config.SETUP_COMMAND,
    })
    ctx = {
        "repo": str(repo),
        "base_sha": base_sha,
        "task_md": task_md,
        "task_meta": task_meta,
        "test_command": test_command or config.TEST_COMMAND,
        "setup_command": setup_command if setup_command is not None else config.SETUP_COMMAND,
    }
    st = state_mod.new_state(job)
    final = driver.run_job(st, stages.make_run_node(ctx), persist=True)
    events.record(job, "run_finished", node=final["node"], global_steps=final["global_steps"])

    if final["node"] == NEEDS_HUMAN:
        _write_escalation(job, final)
    if not keep_worktree:
        worktree.remove(repo, job)
    return final


class ResumeError(RuntimeError):
    """A job cannot be resumed/retried from its persisted state."""


def _ctx_from_artifacts(job: str, meta: dict) -> dict:
    """Rebuild the run context from on-disk artifacts (for resume/retry)."""
    return {
        "repo": meta["repo"],
        "base_sha": meta["base_sha"],
        "task_md": artifacts.read_text(job, "task.md"),
        "task_meta": artifacts.read_json(job, "task_meta.json", {}) or {},
        "test_command": meta.get("test_command"),
        "setup_command": meta.get("setup_command"),
    }


def _drive(job: str, st: dict) -> dict:
    meta = artifacts.read_json(job, "job_meta.json")
    if not meta:
        raise ResumeError("no job_meta.json — run the job first")
    if not worktree.worktree_path(job).exists():
        raise ResumeError("job worktree is gone — re-run instead of resume/retry")
    final = driver.run_job(st, stages.make_run_node(_ctx_from_artifacts(job, meta)), persist=True)
    if final["node"] == NEEDS_HUMAN:
        _write_escalation(job, final)
    return final


def resume_pipeline(job: str) -> dict:
    """Continue a job from its persisted state.json (after a crash/interrupt)."""
    job = state_mod.validate_job_id(job)
    st = state_mod.load_state(job)
    if st["node"] in (DONE, NEEDS_HUMAN):
        return st  # already terminal — nothing to resume
    return _drive(job, st)


def retry_pipeline(job: str, stage: str) -> dict:
    """Rewind a job to a specific stage and drive forward from there."""
    job = state_mod.validate_job_id(job)
    stage = stage.upper()
    if stage not in graph.ORDER:  # ORDER excludes the terminal nodes
        raise ResumeError(f"unknown stage {stage!r}; choose from {sorted(graph.ORDER)}")
    st = state_mod.load_state(job)
    st["node"] = stage
    state_mod.save_state(st)
    return _drive(job, st)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run a PDD job end-to-end.")
    p.add_argument("--job", required=True, help="correlation id (Jira key)")
    p.add_argument("--repo", required=True, help="target git repo path")
    p.add_argument("--task", required=True, help="path to task.md")
    p.add_argument("--meta", required=True, help="path to task_meta.json")
    p.add_argument("--test-command", default=None)
    p.add_argument("--setup-command", default=None)
    p.add_argument("--base-ref", default="HEAD")
    p.add_argument("--drop-worktree", action="store_true")
    args = p.parse_args(argv)

    task_md = open(args.task, encoding="utf-8").read()
    task_meta = json.load(open(args.meta, encoding="utf-8"))

    final = run_pipeline(
        args.job, args.repo,
        task_md=task_md, task_meta=task_meta,
        test_command=args.test_command, setup_command=args.setup_command, base_ref=args.base_ref,
        keep_worktree=not args.drop_worktree,
    )
    print(f"\n=== {args.job} finished at: {final['node']} ===")
    print(f"worktree: {worktree.worktree_path(args.job)}")
    print(f"artifacts: {state_mod.job_dir(args.job)}")
    return 0 if final["node"] == DONE else 2


if __name__ == "__main__":
    sys.exit(main())
