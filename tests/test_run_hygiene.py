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

