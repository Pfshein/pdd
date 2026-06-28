"""Issue provider boundary.

A pure dispatch that turns a provider's issue payload into PDD task files. Every
provider returns the same shape as jira.normalize_issue: (task_markdown, task_meta).

No network here: a provider's fetch/MCP/export layer supplies the payload dict.
Adding a provider = one branch + its normalizer, nothing else changes.
"""
from . import jira

SUPPORTED_PROVIDERS = ("jira",)


def normalize_issue_payload(provider: str, payload: dict) -> tuple[str, dict]:
    """Return (task_markdown, task_meta) for a provider's issue payload.

    provider="jira" delegates to jira.normalize_issue (behavior unchanged).
    Unknown providers raise a clear ValueError.
    """
    if provider == "jira":
        return jira.normalize_issue(payload)
    raise ValueError(
        f"unsupported issue provider {provider!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )
