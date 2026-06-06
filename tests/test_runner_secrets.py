"""#2: the API key must never reach argv — only the process env."""
from orchestrator import runner

SENTINEL = "pdd-test-sentinel-key-value"  # deliberately not a real-looking token


def test_api_key_absent_from_argv():
    argv = runner.build_qwen_argv(model="test-model", base_url="https://example/v1")
    assert "--openai-api-key" not in argv
    assert not any(SENTINEL in str(a) for a in argv)


def test_build_argv_signature_has_no_api_key():
    import inspect

    params = inspect.signature(runner.build_qwen_argv).parameters
    assert "api_key" not in params  # cannot accidentally pass a key to argv


def test_key_delivered_via_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    env = runner.stage_env()
    assert env["OPENAI_API_KEY"] == SENTINEL


def test_yolo_warning_not_suppressed(monkeypatch):
    monkeypatch.delenv("QWEN_CODE_SUPPRESS_YOLO_WARNING", raising=False)
    env = runner.stage_env()
    assert "QWEN_CODE_SUPPRESS_YOLO_WARNING" not in env
