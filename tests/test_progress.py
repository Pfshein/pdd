"""Live console progress (PDD-19): event subscriptions + formatter + CLI wiring."""
import io

from orchestrator import artifacts, cli, config, events, progress, queue


def test_events_subscribe_and_unsubscribe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    got = []
    sub = got.append
    events.subscribe(sub)
    try:
        events.record("JOB-EV", "stage_start", stage="CODER")
    finally:
        events.unsubscribe(sub)
    events.record("JOB-EV", "stage_end", stage="CODER")  # after unsubscribe -> ignored

    assert len(got) == 1
    assert got[0]["event"] == "stage_start" and got[0]["stage"] == "CODER"


def test_subscriber_exception_never_breaks_record(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")

    def boom(_row):
        raise RuntimeError("subscriber blew up")

    events.subscribe(boom)
    try:
        row = events.record("JOB-EX", "job_start", node="INTAKE")
    finally:
        events.unsubscribe(boom)
    assert row["event"] == "job_start"  # the run is never broken by a live consumer


def test_format_event_lines():
    assert progress.format_event({"event": "job_start", "job": "J", "node": "INTAKE"}).startswith("== J: start")
    line = progress.format_event(
        {"event": "stage_end", "stage": "CODER", "duration_ms": 46557, "next": "CODE_REVIEW", "status": "ok"}
    )
    assert "ok CODER" in line and "-> CODE_REVIEW" in line and "status=ok" in line
    assert progress.format_event({"event": "transition", "frm": "A", "to": "B"}) is None
    assert "finished -> DONE" in progress.format_event({"event": "job_end", "job": "J", "node": "DONE"})


def test_cli_run_streams_progress_to_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    task = tmp_path / "task.md"; task.write_text("t", encoding="utf-8")
    meta = tmp_path / "meta.json"; meta.write_text('{"issue_type": "bug"}', encoding="utf-8")

    def fake_pipeline(job, repo, **kw):
        events.record(job, "stage_end", stage="CODER", duration_ms=10, next="CODE_REVIEW", status="ok")
        return {"node": "DONE", "global_steps": 1, "global_step_cap": 30, "budgets": {}}

    monkeypatch.setattr(cli.run_mod, "run_pipeline", fake_pipeline)

    rc = cli.main(["run", "--job", "J", "--repo", str(tmp_path),
                   "--task", str(task), "--meta", str(meta)])

    assert rc == 0
    assert "ok CODER" in capsys.readouterr().err  # progress streamed live (stderr)


def test_cli_run_quiet_suppresses_progress(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    task = tmp_path / "task.md"; task.write_text("t", encoding="utf-8")
    meta = tmp_path / "meta.json"; meta.write_text('{"issue_type": "bug"}', encoding="utf-8")

    monkeypatch.setattr(
        cli.run_mod, "run_pipeline",
        lambda job, repo, **kw: (events.record(job, "stage_end", stage="CODER", next="X"),
                                 {"node": "DONE", "global_steps": 1, "global_step_cap": 30, "budgets": {}})[1],
    )
    cli.main(["run", "--job", "JQ", "--repo", str(tmp_path),
              "--task", str(task), "--meta", str(meta), "--quiet"])
    assert "ok CODER" not in capsys.readouterr().err


def test_cli_worker_streams_progress_and_publish_to_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    repo = tmp_path / "repo"; repo.mkdir()
    task = tmp_path / "task.md"; task.write_text("t", encoding="utf-8")
    meta = tmp_path / "meta.json"; meta.write_text("{}", encoding="utf-8")
    queue.enqueue("JW", repo=str(repo), task=str(task), meta=str(meta))

    def fake_pipeline(job, repo, **kw):
        artifacts.write_text(job, "plan.md", "Plan:\n- do the worker thing")
        events.record(job, "stage_end", stage="ARCHITECT", duration_ms=5, next="CODER", status="ok")
        events.record(job, "stage_start", stage="CODER")
        events.record(job, "stage_end", stage="CODER", duration_ms=10, next="CODE_REVIEW", status="ok")
        events.record(job, "job_end", node="DONE")
        return {"node": "DONE"}

    monkeypatch.setattr(cli.run_mod, "run_pipeline", fake_pipeline)
    from orchestrator import publish as publish_mod
    monkeypatch.setattr(
        publish_mod, "publish",
        lambda job, push=False: {"branch": f"pdd/{job}", "committed": "abc", "pushed": push},
    )

    assert cli.main(["worker", "--once", "--publish"]) == 0

    captured = capsys.readouterr()
    assert "worker: JW -> done" in captured.out
    assert "JW: worker started" in captured.err
    assert "ARCHITECT -> CODER (plan.md)" in captured.err
    assert "| - do the worker thing" in captured.err
    assert ".. CODER running" in captured.err
    assert "ok CODER" in captured.err
    assert ".. publish running" in captured.err
    assert "ok publish" in captured.err


def test_console_printer_logs_architect_plan_passed_to_coder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    artifacts.write_text("JPLAN", "plan.md", "Plan:\n- edit dashboard\n- add tests")
    stream = io.StringIO()
    printer = progress.console_printer(stream=stream)

    printer({
        "event": "stage_end",
        "job": "JPLAN",
        "stage": "ARCHITECT",
        "duration_ms": 1200,
        "next": "CODER",
        "status": "ok",
    })

    out = stream.getvalue()
    assert "ok ARCHITECT" in out
    assert "ARCHITECT -> CODER (plan.md)" in out
    assert "| - edit dashboard" in out


def test_format_event_shows_budget_on_return_target():
    line = progress.format_event(
        {"event": "stage_end", "stage": "CODE_REVIEW", "duration_ms": 1200,
         "next": "CODER", "status": "ok", "budget": "2/4"}
    )
    assert "-> CODER" in line and "budget=2/4" in line
