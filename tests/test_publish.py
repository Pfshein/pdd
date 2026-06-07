"""publish: commit the job worktree to its branch (push/PR are mocked)."""
import subprocess

import pytest

from orchestrator import artifacts, config, publish, state as state_mod, worktree


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "wt")
    r = tmp_path / "repo"
    r.mkdir()
    _git(["init", "-q"], r)
    _git(["config", "user.email", "t@t"], r)
    _git(["config", "user.name", "t"], r)
    (r / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    _git(["add", "-A"], r)
    _git(["commit", "-q", "-m", "init"], r)
    return r


def _seed_job(job, repo):
    wt, branch, base_sha = worktree.create(repo, job)
    artifacts.write_json(job, "job_meta.json", {
        "job": job, "repo": str(repo), "base_ref": "HEAD",
        "base_sha": base_sha, "branch": branch, "worktree": str(wt),
    })
    artifacts.write_text(job, "task.md", "# Fix add()\nmake it add")
    artifacts.write_text(job, "changes.md", "fixed add to return a + b")
    return wt, branch


def test_publish_commits_worktree(repo):
    wt, branch = _seed_job("JOB-P", repo)
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    res = publish.publish("JOB-P", push=False)

    assert res["committed"]
    assert res["branch"] == "pdd/JOB-P"
    assert res["pushed"] is False
    # the commit lives in the standalone clone (the per-job checkout), not the source
    log = subprocess.run(["git", "log", "--oneline", "-1", "pdd/JOB-P"],
                         cwd=wt, capture_output=True, text=True).stdout
    assert "JOB-P: Fix add()" in log
    assert (state_mod.job_dir("JOB-P") / "publish.json").exists()


def test_publish_excludes_pycache(repo):
    wt, _ = _seed_job("JOB-PYC", repo)
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (wt / "__pycache__").mkdir()
    (wt / "__pycache__" / "calc.pyc").write_bytes(b"\x00\x01")

    publish.publish("JOB-PYC", push=False)

    tree = subprocess.run(["git", "show", "--name-only", "--format=", "pdd/JOB-PYC"],
                          cwd=wt, capture_output=True, text=True).stdout
    assert "calc.py" in tree
    assert "__pycache__" not in tree


def test_publish_nothing_to_commit(repo):
    _seed_job("JOB-EMPTY", repo)  # no edits

    res = publish.publish("JOB-EMPTY", push=False)

    assert res["committed"] is None
    assert "nothing new to commit" in res.get("note", "")


def test_publish_requires_job_meta(repo, monkeypatch):
    state_mod.job_dir("JOB-NM")  # exists but no job_meta.json
    with pytest.raises(publish.PublishError):
        publish.publish("JOB-NM", push=False)


def test_cli_publish_invokes_publish(monkeypatch, capsys):
    from orchestrator import cli

    monkeypatch.setattr(
        publish, "publish",
        lambda job, **kw: {"branch": "pdd/X", "committed": "abc", "pushed": kw.get("push"), "pr_url": None},
    )
    assert cli.main(["publish", "X", "--push"]) == 0
    assert '"committed": "abc"' in capsys.readouterr().out


def test_publish_pushes_even_without_new_commit(repo, monkeypatch):
    _seed_job("JOB-IP", repo)  # no edits -> nothing new to commit
    monkeypatch.setattr(publish, "_push", lambda wt, branch: True)

    res = publish.publish("JOB-IP", push=True)

    assert res["committed"] is None   # nothing new this call
    assert res["pushed"] is True      # ...but push still happened (idempotent)


def test_publish_reports_pr_create_url(repo, monkeypatch):
    _git(["remote", "add", "origin", "https://github.com/Pfshein/pdd.git"], repo)
    wt, _ = _seed_job("JOB-PR", repo)
    (wt / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    monkeypatch.setattr(publish, "_push", lambda wt, branch: True)

    res = publish.publish("JOB-PR", push=True)

    assert res["committed"] and res["pushed"] is True
    assert res["worktree"] == str(wt)
    assert res["pr_create_url"] == "https://github.com/Pfshein/pdd/pull/new/pdd/JOB-PR"


def test_pr_create_url_parsing():
    assert publish._pr_create_url("https://github.com/Pfshein/pdd.git", "pdd/T1") == \
        "https://github.com/Pfshein/pdd/pull/new/pdd/T1"
    assert publish._pr_create_url("git@github.com:Pfshein/pdd.git", "pdd/T1") == \
        "https://github.com/Pfshein/pdd/pull/new/pdd/T1"
    assert publish._pr_create_url("https://gitlab.com/x/y.git", "b") is None
