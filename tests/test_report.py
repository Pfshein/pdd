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


def test_events_section(tmp_path, monkeypatch):
    _seed("JOB-EVENTS", monkeypatch, tmp_path)
    artifacts.write_text(
        "JOB-EVENTS",
        "events.jsonl",
        '{"job":"JOB-EVENTS","event":"stage_end","stage":"TEST_RUN","duration_ms":42,"reason":"tests green"}\n',
    )

    md = report.build_report("JOB-EVENTS")

    assert "## Events" in md
    assert "stage_end stage=TEST_RUN 42ms reason=tests green" in md


def test_stage_error_section_includes_diagnostic_artifact(tmp_path, monkeypatch):
    _seed("JOB-STAGE-ERR", monkeypatch, tmp_path)
    artifacts.write_json("JOB-STAGE-ERR", "stage_error.json", {
        "stage": "CODER",
        "error": "qwen budget exceeded (exit 55; qwen did not report which limit)",
        "exit_code": 55,
        "timed_out": False,
        "limit": "unknown",
        "stderr_tail": "FatalBudgetExceededError",
        "stdout_tail": "",
    })
    state_mod.record_attempt(
        "JOB-STAGE-ERR", "CODER", "CODER failed", None,
        status="error", error="qwen budget exceeded (exit 55; qwen did not report which limit)",
    )

    md = report.build_report("JOB-STAGE-ERR")

    assert "## Stage error" in md
    assert "stage: **CODER**" in md
    assert "exit: 55" in md
    assert "FatalBudgetExceededError" in md
    assert "error: qwen budget exceeded" in md


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


def test_report_shows_terminal_reason_near_outcome(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    st = state_mod.new_state("JOB-TR")
    st["node"] = "NEEDS_HUMAN"
    st["terminal_reason"] = "no_progress"
    state_mod.save_state(st)

    md = report.build_report("JOB-TR")

    assert "**Stop reason:** no_progress" in md
    # near the outcome: appears before the Timeline section
    assert md.index("Stop reason") < md.index("## Timeline")


def test_report_shows_cost_when_rates_present(tmp_path, monkeypatch):
    from orchestrator import usage
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "MODEL_INPUT_PRICE_PER_1M", 1.0)
    monkeypatch.setattr(config, "MODEL_OUTPUT_PRICE_PER_1M", 3.0)
    st = state_mod.new_state("JOB-COST"); st["node"] = "DONE"; state_mod.save_state(st)
    usage.record("JOB-COST", "CODER", prompt="x" * 4_000_000, result={"stdout": ""})

    md = report.build_report("JOB-COST")

    assert "## Usage" in md
    assert "estimated cost: $" in md
    assert "(estimate)" in md


def test_report_omits_cost_when_no_rates(tmp_path, monkeypatch):
    from orchestrator import usage
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "MODEL_INPUT_PRICE_PER_1M", None)
    monkeypatch.setattr(config, "MODEL_OUTPUT_PRICE_PER_1M", None)
    st = state_mod.new_state("JOB-NOCOST"); st["node"] = "DONE"; state_mod.save_state(st)
    usage.record("JOB-NOCOST", "CODER", prompt="x" * 40, result={"stdout": ""})

    md = report.build_report("JOB-NOCOST")

    assert "## Usage" in md            # tokens still shown
    assert "estimated cost" not in md  # but no bogus $0.00
    assert "$0.00" not in md


def test_report_has_loop_budget_section(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    st = state_mod.new_state("JOB-BUD")
    st["node"] = "DONE"
    st["budgets"]["CODER"]["used"] = 2
    state_mod.save_state(st)

    md = report.build_report("JOB-BUD")

    assert "## Loop budget" in md
    assert "CODER: 2/4 (used/max)" in md
    assert md.isascii()  # ASCII-safe


def test_write_handoff_creates_concise_artifact(tmp_path, monkeypatch):
    from orchestrator import run as run_mod, graph as g
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.job_dir("JOB-HO")
    artifacts.write_json("JOB-HO", "verdict.json", {"issues": [{"class": "logic_bug", "summary": "off by one"}]})
    artifacts.write_json("JOB-HO", "test_result.json", {"status": "red", "log_tail": "AssertionError: 4 != 5"})
    final = {"node": "NEEDS_HUMAN", "global_steps": 12, "global_step_cap": 30,
             "terminal_reason": g.REASON_NO_PROGRESS}

    run_mod._write_handoff("JOB-HO", final)

    ho = artifacts.read_text("JOB-HO", "handoff.md")
    assert "Stop reason: no_progress" in ho
    assert "off by one" in ho
    assert "AssertionError: 4 != 5" in ho          # red test tail
    assert "## Next action" in ho and "underspecified" in ho


def test_report_prefers_handoff_for_needs_human(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    st = state_mod.new_state("JOB-RH"); st["node"] = "NEEDS_HUMAN"; state_mod.save_state(st)
    artifacts.write_text("JOB-RH", "escalation.md", "detailed escalation")
    artifacts.write_text("JOB-RH", "handoff.md", "concise handoff")

    md = report.build_report("JOB-RH")

    assert "## Handoff" in md and "concise handoff" in md
    assert "## Escalation" not in md
