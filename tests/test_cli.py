"""CLI inspection commands for the user-facing job flow."""
import json

from orchestrator import cli, config, state as state_mod


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


