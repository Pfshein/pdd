"""Single worker loop over the durable queue."""
from orchestrator import config, events, queue, run as run_mod, worker


def _patch_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")


def _enqueue(tmp_path, job="DEMO-1"):
    task = tmp_path / "task.md"
    task.write_text("fix it", encoding="utf-8")
    meta = tmp_path / "task_meta.json"
    meta.write_text("{}", encoding="utf-8")
    return queue.enqueue(job, repo=str(tmp_path / "repo"), task=str(task), meta=str(meta))


def test_no_work_returns_none(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)

    assert worker.process_one() is None


def test_worker_once_exits_zero_when_idle(tmp_path, monkeypatch, capsys):
    _patch_runs(tmp_path, monkeypatch)

    assert worker.run_worker(once=True) == 0
    assert "no queued work" in capsys.readouterr().out


def test_successful_pipeline_marks_done(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "DONE"})

    result = worker.process_one(worker="w1")

    assert result == {"job": "DEMO-1", "status": queue.DONE, "node": "DONE"}
    rec = queue.get("DEMO-1")
    assert rec["status"] == queue.DONE
    assert rec["lease"] is None  # released, not left leased
    assert any(e["event"] == "worker_finished" for e in events.read("DEMO-1"))


def test_needs_human_pipeline_marks_needs_human(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "NEEDS_HUMAN"})

    result = worker.process_one()

    assert result["status"] == queue.NEEDS_HUMAN
    assert queue.get("DEMO-1")["status"] == queue.NEEDS_HUMAN


def test_exception_marks_failed_and_stores_error(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("worktree exploded")

    monkeypatch.setattr(run_mod, "run_pipeline", boom)

    result = worker.process_one()

    assert result["status"] == queue.FAILED
    rec = queue.get("DEMO-1")
    assert rec["status"] == queue.FAILED
    assert rec["lease"] is None  # never left leased after an exception
    from orchestrator import artifacts
    assert "worktree exploded" in artifacts.read_text("DEMO-1", "worker_error.txt")


def test_worker_processes_one_then_stops_with_once(tmp_path, monkeypatch, capsys):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path, "A")
    _enqueue(tmp_path, "B")
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "DONE"})

    assert worker.run_worker(once=True) == 0

    # exactly one job consumed; the other stays queued
    statuses = {r["job"]: r["status"] for r in queue.list_jobs()}
    assert statuses == {"A": queue.DONE, "B": queue.QUEUED}
    assert "A -> done" in capsys.readouterr().out


# --- Auto publish on DONE (PDD-37) ----------------------------------------
def test_publish_on_done_writes_outcome_to_queue(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "DONE"})
    from orchestrator import publish as publish_mod
    monkeypatch.setattr(publish_mod, "publish",
                        lambda job, push=False: {"branch": f"pdd/{job}", "committed": "abc", "pushed": push})

    result = worker.process_one(publish=True, push=True)

    assert result["status"] == queue.DONE
    assert result["publish"]["ok"] is True and result["publish"]["pushed"] is True
    rec = queue.get("DEMO-1")
    assert rec["status"] == queue.DONE          # status preserved
    assert rec["publish"]["ok"] is True          # outcome recorded on the queue record


def test_publish_failure_keeps_done_status(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "DONE"})
    from orchestrator import publish as publish_mod

    def boom(job, push=False):
        raise publish_mod.PublishError("no worktree")

    monkeypatch.setattr(publish_mod, "publish", boom)

    result = worker.process_one(publish=True)

    assert result["status"] == queue.DONE        # DONE not lost
    assert result["publish"]["ok"] is False
    assert "no worktree" in result["publish"]["error"]
    assert queue.get("DEMO-1")["status"] == queue.DONE


def test_publish_is_opt_in(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "DONE"})
    from orchestrator import publish as publish_mod

    def fail(*a, **k):
        raise AssertionError("publish must not be called without --publish")

    monkeypatch.setattr(publish_mod, "publish", fail)

    result = worker.process_one()  # no publish flag

    assert "publish" not in result


def test_publish_skipped_for_needs_human(tmp_path, monkeypatch):
    _patch_runs(tmp_path, monkeypatch)
    _enqueue(tmp_path)
    monkeypatch.setattr(run_mod, "run_pipeline", lambda *a, **k: {"node": "NEEDS_HUMAN"})
    from orchestrator import publish as publish_mod
    monkeypatch.setattr(publish_mod, "publish",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("not DONE")))

    result = worker.process_one(publish=True)

    assert result["status"] == queue.NEEDS_HUMAN
    assert "publish" not in result
