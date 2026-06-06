"""exit-55 disambiguation: tool-call vs wall-time, and the wall-time retry (#4)."""
from orchestrator import config, runner

WALL_MSG = "Run aborted: wall-clock budget of 1s exceeded (--max-wall-time)."
TOOL_MSG = "Run aborted: tool-call budget of 0 exceeded (--max-tool-calls); observed 1."


def test_classify_limit_distinguishes_reasons():
    assert runner.classify_limit({"exit_code": 55, "stderr": TOOL_MSG}) == "tool_calls"
    assert runner.classify_limit({"exit_code": 55, "stderr": WALL_MSG}) == "wall_time"
    assert runner.classify_limit({"exit_code": 55, "stderr": "weird"}) == "unknown"
    assert runner.classify_limit({"exit_code": 0, "stderr": ""}) is None
    assert runner.classify_limit({"exit_code": 1, "stderr": ""}) is None


def _creds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    monkeypatch.setattr(runner, "stage_env", lambda: {})


def test_wall_time_limit_retries_with_bigger_budget(monkeypatch):
    _creds(monkeypatch)
    walls = []

    def fake_run_process(argv, **kw):
        wt = int(argv[argv.index("--max-wall-time") + 1])
        walls.append(wt)
        if len(walls) == 1:
            return {"exit_code": 55, "stdout": "", "stderr": WALL_MSG, "timed_out": False}
        return {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False}

    monkeypatch.setattr(runner, "run_process", fake_run_process)

    res = runner.run_qwen_stage("hi", wall_time_s=100)

    assert len(walls) == 2 and walls[1] > walls[0]  # retried with a bigger budget
    assert res["exit_code"] == 0
    assert res["limit"] is None  # resolved after the retry


def test_tool_call_limit_does_not_retry(monkeypatch):
    _creds(monkeypatch)
    calls = []

    def fake_run_process(argv, **kw):
        calls.append(1)
        return {"exit_code": 55, "stdout": "", "stderr": TOOL_MSG, "timed_out": False}

    monkeypatch.setattr(runner, "run_process", fake_run_process)

    res = runner.run_qwen_stage("hi", wall_time_s=100)

    assert len(calls) == 1  # stuck != slow -> no retry
    assert res["limit"] == "tool_calls"


def test_wall_retry_capped(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setattr(config, "STAGE_WALL_MAX_S", 100)  # already at cap
    calls = []
    monkeypatch.setattr(
        runner, "run_process",
        lambda argv, **kw: calls.append(1) or {"exit_code": 55, "stdout": "", "stderr": WALL_MSG, "timed_out": False},
    )

    res = runner.run_qwen_stage("hi", wall_time_s=100)

    assert len(calls) == 1  # bigger budget would not exceed the cap -> no retry
    assert res["limit"] == "wall_time"
