"""Issue provider boundary.

A pure dispatch that turns a provider's issue payload into PDD task files. Every
provider returns the same shape as jira.normalize_issue: (task_markdown, task_meta).

No network here: a provider's fetch/MCP/export layer supplies the payload dict.
Adding a provider = one normalizer + one dispatch branch, nothing else changes.
"""
import json
from pathlib import Path

from . import jira, triage

SUPPORTED_PROVIDERS = ("jira", "github")


def normalize_issue_payload(provider: str, payload: dict) -> tuple[str, dict]:
    """Return (task_markdown, task_meta) for a provider's issue payload.

    provider="jira" delegates to jira.normalize_issue (behavior unchanged);
    provider="github" parses a GitHub issue JSON. Unknown providers raise.
    """
    if provider == "jira":
        return jira.normalize_issue(payload)
    if provider == "github":
        return normalize_github_issue(payload)
    raise ValueError(
        f"unsupported issue provider {provider!r}; supported: {', '.join(SUPPORTED_PROVIDERS)}"
    )


def _github_labels(payload: dict) -> list:
    """GitHub labels are either strings or objects with a 'name'."""
    out = []
    for label in payload.get("labels", []) or []:
        if isinstance(label, dict):
            name = label.get("name")
            if name:
                out.append(str(name))
        elif label:
            out.append(str(label))
    return out


def _github_issue_type(labels: list) -> str:
    """Pick an issue_type from labels for triage; default to a small 'task'."""
    lowered = {x.strip().lower() for x in labels}
    for known in (*triage.COMPLEX_ISSUE_TYPES, *triage.SIMPLE_ISSUE_TYPES):
        if known in lowered:
            return known
    if "enhancement" in lowered:  # common GitHub label -> treat like a feature
        return "feature"
    return "task"


def normalize_github_issue(payload: dict) -> tuple[str, dict]:
    """Return (task_markdown, task_meta) for a GitHub issue JSON (no API call)."""
    number = payload.get("number")
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "")
    labels = _github_labels(payload)
    issue_type = _github_issue_type(labels)

    key = f"GH-{number}" if number is not None else "GH"
    heading = f"{key}: {title}".strip(": ") or "GitHub issue"
    task_md = "\n".join([
        f"# {heading}",
        "",
        f"- GitHub issue: `#{number if number is not None else '-'}`",
        f"- Type: `{issue_type}`",
        f"- Labels: {', '.join(labels) if labels else '-'}",
        "",
        "## Description",
        body.strip() or "_No description provided._",
        "",
    ])
    meta = {
        "issue_type": issue_type,
        "labels": labels,
        "description_chars": len(body),
        "estimate": None,
        "summary": title,
    }
    if number is not None:
        meta["github_number"] = number
    return task_md, meta


def write_intake(provider: str, payload: dict, out_dir) -> dict:
    """Normalize an issue and write task.md + task_meta.json. Returns their paths."""
    task_md, meta = normalize_issue_payload(provider, payload)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    task_path = out / "task.md"
    meta_path = out / "task_meta.json"
    task_path.write_text(task_md, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"task": str(task_path), "meta": str(meta_path), "task_meta": meta}
