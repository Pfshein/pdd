"""git worktree per job: create / diff / remove (no model needed)."""
import subprocess

import pytest

from orchestrator import config, worktree


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def target_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "worktrees")
    return repo


def test_create_diff_remove(target_repo):
    wt, branch, base_sha = worktree.create(target_repo, "JOB-1")
    assert wt.exists()
    assert branch == "pdd/JOB-1"

    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (wt / "new_file.py").write_text("VALUE = 1\n", encoding="utf-8")

    d = worktree.diff("JOB-1", base_sha)
    assert "a + b" in d           # modified file shows up
    assert "new_file.py" in d     # newly created file shows up too

    worktree.remove(target_repo, "JOB-1")
    assert not wt.exists()


def test_create_is_idempotent(target_repo):
    worktree.create(target_repo, "JOB-2")
    # creating again must not raise (stale worktree/branch cleared first)
    wt, _, _ = worktree.create(target_repo, "JOB-2")
    assert wt.exists()
    worktree.remove(target_repo, "JOB-2")


def test_rejects_unsafe_job_id(target_repo):
    with pytest.raises(ValueError):
        worktree.create(target_repo, "../NOPE")


def test_create_clears_orphaned_dir(target_repo):
    # A leftover dir from a previous run/repo that git does not know about here.
    orphan = config.WORKTREES_DIR / "JOB-ORPH"
    orphan.mkdir(parents=True)
    (orphan / "stale.txt").write_text("old", encoding="utf-8")

    wt, _, _ = worktree.create(target_repo, "JOB-ORPH")
    assert wt.exists()
    assert not (wt / "stale.txt").exists()  # orphan was force-cleared
    worktree.remove(target_repo, "JOB-ORPH")
