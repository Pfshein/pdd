"""Jira intake normalization and CLI helpers."""
import json

from orchestrator import artifacts, cli, config, jira, state as state_mod


def test_normalize_issue_plain_description():
    issue = {
        "key": "PROJ-1",
        "fields": {
            "summary": "Fix checkout total",
            "issuetype": {"name": "Bug"},
            "labels": ["checkout", "backend"],
            "description": "Total is wrong when discounts apply.",
            "customfield_10016": 3,
        },
    }

    task_md, meta = jira.normalize_issue(issue)

    assert "# PROJ-1: Fix checkout total" in task_md
    assert "Total is wrong" in task_md
    assert meta["issue_type"] == "bug"
    assert meta["labels"] == ["checkout", "backend"]
    assert meta["description_chars"] == len("Total is wrong when discounts apply.")
    assert meta["estimate"] == 3.0
    assert meta["jira_key"] == "PROJ-1"


def test_normalize_issue_adf_description():
    issue = {
        "key": "PROJ-2",
        "fields": {
            "summary": "Add empty state",
            "issuetype": {"name": "Story"},
            "description": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Show an empty state."}]}
                ],
            },
        },
    }

    task_md, meta = jira.normalize_issue(issue)

    assert "Show an empty state." in task_md
    assert meta["issue_type"] == "story"
    assert meta["description_chars"] == len("Show an empty state.")


def test_cli_intake_jira_writes_task_files(tmp_path, capsys):
    issue_path = tmp_path / "issue.json"
    out_dir = tmp_path / "out"
    issue_path.write_text(
        json.dumps({"key": "PROJ-3", "fields": {"summary": "S", "issuetype": {"name": "Task"}}}),
        encoding="utf-8",
    )

    assert cli.main(["intake-jira", "--issue", str(issue_path), "--out", str(out_dir)]) == 0

    assert (out_dir / "task.md").exists()
    assert json.loads((out_dir / "task_meta.json").read_text(encoding="utf-8"))["issue_type"] == "task"
    assert "task_meta" in capsys.readouterr().out


def test_jira_comment_draft_uses_report_and_escalation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.job_dir("PROJ-4")
    artifacts.write_text("PROJ-4", "report.md", "# PDD report\nNeeds review")
    artifacts.write_text("PROJ-4", "escalation.md", "Stopped at NEEDS_HUMAN")

    assert cli.main(["jira-comment-draft", "PROJ-4"]) == 0

    out = capsys.readouterr().out
    assert "PDD stopped and needs human input" in out
    assert "Stopped at NEEDS_HUMAN" in out
    assert "# PDD report" in out



def test_needs_human_comment_prefers_handoff_over_report():
    comment = jira.needs_human_comment(
        "PROJ-9", report_md="FULL REPORT BODY", reason="stopped",
        handoff_md="CONCISE HANDOFF",
    )
    assert "CONCISE HANDOFF" in comment
    assert "FULL REPORT BODY" not in comment
    assert "Handoff:" in comment


def test_needs_human_comment_falls_back_to_report():
    comment = jira.needs_human_comment("PROJ-9", report_md="FULL REPORT", handoff_md="")
    assert "FULL REPORT" in comment
    assert "Report excerpt:" in comment


def test_jira_comment_draft_prefers_handoff(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
    state_mod.job_dir("PROJ-H")
    artifacts.write_text("PROJ-H", "report.md", "# PDD report\nlong details")
    artifacts.write_text("PROJ-H", "handoff.md", "# Handoff\nNext action: re-run")

    assert cli.main(["jira-comment-draft", "PROJ-H"]) == 0
    out = capsys.readouterr().out
    assert "Next action: re-run" in out
    assert "long details" not in out
