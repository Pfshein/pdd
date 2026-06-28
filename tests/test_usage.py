"""Per-stage token usage accounting (PDD-32, estimate-first)."""
import json

from orchestrator import config, usage


def _events(assistant_text=None, result_event=None, extra=None):
    evs = []
    if assistant_text:
        evs.append({"type": "assistant", "message": {"content": [{"type": "text", "text": assistant_text}]}})
    if extra:
        evs.extend(extra)
    evs.append(result_event or {"type": "result", "is_error": False})
    return {"stdout": json.dumps(evs)}


def test_estimate_tokens_is_length_based():
    assert usage.estimate_tokens("") == 0
    assert usage.estimate_tokens("a") == 1
    assert usage.estimate_tokens("x" * 40) == 10


def test_record_estimates_when_no_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    res = _events(assistant_text="applied the fix")

    row = usage.record("JOB-U", "CODER", prompt="x" * 40, result=res)

    assert row["source"] == "estimate"
    assert row["input_tokens"] == 10              # 40 chars / 4
    assert row["output_tokens"] >= 1              # from assistant text
    assert row["total_tokens"] == row["input_tokens"] + row["output_tokens"]
    assert usage.read("JOB-U") == [row]


def test_record_prefers_authoritative_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    res = _events(result_event={
        "type": "result", "is_error": False,
        "usage": {"input_tokens": 123, "output_tokens": 45},
    })

    row = usage.record("JOB-A", "CODER", prompt="ignored-because-authoritative", result=res)

    assert row["source"] == "qwen_event"
    assert row == {**row, "input_tokens": 123, "output_tokens": 45, "total_tokens": 168}


def test_extract_usage_supports_alt_field_names():
    res = {"stdout": json.dumps([
        {"type": "result", "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    ])}
    assert usage.extract_usage(res) == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}


def test_extract_usage_absent_returns_none():
    assert usage.extract_usage(_events(assistant_text="hi")) is None


def test_record_never_raises_on_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    # not valid JSON stdout, and missing fields -> still no exception, returns a row
    row = usage.record("JOB-G", "CODER", prompt="abcd", result={"stdout": "not json"})
    assert row["source"] == "estimate"
    assert row["output_tokens"] == 0  # unparseable response -> no output estimate


def test_totals_sums_rows_and_flags_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    usage.record("JOB-T", "ARCHITECT", prompt="x" * 40, result=_events("plan"))
    usage.record("JOB-T", "CODER", prompt="x" * 80, result=_events("code"))

    t = usage.totals("JOB-T")

    assert t["rows"] == 2
    assert t["input_tokens"] == 30  # 10 + 20
    assert t["estimated"] is True


# --- Cost estimation (PDD-33) ---------------------------------------------
def test_estimate_cost_none_when_no_rates():
    assert usage.estimate_cost(1000, 1000, None, None) is None


def test_estimate_cost_computes_from_rates():
    # 2M input @ $1/1M + 1M output @ $3/1M = $2 + $3 = $5
    assert usage.estimate_cost(2_000_000, 1_000_000, 1.0, 3.0) == 5.0


def test_estimate_cost_handles_one_rate_only():
    assert usage.estimate_cost(1_000_000, 1_000_000, 2.0, None) == 2.0


def test_cost_summary_uses_config_rates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "MODEL_INPUT_PRICE_PER_1M", 1.0)
    monkeypatch.setattr(config, "MODEL_OUTPUT_PRICE_PER_1M", 3.0)
    usage.record("JOB-C", "CODER", prompt="x" * 4_000_000, result=_events("y" * 4_000_000))

    s = usage.cost_summary("JOB-C")

    assert s["cost_usd"] is not None and s["cost_usd"] > 0


def test_cost_summary_none_cost_without_rates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "MODEL_INPUT_PRICE_PER_1M", None)
    monkeypatch.setattr(config, "MODEL_OUTPUT_PRICE_PER_1M", None)
    usage.record("JOB-N", "CODER", prompt="x" * 40, result=_events("y"))

    assert usage.cost_summary("JOB-N")["cost_usd"] is None
