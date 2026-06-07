"""Per-job standalone checkout: clean rollback + natural diff + container-safe.

Correlation id (the Jira key) names the branch and checkout dir, so parallel jobs
never collide and the reviewer gets a real unified diff for free.

We use a local CLONE, not `git worktree`: the checkout is bind-mounted ALONE into
the sandbox container, and a worktree's `.git` is only a link to the parent repo's
gitdir (an absolute host path that isn't mounted), so git fails inside the
container. A clone is a self-contained repo, so in-container git just works.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

from . import config, state as state_mod


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
    job = state_mod.validate_job_id(job)
    return f"pdd/{job}"


def worktree_path(job: str) -> Path:
    job = state_mod.validate_job_id(job)
    return config.WORKTREES_DIR / job


def create(repo, job: str, base_ref: str = "HEAD"):
    """Standalone clone + job branch at base_ref. Returns (path, branch, base_sha)."""
    repo = Path(repo)
    wt = worktree_path(job)
    branch = branch_name(job)
    config.WORKTREES_DIR.mkdir(parents=True, exist_ok=True)

    remove(repo, job)  # clear any stale checkout first

    base_sha = _git(["rev-parse", base_ref], repo).stdout.strip()
    if not base_sha:
        raise RuntimeError(f"cannot resolve base ref {base_ref!r} in {repo}")

    clone = _git(["clone", "--no-hardlinks", "--quiet", str(repo), str(wt)], config.WORKTREES_DIR)
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed: {clone.stderr.strip()}")

    co = _git(["checkout", "-B", branch, base_sha], wt)
    if co.returncode != 0:
        raise RuntimeError(f"git checkout {base_sha} failed: {co.stderr.strip()}")

    # A fresh clone has its own config: carry over the target repo's commit
    # identity (clone does NOT copy [user]) so `publish` can commit.
    for key in ("user.name", "user.email"):
        val = _git(["config", key], repo).stdout.strip()
        if val:
            _git(["config", key, val], wt)

    # Point origin at the SOURCE repo's real remote so `publish --push` targets it
    # (a fresh clone's origin is the local source path, not the upstream).
    src_origin = _git(["remote", "get-url", "origin"], repo).stdout.strip()
    if src_origin:
        _git(["remote", "set-url", "origin", src_origin], wt)
    else:
        _git(["remote", "remove", "origin"], wt)

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


def _on_rm_error(func, path, _exc):
    # Windows: .git/objects are read-only; clear the bit and retry.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _force_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onerror=_on_rm_error)


def remove(repo, job: str) -> None:
    """Tear down the per-job checkout. Idempotent."""
    repo = Path(repo)
    # Best-effort cleanup of any LEGACY git-worktree registration (pre-clone era);
    # harmless no-ops for a clone-based checkout.
    _git(["worktree", "remove", "--force", str(worktree_path(job))], repo)
    _git(["worktree", "prune"], repo)
    _git(["branch", "-D", branch_name(job)], repo)
    _force_rmtree(worktree_path(job))
