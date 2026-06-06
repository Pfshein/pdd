"""One human-readable Markdown report per job, assembled from its artifacts.

Pulls together state, the transition timeline, stage attempts, the last verdict,
test result, a diff summary, publish info and — prominently — any security
warning. Read-only over the job dir; no model calls.
"""
import json

from . import artifacts, state as state_mod


def _read_transitions(job: str) -> list:
    path = state_mod.job_dir(job) / "transitions.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_jsonl_artifact(job: str, name: str) -> list:
    path = state_mod.job_dir(job) / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _diff_stats(diff_text: str):
    files = added = removed = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return files, added, removed


def build_report(job: str) -> str:
    job = state_mod.validate_job_id(job)
    meta = artifacts.read_json(job, "job_meta.json", {}) or {}
    st = artifacts.read_json(job, "state.json", {}) or {}
    verdict = artifacts.read_json(job, "verdict.json", {}) or {}
    test = artifacts.read_json(job, "test_result.json", {}) or {}
    publish = artifacts.read_json(job, "publish.json", {}) or {}
    diff_text = artifacts.read_text(job, "diff.patch")
    security = artifacts.read_text(job, "SECURITY.txt").strip()
    escalation = artifacts.read_text(job, "escalation.md").strip()
    transitions = _read_transitions(job)
    attempts = state_mod.read_attempts(job)
    sandbox_audit = _read_jsonl_artifact(job, "sandbox_audit.jsonl")

    out = [
        f"# PDD report: {job}",
        "",
        f"- **Outcome:** {st.get('node', 'unknown')}",
        f"- **Steps:** {st.get('global_steps')}/{st.get('global_step_cap')}",
    ]
    if meta.get("repo"):
        out.append(f"- **Repo:** {meta['repo']}")
    if meta.get("branch"):
        out.append(f"- **Branch:** {meta['branch']}")

    # Security first and prominent — it is not just another section.
    if security:
        out += ["", "## (!) Security warnings", "```", security, "```"]

    out += ["", "## Timeline"]
    if transitions:
        out += [f"- `{t.get('from')}` -> `{t.get('to')}`: {t.get('reason')}" for t in transitions]
    else:
        out.append("_(no transitions recorded)_")

    if attempts:
        out += ["", "## Stage attempts"]
        for a in attempts:
            tags = " ".join(f"{k}={a[k]}" for k in ("status", "limit") if a.get(k))
            suffix = f"  _[{tags}]_" if tags else ""
            out.append(f"- **{a.get('stage')}**: {a.get('note')}{suffix}")

    if sandbox_audit:
        out += ["", "## Sandbox audit"]
        for row in sandbox_audit[-10:]:
            out.append(
                f"- **{row.get('stage') or '-'}**: container `{row.get('container')}`, "
                f"network `{row.get('network')}`, seccomp `{row.get('seccomp')}`, "
                f"exit {row.get('exit_code')}, timed_out={row.get('timed_out')}"
            )

    out += ["", "## Last verdict"]
    issues = verdict.get("issues", [])
    if issues:
        for i in issues:
            loc = f" ({i['location']})" if i.get("location") else ""
            out.append(f"- **{i.get('class')}**: {i.get('summary')}{loc}")
    else:
        out.append("_no blocking issues_")

    if test:
        out += [
            "", "## Tests",
            f"- status: **{test.get('status')}** (exit {test.get('exit_code')})",
            f"- command: `{test.get('command')}`",
        ]

    if diff_text.strip():
        files, added, removed = _diff_stats(diff_text)
        out += ["", "## Diff summary", f"- {files} file(s), +{added} / -{removed} lines"]

    if publish:
        out += [
            "", "## Publish",
            f"- branch: `{publish.get('branch')}`",
            f"- commit: `{publish.get('committed')}`",
            f"- pushed: {publish.get('pushed')}",
            f"- PR: {publish.get('pr_url') or '-'}",
        ]

    # Escalation only matters for a needs-human outcome (a stale file from an
    # earlier run must not bleed into a later DONE report).
    if escalation and st.get("node") == "NEEDS_HUMAN":
        out += ["", "## Escalation", escalation]

    return "\n".join(out) + "\n"
