"""Durable file-based job queue (V1)."""
import pytest

from orchestrator import config, queue


def _patch_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def _enqueue(job, now):
    return queue.enqueue(
        job, repo="/repo", task="/repo/task.md", meta="/repo/task_meta.json", now=now
    )


def test_enqueue_creates_one_record(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)

    rec = _enqueue("DEMO-1", now=100.0)

    assert rec["status"] == queue.QUEUED
    assert rec["lease"] is None
    assert rec["created_ts"] == 100.0
    on_disk = queue.get("DEMO-1")
    assert on_disk == rec
    assert (tmp_path / "runs" / "queue" / "DEMO-1.json").exists()


def test_list_jobs_sorted_by_created_time(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("LATE", now=200.0)
    _enqueue("EARLY", now=100.0)

    jobs = [r["job"] for r in queue.list_jobs()]

    assert jobs == ["EARLY", "LATE"]


def test_enqueue_rejects_active_duplicate(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("DEMO-1", now=100.0)

    with pytest.raises(ValueError):
        _enqueue("DEMO-1", now=101.0)


def test_invalid_job_id_is_rejected(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        _enqueue("bad/id", now=100.0)


def test_acquire_returns_oldest_and_writes_lease(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("LATE", now=200.0)
    _enqueue("EARLY", now=100.0)

    leased = queue.acquire(worker="w1", now=300.0)

    assert leased["job"] == "EARLY"
    assert leased["status"] == queue.LEASED
    assert leased["lease"]["worker"] == "w1"
    assert leased["lease"]["token"]
    assert leased["lease"]["ts"] == 300.0
    # persisted, not just returned
    assert queue.get("EARLY")["status"] == queue.LEASED


def test_acquire_skips_already_leased_records(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("A", now=100.0)
    _enqueue("B", now=200.0)
    first = queue.acquire(now=300.0)

    second = queue.acquire(now=301.0)

    assert first["job"] == "A"
    assert second["job"] == "B"


def test_acquire_returns_none_when_idle(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)

    assert queue.acquire(now=100.0) is None


def test_release_marks_final_status_and_clears_lease(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("DEMO-1", now=100.0)
    queue.acquire(now=200.0)

    released = queue.release("DEMO-1", queue.DONE, now=300.0)

    assert released["status"] == queue.DONE
    assert released["lease"] is None
    assert released["updated_ts"] == 300.0


def test_release_rejects_non_terminal_status(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("DEMO-1", now=100.0)

    with pytest.raises(ValueError):
        queue.release("DEMO-1", queue.RUNNING, now=200.0)


def test_stale_lease_is_detected_and_returns_to_queued(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("DEMO-1", now=100.0)
    queue.acquire(now=200.0)

    # fresh: within ttl -> not stale, not reclaimed
    assert queue.reclaim_stale(now=250.0, ttl=100.0) == []

    # old: lease ts (200) is older than ttl -> stale, reclaimed
    assert queue.is_stale(queue.get("DEMO-1"), now=400.0, ttl=100.0) is True
    reclaimed = queue.reclaim_stale(now=400.0, ttl=100.0)

    assert reclaimed == ["DEMO-1"]
    back = queue.get("DEMO-1")
    assert back["status"] == queue.QUEUED
    assert back["lease"] is None
    # reclaimed work can be picked up again
    assert queue.acquire(now=500.0)["job"] == "DEMO-1"


def test_terminal_record_is_not_reclaimed(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue("DEMO-1", now=100.0)
    queue.acquire(now=200.0)
    queue.release("DEMO-1", queue.FAILED, now=210.0)

    assert queue.reclaim_stale(now=9999.0, ttl=1.0) == []
    assert queue.get("DEMO-1")["status"] == queue.FAILED
