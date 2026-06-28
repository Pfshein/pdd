"""Run-start hygiene for reused job ids."""
from orchestrator import artifacts, config, run as run_mod, state as state_mod


def test_reset_job_logs_removes_stale_per_run_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.job_dir("JOB-HYGIENE")
    for name in run_mod.PER_RUN_ARTIFACTS:
        artifacts.write_text("JOB-HYGIENE", name, "stale")
    artifacts.write_text("JOB-HYGIENE", "task.md", "keep until intake overwrites")

    run_mod._reset_job_logs("JOB-HYGIENE")

    jd = state_mod.job_dir("JOB-HYGIENE")
    assert not any((jd / name).exists() for name in run_mod.PER_RUN_ARTIFACTS)
    assert (jd / "task.md").exists()


def test_hydrate_task_context_inlines_referenced_pdd_card(tmp_path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (docs / "LOOP_ENGINEERING_PROJECT.md").write_text(
        "# Backlog\n\n"
        "### PDD-46: Static HTML Report Index\n\n"
        "Objective: generate an HTML index from existing run artifacts.\n\n"
        "Files:\n\n"
        "- new `orchestrator/dashboard.py`\n"
        "- `orchestrator/cli.py`\n\n"
        "### PDD-47: Live Local Dashboard\n\n"
        "Objective: optional local status server.\n",
        encoding="utf-8",
    )
    task = (
        "# PDD-46\n\n"
        "Read `docs/LOOP_ENGINEERING_PROJECT.md`, section "
        '"### PDD-46: Static HTML Report Index".'
    )

    hydrated = run_mod.hydrate_task_context(str(repo), task)

    assert "## Resolved referenced specification" in hydrated
    assert "Objective: generate an HTML index" in hydrated
    assert "orchestrator/dashboard.py" in hydrated
    assert "PDD-47" not in hydrated


def test_hydrate_task_context_leaves_unreferenced_task_unchanged(tmp_path):
    task = "# T\n\nDo the thing."

    assert run_mod.hydrate_task_context(str(tmp_path), task) == task

