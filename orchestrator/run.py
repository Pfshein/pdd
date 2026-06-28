"""Run one job end-to-end: set up the worktree, drive the graph, finalize.

CLI:
  python -m orchestrator.run --job DEMO-1 --repo <path> \
      --task task.md --meta meta.json [--setup-command "pip install -r requirements.txt"]
"""
import argparse
import json
import re
import sys
from pathlib import Path

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
    "handoff.md",
    "report.md",
    "stage_error.json",
    "publish.json",
    "SECURITY.txt",
    "reaped.json",
    "sandbox_audit.jsonl",
    "events.jsonl",
    "usage.jsonl",
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


# Suggested next human action keyed by the router's machine-readable stop reason.
_NEXT_ACTION = {
    graph.REASON_COST_BUDGET:
        "Cost cap hit. Review scope, then raise PDD_MAX_JOB_COST_USD and re-run, "
        "or finish the change manually.",
    graph.REASON_GLOBAL_STEP_CAP:
        "The loop hit the global step cap. Review the diff and either raise the cap "
        "(or use --loop-profile aggressive) or finish manually.",
    graph.REASON_NO_PROGRESS:
        "The model repeated the same failure without progress. The task is likely "
        "underspecified — clarify it and re-run, or fix manually.",
    graph.REASON_BUDGET_EXHAUSTED:
        "Stage retries were exhausted. Review the last verdict and diff, then finish "
        "the change manually or re-run with a higher budget.",
    graph.REASON_STAGE_ERROR:
        "A stage errored. Check the error below and the report, fix the cause, re-run.",
}


def _next_action(reason: str) -> str:
    return _NEXT_ACTION.get(reason, "Review the report and diff, then finish manually or re-run.")


def _write_handoff(job: str, final: dict) -> None:
    """Concise NEEDS_HUMAN handoff for an issue comment (handoff.md)."""
    reason = final.get("terminal_reason") or "unknown"
    verdict = artifacts.read_json(job, "verdict.json", {}) or {}
    test = artifacts.read_json(job, "test_result.json", {}) or {}
    lines = [
        f"# Handoff: {job}",
        "",
        f"- Stopped at: {final['node']}",
        f"- Stop reason: {reason}",
        f"- Steps: {final['global_steps']}/{final['global_step_cap']}",
        "",
        "## Last verdict",
    ]
    issues = verdict.get("issues", [])
    if issues:
        for i in issues:
            loc = f" ({i['location']})" if i.get("location") else ""
            lines.append(f"- {i.get('class')}: {i.get('summary')}{loc}")
    else:
        lines.append("- (no blocking issues)")
    if test.get("status") == "red" and (test.get("log_tail") or "").strip():
        lines += ["", "## Last red test output", "```", test["log_tail"].strip()[-1500:], "```"]
    lines += ["", "## Next action", _next_action(reason)]
    artifacts.write_text(job, "handoff.md", "\n".join(lines) + "\n")


def _reset_job_logs(job: str) -> None:
    """Start a run fresh when reusing the same job id."""
    jd = state_mod.job_dir(job)
    for name in PER_RUN_ARTIFACTS:
        p = jd / name
        if p.exists():
            p.unlink()


_PDD_CARD_RE = re.compile(r"\b(PDD-\d+)\b")


def _extract_markdown_section(text: str, heading_prefix: str) -> str:
    """Return a markdown section whose heading starts with `heading_prefix`."""
    lines = text.splitlines()
    start = None
    level = None
    for idx, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and match.group(2).startswith(heading_prefix):
            start = idx
            level = len(match.group(1))
            break
    if start is None:
        return ""

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[idx])
        if match and len(match.group(1)) <= level:
            end = idx
            break
    return "\n".join(lines[start:end]).strip()


def hydrate_task_context(repo: str, task_md: str) -> str:
    """Inline referenced backlog card specs so tool-less stages can see them."""
    if "docs/LOOP_ENGINEERING_PROJECT.md" not in task_md:
        return task_md
    card_match = _PDD_CARD_RE.search(task_md)
    if not card_match:
        return task_md

    doc_path = Path(repo) / "docs" / "LOOP_ENGINEERING_PROJECT.md"
    if not doc_path.exists():
        return task_md
    section = _extract_markdown_section(doc_path.read_text(encoding="utf-8"), card_match.group(1))
    if not section:
        return task_md
    if section in task_md:
        return task_md
    return (
        task_md.rstrip()
        + "\n\n## Resolved referenced specification\n"
        + "Source: docs/LOOP_ENGINEERING_PROJECT.md\n\n"
        + section
        + "\n"
    )


def run_pipeline(job, repo, *, task_md, task_meta, test_command=None, setup_command=None,
                 base_ref="HEAD", keep_worktree=True, loop_profile=config.DEFAULT_LOOP_PROFILE) -> dict:
    _reset_job_logs(job)
    task_md = hydrate_task_context(str(repo), task_md)
    profile = config.loop_profile(loop_profile)  # validates before touching git
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
        "loop_profile": loop_profile,
    })
    ctx = {
        "repo": str(repo),
        "base_sha": base_sha,
        "task_md": task_md,
        "task_meta": task_meta,
        "test_command": test_command or config.TEST_COMMAND,
        "setup_command": setup_command if setup_command is not None else config.SETUP_COMMAND,
    }
    st = state_mod.new_state(job, budgets=profile["budgets"], global_step_cap=profile["global_step_cap"])
    final = driver.run_job(st, stages.make_run_node(ctx), persist=True)
    events.record(job, "run_finished", node=final["node"], global_steps=final["global_steps"])

    if final["node"] == NEEDS_HUMAN:
        _write_escalation(job, final)
        _write_handoff(job, final)
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
        _write_handoff(job, final)
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
    p.add_argument("--loop-profile", default=config.DEFAULT_LOOP_PROFILE,
                   choices=sorted(config.LOOP_PROFILES))
    p.add_argument("--drop-worktree", action="store_true")
    args = p.parse_args(argv)

    task_md = open(args.task, encoding="utf-8").read()
    task_meta = artifacts.read_user_json(args.meta)

    final = run_pipeline(
        args.job, args.repo,
        task_md=task_md, task_meta=task_meta,
        test_command=args.test_command, setup_command=args.setup_command, base_ref=args.base_ref,
        keep_worktree=not args.drop_worktree, loop_profile=args.loop_profile,
    )
    print(f"\n=== {args.job} finished at: {final['node']} ===")
    print(f"worktree: {worktree.worktree_path(args.job)}")
    print(f"artifacts: {state_mod.job_dir(args.job)}")
    return 0 if final["node"] == DONE else 2


if __name__ == "__main__":
    sys.exit(main())
