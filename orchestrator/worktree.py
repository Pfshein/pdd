"""git worktree per job: isolated checkout for clean rollback + natural diff.

Correlation id (the Jira key) names the branch and worktree dir, so parallel
jobs never collide and the reviewer gets a real unified diff for free.
"""
import subprocess
from pathlib import Path

from . import config


def _git(args, cwd):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def branch_name(job: str) -> str:
    return f"pdd/{job}"


def worktree_path(job: str) -> Path:
    return config.WORKTREES_DIR / job


def create(repo, job: str, base_ref: str = "HEAD"):
    """Create a fresh worktree+branch for the job. Returns (path, branch, base_sha)."""
    repo = Path(repo)
    wt = worktree_path(job)
    branch = branch_name(job)
    config.WORKTREES_DIR.mkdir(parents=True, exist_ok=True)

    remove(repo, job)  # clear any stale worktree/branch first

    base_sha = _git(["rev-parse", base_ref], repo).stdout.strip()
    r = _git(["worktree", "add", "-B", branch, str(wt), base_sha], repo)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {r.stderr.strip()}")
    return wt, branch, base_sha


def diff(job: str, base_sha: str) -> str:
    """Unified diff of all changes in the worktree vs the base commit.

    Stages everything first so newly created files appear in the diff too.
    """
    wt = worktree_path(job)
    _git(["add", "-A"], wt)
    return _git(
        [
            "diff", "--cached", base_sha, "--", ".",
            ":(exclude)**/__pycache__/**",
            ":(exclude)**/*.pyc",
        ],
        wt,
    ).stdout


def remove(repo, job: str) -> None:
    """Tear down the worktree and its branch. Idempotent."""
    repo = Path(repo)
    wt = worktree_path(job)
    _git(["worktree", "remove", "--force", str(wt)], repo)
    _git(["worktree", "prune"], repo)
    _git(["branch", "-D", branch_name(job)], repo)
