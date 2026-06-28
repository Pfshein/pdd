"""Issue provider boundary (PDD-35)."""
import pytest

from orchestrator import issues, jira


def _jira_issue():
    return {
        "key": "DEMO-1",
        "fields": {
            "summary": "Fix the thing",
            "issuetype": {"name": "Bug"},
            "labels": ["backend"],
            "description": "It is broken.",
        },
    }


def test_jira_provider_matches_jira_normalizer():
    payload = _jira_issue()

    via_boundary = issues.normalize_issue_payload("jira", payload)
    direct = jira.normalize_issue(payload)

    assert via_boundary == direct
    task_md, meta = via_boundary
    assert "DEMO-1" in task_md
    assert meta["jira_key"] == "DEMO-1"


def test_unsupported_provider_raises_clear_error():
    with pytest.raises(ValueError) as exc:
        issues.normalize_issue_payload("gitlab", _jira_issue())
    assert "gitlab" in str(exc.value)
    assert "supported" in str(exc.value)


# --- GitHub provider (PDD-36) ---------------------------------------------
def _github_issue():
    return {
        "number": 42,
        "title": "Fix the bug",
        "body": "It crashes on start.",
        "labels": [{"name": "bug"}, {"name": "backend"}],
        "state": "open",
    }


def test_github_converts_number_title_body_labels():
    task_md, meta = issues.normalize_issue_payload("github", _github_issue())

    assert "GH-42: Fix the bug" in task_md
    assert "`#42`" in task_md
    assert "It crashes on start." in task_md
    assert meta["github_number"] == 42
    assert meta["summary"] == "Fix the bug"
    assert meta["labels"] == ["bug", "backend"]
    assert meta["issue_type"] == "bug"
    assert meta["description_chars"] == len("It crashes on start.")


def test_github_labels_accept_plain_strings():
    payload = {"number": 7, "title": "t", "body": "", "labels": ["refactor"]}
    _md, meta = issues.normalize_issue_payload("github", payload)
    assert meta["labels"] == ["refactor"]


def test_github_missing_body_is_safe():
    _md, meta = issues.normalize_issue_payload("github", {"number": 1, "title": "t"})
    assert meta["description_chars"] == 0


def test_write_intake_github_writes_files(tmp_path):
    res = issues.write_intake("github", _github_issue(), tmp_path / "GH-42")
    import json as _json
    assert (tmp_path / "GH-42" / "task.md").exists()
    meta = _json.loads((tmp_path / "GH-42" / "task_meta.json").read_text(encoding="utf-8"))
    assert meta["github_number"] == 42
