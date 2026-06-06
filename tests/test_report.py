"""report: one human-readable Markdown summary assembled from job artifacts."""
from orchestrator import artifacts, cli, config, report, state as state_mod


def _seed(job, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    st = state_mod.new_state(job)
    st["node"] = "DONE"
    st["global_steps"] = 7
    state_mod.save_state(st)
    artifacts.write_json(job, "job_meta.json", {"repo": "/r", "branch": f"pdd/{job}"})
    state_mod.record_transition(job, "INTAKE", "TRIAGE", "intake ok")
    state_mod.record_attempt(job, "CODER", "coder produced diff", None, status="ok")
    artifacts.write_json(job, "verdict.json", {"issues": [{"class": "nit", "summary": "style"}]})
    artifacts.write_json(job, "test_result.json", {"status": "green", "exit_code": 0, "command": "pytest"})
    artifacts.write_text(job, "diff.patch", "diff --git a/x b/x\n+added line\n-removed line\n")
    artifacts.write_json(job, "publish.json",
                         {"branch": f"pdd/{job}", "committed": "abc123", "pushed": True, "pr_url": None})


def test_build_report_has_sections(tmp_path, monkeypatch):
    _seed("JOB-RPT", monkeypatch, tmp_path)
    md = report.build_report("JOB-RPT")

    assert "# PDD report: JOB-RPT" in md
    assert "**Outcome:** DONE" in md
    assert "`INTAKE` -> `TRIAGE`" in md          # timeline
    assert "**CODER**" in md and "status=ok" in md  # attempts with diagnostics
    assert "**nit**" in md                      # verdict
    assert "status: **green**" in md            # tests
    assert "1 file(s), +1 / -1 lines" in md     # diff summary
    assert "## Publish" in md and "abc123" in md


def test_security_warning_is_prominent(tmp_path, monkeypatch):
    _seed("JOB-SEC", monkeypatch, tmp_path)
    artifacts.write_text("JOB-SEC", "SECURITY.txt", "CODER ran UNSANDBOXED")
    md = report.build_report("JOB-SEC")
    assert "## (!) Security warnings" in md
    assert "CODER ran UNSANDBOXED" in md


def test_sandbox_audit_section(tmp_path, monkeypatch):
    _seed("JOB-AUDIT", monkeypatch, tmp_path)
    (state_mod.job_dir("JOB-AUDIT") / "sandbox_audit.jsonl").write_text(
        '{"stage":"TEST_RUN","container":"pdd-x","network":"none","seccomp":"docker-default","exit_code":0,"timed_out":false}\n',
        encoding="utf-8",
    )

    md = report.build_report("JOB-AUDIT")

    assert "## Sandbox audit" in md
    assert "**TEST_RUN**" in md
    assert "container `pdd-x`" in md


def test_escalation_only_for_needs_human(tmp_path, monkeypatch):
    _seed("JOB-ESC", monkeypatch, tmp_path)  # node == DONE
    artifacts.write_text("JOB-ESC", "escalation.md", "stale escalation")
    assert "## Escalation" not in report.build_report("JOB-ESC")

    st = state_mod.new_state("JOB-ESC")
    st["node"] = "NEEDS_HUMAN"
    state_mod.save_state(st)
    artifacts.write_text("JOB-ESC", "escalation.md", "real escalation")
    md = report.build_report("JOB-ESC")
    assert "## Escalation" in md and "real escalation" in md


def test_cli_report_writes_artifact(tmp_path, monkeypatch, capsys):
    _seed("JOB-CLI-R", monkeypatch, tmp_path)
    assert cli.main(["report", "JOB-CLI-R"]) == 0
    assert (state_mod.job_dir("JOB-CLI-R") / "report.md").exists()
    assert "# PDD report: JOB-CLI-R" in capsys.readouterr().out


def test_cli_report_missing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    assert cli.main(["report", "NOPE"]) == 2
