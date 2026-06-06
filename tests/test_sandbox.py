"""Sandbox invariants: fail-closed and no secrets in docker argv."""
import subprocess

import pytest

from orchestrator import config, sandbox, testrun


def test_ensure_ready_fail_closed_without_docker(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    monkeypatch.setattr(config, "REQUIRE_SANDBOX", True)
    monkeypatch.setattr(config, "ALLOW_UNSANDBOXED", False)

    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.ensure_ready()


def test_ensure_ready_allows_explicit_unsandboxed_override(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: False)
    monkeypatch.setattr(config, "REQUIRE_SANDBOX", True)
    monkeypatch.setattr(config, "ALLOW_UNSANDBOXED", True)

    assert sandbox.ensure_ready() == "UNSANDBOXED"


def test_ensure_ready_prefers_docker(monkeypatch):
    monkeypatch.setattr(sandbox, "docker_available", lambda: True)
    monkeypatch.setattr(config, "ALLOW_UNSANDBOXED", True)

    assert sandbox.ensure_ready() == "docker"


def test_docker_run_argv_contains_hardening_flags_without_secret_values(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SANDBOX_IMAGE", "pdd-test:latest")
    monkeypatch.setattr(config, "SANDBOX_NETWORK", "pdd-egress-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")

    argv = sandbox.docker_run_argv(["qwen"], tmp_path)
    joined = " ".join(argv)

    assert "--read-only" in argv
    assert "--cap-drop" in argv
    assert "ALL" in argv
    assert "--security-opt" in argv
    assert "no-new-privileges" in argv
    assert "--network" in argv
    assert "pdd-egress-test" in argv
    assert "HOME=/tmp/pdd-home" in argv
    assert "XDG_CACHE_HOME=/tmp/pdd-cache" in argv
    assert "OPENAI_API_KEY" in argv
    assert "sk-secret-value" not in joined


def test_docker_build_argv_defaults_to_project_dockerfile(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_IMAGE", "pdd-test:latest")

    argv = sandbox.docker_build_argv(qwen_package="qwen-test")

    assert argv[:4] == ["docker", "build", "-t", "pdd-test:latest"]
    assert "-f" in argv
    assert "sandbox" in argv[argv.index("-f") + 1]
    assert "--build-arg" in argv
    assert "QWEN_NPM_PACKAGE=qwen-test" in argv


def test_dockerfile_uses_node_22_stage():
    dockerfile = (config.ROOT / "sandbox" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-slim AS node" in dockerfile
    assert "COPY --from=node" in dockerfile
    assert "npm-cli.js" in dockerfile


def test_docker_network_create_argv(monkeypatch):
    monkeypatch.setattr(config, "SANDBOX_NETWORK", "pdd-egress-test")
    assert sandbox.docker_network_create_argv() == [
        "docker", "network", "create", "pdd-egress-test"
    ]
    assert sandbox.docker_network_inspect_argv() == [
        "docker", "network", "inspect", "pdd-egress-test"
    ]


def test_docker_run_argv_has_init_and_optional_name(tmp_path):
    base = sandbox.docker_run_argv(["qwen"], tmp_path)
    assert "--init" in base
    assert "--name" not in base

    named = sandbox.docker_run_argv(["qwen"], tmp_path, name="pdd-abc123")
    assert named[named.index("--name") + 1] == "pdd-abc123"


def test_run_in_sandbox_force_removes_container_on_timeout(tmp_path, monkeypatch):
    from orchestrator import runner

    monkeypatch.setattr(runner, "stage_env", lambda: {})
    monkeypatch.setattr(
        runner, "run_process",
        lambda argv, **kw: {"exit_code": None, "stdout": "", "stderr": "", "timed_out": True},
    )
    calls = []
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    res = sandbox.run_in_sandbox(["sh", "-lc", "sleep 100"], tmp_path)

    assert res["timed_out"] is True
    assert res["container"].startswith("pdd-")
    assert any(c[:2] == ["docker", "kill"] for c in calls)
    assert any(c[:3] == ["docker", "rm", "-f"] for c in calls)


def test_run_in_sandbox_no_teardown_on_success(tmp_path, monkeypatch):
    from orchestrator import runner

    monkeypatch.setattr(runner, "stage_env", lambda: {})
    monkeypatch.setattr(
        runner, "run_process",
        lambda argv, **kw: {"exit_code": 0, "stdout": "ok", "stderr": "", "timed_out": False},
    )
    calls = []
    monkeypatch.setattr(sandbox.subprocess, "run", lambda cmd, **kw: calls.append(cmd))

    res = sandbox.run_in_sandbox(["sh"], tmp_path)

    assert res["timed_out"] is False
    assert calls == []  # --rm handles cleanup; no force-remove on success


def test_unsandboxed_test_run_writes_security_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(sandbox, "ensure_ready", lambda: "UNSANDBOXED")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = testrun.run_tests("JOB-SAFE", tmp_path, "echo ok")

    assert result["status"] == "green"
    security = (config.RUNS_DIR / "JOB-SAFE" / "SECURITY.txt").read_text(encoding="utf-8")
    assert "TEST_RUN ran UNSANDBOXED" in security
