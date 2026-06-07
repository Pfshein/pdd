"""CLI entry point (PDD-20): bare `pdd` help + repo defaults to cwd."""
import os

from orchestrator import cli, config, run as run_mod


def test_bare_invocation_prints_help(capsys):
    rc = cli.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "usage: pdd" in out
    assert "run" in out and "doctor" in out


def test_run_defaults_repo_to_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "task.md").write_text("t", encoding="utf-8")
    (tmp_path / "meta.json").write_text('{"issue_type": "bug"}', encoding="utf-8")

    captured = {}

    def fake_pipeline(job, repo, **kw):
        captured["repo"] = repo
        return {"node": "DONE", "global_steps": 1, "global_step_cap": 30, "budgets": {}}

    monkeypatch.setattr(run_mod, "run_pipeline", fake_pipeline)

    # no --repo passed -> must default to cwd
    rc = cli.main(["run", "--job", "J", "--task", "task.md", "--meta", "meta.json", "--quiet"])

    assert rc == 0
    assert captured["repo"] == os.getcwd()


def test_pyproject_declares_pdd_entrypoint():
    text = (config.ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'pdd = "orchestrator.cli:main"' in text
