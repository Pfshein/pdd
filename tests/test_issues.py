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
        issues.normalize_issue_payload("github", _jira_issue())
    assert "github" in str(exc.value)
    assert "supported" in str(exc.value)
