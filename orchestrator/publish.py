"""Publish a finished job: commit the worktree to its branch, optional push/PR.

Turns the diff-on-disk into a real branch (and optionally a pushed branch / a PR
via `gh`) on the TARGET repo. PDD does not impose a git identity — the commit
uses the target repo's own user.name/email.
"""
import shutil
import subprocess

from . import artifacts, state as state_mod, worktree

# Keep build artifacts out of the published commit.
_EXCLUDES = [":(exclude)**/__pycache__/**", ":(exclude)**/*.pyc"]


class PublishError(RuntimeError):
    """Publishing cannot proceed (missing job, worktree, or git failure)."""


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _message(job: str, override: str | None):
    body = artifacts.read_text(job, "changes.md").strip()
    if override:
        return override, body
    task_lines = artifacts.read_text(job, "task.md").strip().splitlines()
    headline = task_lines[0].lstrip("# ").strip() if task_lines else ""
    title = f"{job}: {headline}" if headline else f"{job}: automated change"
    return title[:72], body


def _commit(job: str, wt, title: str, body: str):
    _git(["add", "-A", "--", ".", *_EXCLUDES], wt)
    if _git(["diff", "--cached", "--quiet"], wt).returncode == 0:
        return None  # nothing staged
    message = f"{title}\n\n{body}" if body else title
    r = _git(["commit", "-m", message], wt)
    if r.returncode != 0:
        raise PublishError(f"git commit failed: {r.stderr.strip()}")
    return _git(["rev-parse", "HEAD"], wt).stdout.strip()


def _push(wt, branch: str) -> bool:
    return _git(["push", "-u", "origin", branch], wt).returncode == 0


def _create_pr(wt, branch: str, base: str, title: str, body: str):
    if shutil.which("gh") is None:
        return None
    r = subprocess.run(
        ["gh", "pr", "create", "--base", base, "--head", branch,
         "--title", title, "--body", body or title],
        cwd=str(wt), capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip().splitlines()
    return out[-1] if out else None


def publish(job: str, *, push: bool = False, make_pr: bool = False,
            base: str | None = None, message: str | None = None) -> dict:
    job = state_mod.validate_job_id(job)
    meta = artifacts.read_json(job, "job_meta.json")
    if not meta:
        raise PublishError("no job_meta.json — run the job first")
    branch = meta["branch"]
    base = base or meta.get("base_ref") or "HEAD"
    wt = worktree.worktree_path(job)
    if not wt.exists():
        raise PublishError("job worktree is gone (cleaned up?) — nothing to publish")

    title, body = _message(job, message)
    sha = _commit(job, wt, title, body)
    result = {"branch": branch, "committed": sha, "pushed": False, "pr_url": None}
    if sha is None:
        result["note"] = "nothing to commit (no changes in the worktree)"
    if push and sha:
        result["pushed"] = _push(wt, branch)
    if make_pr and result["pushed"]:
        result["pr_url"] = _create_pr(wt, branch, base, title, body)
    artifacts.write_json(job, "publish.json", result)
    return result
