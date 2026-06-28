"""TTL reaper for stale jobs/worktrees."""
import os

from orchestrator import config, graph, queue, reaper, state as state_mod


def _seed_job(job, node, tmp_path, monkeypatch, *, age_s=0):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "WORKTREES_DIR", tmp_path / "worktrees")
    st = state_mod.new_state(job)
    st["node"] = node
    state_mod.save_state(st)
    state_path = state_mod.job_dir(job) / "state.json"
    now = 10_000
    os.utime(state_path, (now - age_s, now - age_s))
    return now


def test_stale_jobs_finds_only_old_non_terminal_jobs(tmp_path, monkeypatch):
    now = _seed_job("JOB-OLD", graph.CODER, tmp_path, monkeypatch, age_s=500)
    _seed_job("JOB-NEW", graph.CODER, tmp_path, monkeypatch, age_s=10)
    _seed_job("JOB-DONE", graph.DONE, tmp_path, monkeypatch, age_s=500)

    rows = reaper.stale_jobs(now=now, ttl_s=100)

    assert [r["job"] for r in rows] == ["JOB-OLD"]
    assert rows[0]["node"] == graph.CODER


def test_reap_dry_run_does_not_touch_state_or_worktree(tmp_path, monkeypatch):
    now = _seed_job("JOB-DRY", graph.TEST_RUN, tmp_path, monkeypatch, age_s=500)
    wt = config.WORKTREES_DIR / "JOB-DRY"
    wt.mkdir(parents=True)

    rows = reaper.reap(dry_run=True, now=now, ttl_s=100)

    assert rows[0]["action"] == "would-reap"
    assert state_mod.load_state("JOB-DRY")["node"] == graph.TEST_RUN
    assert wt.exists()


def test_reap_apply_marks_needs_human_and_removes_worktree_dir(tmp_path, monkeypatch):
    now = _seed_job("JOB-REAP", graph.CODER, tmp_path, monkeypatch, age_s=500)
    wt = config.WORKTREES_DIR / "JOB-REAP"
    wt.mkdir(parents=True)
    (wt / "stale.txt").write_text("old", encoding="utf-8")

    rows = reaper.reap(dry_run=False, now=now, ttl_s=100)

    assert rows[0]["action"] == "reaped"
    assert rows[0]["worktree"] == "removed-dir"
    assert state_mod.load_state("JOB-REAP")["node"] == graph.NEEDS_HUMAN
    assert not wt.exists()
    assert (state_mod.job_dir("JOB-REAP") / "reaped.json").exists()



# --- queue lease reaping (PDD-28) -----------------------------------------
def _patch_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def _enqueue_and_lease(tmp_path, job, *, lease_ts):
    rec = queue.enqueue(job, repo=str(tmp_path / "repo"),
                        task=str(tmp_path / "t.md"), meta=str(tmp_path / "m.json"))
    queue.acquire(now=lease_ts)  # leases the oldest queued (this job)
    return rec


def test_reap_queue_requeues_stale_lease_without_state(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue_and_lease(tmp_path, "Q-STALE", lease_ts=100.0)

    rows = reaper.reap_queue(dry_run=False, now=400.0, ttl_s=100)

    assert rows[0]["job"] == "Q-STALE"
    assert rows[0]["to"] == queue.QUEUED
    back = queue.get("Q-STALE")
    assert back["status"] == queue.QUEUED and back["lease"] is None
    # pickable again
    assert queue.acquire(now=500.0)["job"] == "Q-STALE"


def test_reap_queue_adopts_terminal_state(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue_and_lease(tmp_path, "Q-DONE", lease_ts=100.0)
    # worker finished the pipeline (terminal state) but crashed before releasing
    st = state_mod.new_state("Q-DONE")
    st["node"] = graph.DONE
    state_mod.save_state(st)

    rows = reaper.reap_queue(dry_run=False, now=400.0, ttl_s=100)

    assert rows[0]["to"] == queue.DONE
    assert queue.get("Q-DONE")["status"] == queue.DONE


def test_reap_queue_skips_fresh_lease(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue_and_lease(tmp_path, "Q-FRESH", lease_ts=100.0)

    assert reaper.reap_queue(dry_run=False, now=150.0, ttl_s=100) == []
    assert queue.get("Q-FRESH")["status"] == queue.LEASED


def test_reap_queue_dry_run_does_not_mutate(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue_and_lease(tmp_path, "Q-DRY", lease_ts=100.0)

    rows = reaper.reap_queue(dry_run=True, now=400.0, ttl_s=100)

    assert rows[0]["action"] == "would-reap-queue"
    assert queue.get("Q-DRY")["status"] == queue.LEASED  # untouched
