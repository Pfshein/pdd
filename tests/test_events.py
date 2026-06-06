"""Structured events.jsonl artifact."""
from orchestrator import config, events


def test_record_and_read_events(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")

    events.record("JOB-EVT", "stage_end", stage="TEST_RUN", duration_ms=12, weird=object())

    rows = events.read("JOB-EVT")
    assert rows[0]["job"] == "JOB-EVT"
    assert rows[0]["event"] == "stage_end"
    assert rows[0]["stage"] == "TEST_RUN"
    assert rows[0]["duration_ms"] == 12
    assert isinstance(rows[0]["weird"], str)

