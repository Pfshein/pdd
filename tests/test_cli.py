"""CLI inspection commands for the user-facing job flow."""
import json

from orchestrator import cli, config, run as run_mod, sandbox, state as state_mod


def test_status_prints_job_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.save_state(state_mod.new_state("JOB-CLI"))
    (state_mod.job_dir("JOB-CLI") / "job_meta.json").write_text(
        json.dumps({"repo": "repo", "branch": "pdd/JOB-CLI", "worktree": "wt"}),
        encoding="utf-8",
    )

    assert cli.main(["status", "JOB-CLI"]) == 0
    out = capsys.readouterr().out
    assert "job: JOB-CLI" in out
    assert "node: INTAKE" in out
    assert "branch: pdd/JOB-CLI" in out


def test_show_prints_requested_artifact(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.job_dir("JOB-CLI")
    (state_mod.job_dir("JOB-CLI") / "verdict.json").write_text(
        '{"issues": []}', encoding="utf-8"
    )

    assert cli.main(["show", "JOB-CLI", "verdict.json"]) == 0
    out = capsys.readouterr().out
    assert "--- verdict.json ---" in out
    assert '"issues": []' in out


def test_diff_prints_saved_diff(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.job_dir("JOB-CLI")
    (state_mod.job_dir("JOB-CLI") / "diff.patch").write_text(
        "+return a + b\n", encoding="utf-8"
    )

    assert cli.main(["diff", "JOB-CLI"]) == 0
    assert "+return a + b" in capsys.readouterr().out


def test_sandbox_build_cli_invokes_docker_build(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_run_command", lambda argv: calls.append(argv) or 0)

    assert cli.main(["sandbox-build", "--image", "pdd-test:latest"]) == 0

    assert calls
    assert calls[0][:4] == ["docker", "build", "-t", "pdd-test:latest"]


def test_sandbox_network_creates_internal_when_absent(monkeypatch):
    calls = []
    monkeypatch.setattr(sandbox, "network_is_internal", lambda net=None: None)  # absent
    monkeypatch.setattr(cli, "_run_command", lambda argv: calls.append(argv) or 0)

    assert cli.main(["sandbox-network"]) == 0
    assert calls and "--internal" in calls[0]


def test_sandbox_network_refuses_non_internal(monkeypatch, capsys):
    monkeypatch.setattr(sandbox, "network_is_internal", lambda net=None: False)  # exists, free egress

    assert cli.main(["sandbox-network"]) == 2
    assert "NOT internal" in capsys.readouterr().err


def test_proxy_up_renders_conf_and_starts_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SANDBOX_PROXY_CONF", tmp_path / "squid.conf")
    monkeypatch.setattr(config, "model_host_allowlist", lambda: ["opencode.ai"])
    calls = []
    monkeypatch.setattr(cli, "_run_command", lambda argv: calls.append(argv) or 0)

    assert cli.main(["proxy-up"]) == 0
    assert (tmp_path / "squid.conf").exists()
    assert any(c[:3] == ["docker", "run", "-d"] for c in calls)
    assert any(c[:4] == ["docker", "network", "connect", "bridge"] for c in calls)


def test_setup_proxy_up_uses_setup_allowlist(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SANDBOX_SETUP_PROXY_CONF", tmp_path / "setup-squid.conf")
    monkeypatch.setattr(config, "setup_host_allowlist", lambda: ["pypi.org"])
    calls = []
    monkeypatch.setattr(cli, "_run_command", lambda argv: calls.append(argv) or 0)

    assert cli.main(["setup-proxy-up"]) == 0
    assert ".pypi.org" in (tmp_path / "setup-squid.conf").read_text(encoding="utf-8")
    assert any("pdd-setup-proxy" in c for c in calls)


def test_cli_resume_invokes_resume(monkeypatch, capsys):
    monkeypatch.setattr(run_mod, "resume_pipeline", lambda job: {"node": "DONE"})
    assert cli.main(["resume", "JOB"]) == 0
    assert "JOB -> DONE" in capsys.readouterr().out


def test_cli_retry_invokes_retry(monkeypatch, capsys):
    seen = {}

    def fake_retry(job, stage):
        seen["stage"] = stage
        return {"node": "DONE"}

    monkeypatch.setattr(run_mod, "retry_pipeline", fake_retry)
    assert cli.main(["retry", "JOB", "--stage", "CODER"]) == 0
    assert seen["stage"] == "CODER"


def test_cli_reap_defaults_to_dry_run(monkeypatch, capsys):
    from orchestrator import reaper

    seen = {}

    def fake_reap(**kwargs):
        seen.update(kwargs)
        return [{"job": "JOB-OLD", "action": "would-reap"}]

    monkeypatch.setattr(reaper, "reap", fake_reap)

    assert cli.main(["reap", "--ttl", "10"]) == 0
    assert seen == {"dry_run": True, "ttl_s": 10}
    assert "would-reap" in capsys.readouterr().out
