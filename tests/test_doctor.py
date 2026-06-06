"""doctor: environment self-check (PDD-10)."""
from orchestrator import cli, config, doctor, sandbox


def _all_ok(monkeypatch):
    monkeypatch.setattr(doctor, "_which", lambda n: f"/usr/bin/{n}")
    monkeypatch.setattr(doctor, "_run", lambda args, timeout=30: (0, "version 1.0"))
    monkeypatch.setattr(config, "model_env",
                        lambda: {"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "b", "OPENAI_MODEL": "m"})
    monkeypatch.setattr(sandbox, "docker_available", lambda: True)
    monkeypatch.setattr(sandbox, "network_is_internal", lambda net=None: True)


def _by_name(checks, name):
    return next(c for c in checks if c["name"] == name)


def test_all_green_has_no_failures(monkeypatch):
    _all_ok(monkeypatch)
    checks = doctor.run_checks()
    names = {c["name"] for c in checks}
    assert {"python", "git", "qwen", "model creds", "docker daemon", "sandbox network"} <= names
    assert not doctor.has_failures(checks)


def test_missing_creds_is_fail(monkeypatch):
    _all_ok(monkeypatch)
    monkeypatch.setattr(config, "model_env", lambda: {})
    checks = doctor.run_checks()
    assert _by_name(checks, "model creds")["status"] == "fail"
    assert doctor.has_failures(checks)


def test_docker_daemon_down_is_fail(monkeypatch):
    _all_ok(monkeypatch)
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    checks = doctor.run_checks()
    assert _by_name(checks, "docker daemon")["status"] == "fail"


def test_non_internal_network_is_fail(monkeypatch):
    _all_ok(monkeypatch)
    monkeypatch.setattr(sandbox, "network_is_internal", lambda net=None: False)
    checks = doctor.run_checks()
    assert _by_name(checks, "sandbox network")["status"] == "fail"


def test_format_renders_status_and_hint():
    s = doctor.format_checks([
        {"name": "x", "status": "fail", "detail": "d", "hint": "do y"},
        {"name": "ok1", "status": "ok", "detail": "", "hint": ""},
    ])
    assert "[FAIL] x: d  -> do y" in s
    assert "[ OK ] ok1" in s


def test_cli_doctor_returns_1_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_checks",
                        lambda: [{"name": "x", "status": "fail", "detail": "", "hint": ""}])
    assert cli.main(["doctor"]) == 1
    assert "[FAIL] x" in capsys.readouterr().out
