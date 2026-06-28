"""Jira intake normalization.

This module deliberately does not talk to Jira by itself. A Jira MCP/tooling
layer can fetch the issue later and pass the same issue dict here. Keeping this
pure makes intake testable and keeps credentials out of the repository.
"""
from pathlib import Path


def _field(issue: dict, name: str, default=None):
    return (issue.get("fields") or {}).get(name, issue.get(name, default))


def _name(value, default: str = "") -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or default)
    if value is None:
        return default
    return str(value)


def _description_text(value) -> str:
    """Extract readable text from plain strings or Atlassian document format."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = []

        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text" and node.get("text"):
                    parts.append(str(node["text"]))
                for child in node.get("content", []) or []:
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
        return "\n".join(parts).strip()
    return str(value)


def _estimate(fields: dict):
    for key in (
        "customfield_10016",  # common Jira Cloud story points
        "story_points",
        "estimate",
    ):
        if fields.get(key) is not None:
            try:
                return float(fields[key])
            except (TypeError, ValueError):
                return None
    return None


def normalize_issue(issue: dict) -> tuple[str, dict]:
    """Return (task_markdown, task_meta) for a Jira issue dict."""
    fields = issue.get("fields") or {}
    key = str(issue.get("key") or fields.get("key") or "").strip()
    summary = str(_field(issue, "summary", "") or "").strip()
    issue_type = _name(_field(issue, "issuetype", ""), "task").lower() or "task"
    labels = [str(x) for x in (_field(issue, "labels", []) or [])]
    description = _description_text(_field(issue, "description", ""))
    estimate = _estimate(fields)

    title = f"{key}: {summary}".strip(": ")
    task_md = "\n".join([
        f"# {title or 'Jira issue'}",
        "",
        f"- Jira key: `{key or '-'}`",
        f"- Type: `{issue_type}`",
        f"- Labels: {', '.join(labels) if labels else '-'}",
        f"- Estimate: {estimate if estimate is not None else '-'}",
        "",
        "## Description",
        description or "_No description provided._",
        "",
    ])
    meta = {
        "issue_type": issue_type,
        "labels": labels,
        "description_chars": len(description),
        "estimate": estimate,
        "summary": summary,
    }
    if key:
        meta["jira_key"] = key
    return task_md, meta


def write_intake(issue: dict, out_dir) -> dict:
    """Write task.md and task_meta.json, returning their paths and metadata."""
    import json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    task_md, meta = normalize_issue(issue)
    task_path = out / "task.md"
    meta_path = out / "task_meta.json"
    task_path.write_text(task_md, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"task": str(task_path), "meta": str(meta_path), "task_meta": meta}


def needs_human_comment(job: str, report_md: str = "", reason: str = "",
                        handoff_md: str = "") -> str:
    """Draft a Jira comment for a NEEDS_HUMAN outcome.

    Prefers the concise handoff over the full report when available.
    """
    lines = [
        f"PDD stopped and needs human input for `{job}`.",
    ]
    if reason:
        lines += ["", f"Reason: {reason}"]
    handoff = handoff_md.strip()
    if handoff:
        lines += ["", "Handoff:", "```", handoff[:3000], "```"]
    elif report_md:
        lines += ["", "Report excerpt:", "```", report_md[:3000], "```"]
    return "\n".join(lines) + "\n"

