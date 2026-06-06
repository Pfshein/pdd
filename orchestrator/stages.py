"""Real run_node dispatcher: maps each graph node to an action.

qwen stages: INTAKE(meta) / ARCHITECT / reviewers emit structured output via
--json-schema; CODER / TESTER edit files in the worktree (no schema, tools on).
Deterministic steps: TRIAGE and TEST_RUN are plain code.

make_run_node(ctx) returns the run_node(node, state) callable the driver expects.
ctx = {repo, base_sha, task_md, task_meta, test_command}
"""
import json

from . import artifacts, config, runner, sandbox, testrun, triage, verdict, worktree
from .graph import (
    INTAKE, TRIAGE, ARCHITECT, CODER, CODE_REVIEW, TESTER, TEST_RUN, FINAL_REVIEW,
)

# Reviewer/architect must not roam the filesystem; cut every file/shell/web tool.
EXCLUDE_EXPLORE = (
    "run_shell_command,glob,read_file,read_many_files,edit,write_file,"
    "notebook_edit,web_fetch,web_search"
)


def _wall(node: str) -> int:
    return config.STAGE_WALL_TIME.get(node, config.STAGE_WALL_TIME_S)


def _last_assistant_text(stdout: str) -> str:
    ev = None
    try:
        events = json.loads(stdout or "")
    except json.JSONDecodeError:
        return ""
    for e in events if isinstance(events, list) else []:
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    ev = block["text"]
    return ev or ""


def _process_failed(res: dict) -> bool:
    return bool(res.get("timed_out")) or res.get("exit_code") not in (0, None)


def _stage_error(res: dict, fallback: str = "stage failed") -> str:
    if res.get("timed_out"):
        return "stage timed out (outer watchdog)"
    if res.get("limit") == "tool_calls":
        return "tool-call budget exceeded (agent stuck in a tool loop)"
    if res.get("limit") == "wall_time":
        return "wall-time budget exceeded even after a longer retry (too slow)"
    stderr = (res.get("stderr") or "").strip()
    if stderr:
        return stderr[-1000:]
    return fallback


def _run_structured(role: str, sections: dict, schema_file: str, job: str, node: str):
    prompt = artifacts.build_prompt(role, sections)
    res = runner.run_qwen_stage(
        prompt,
        cwd=worktree.worktree_path(job),
        json_schema=f"@{config.SCHEMAS_DIR / schema_file}",
        output_format="json",
        wall_time_s=_wall(node),
        max_tool_calls=6,
        extra=["--exclude-tools", EXCLUDE_EXPLORE],
    )
    if _process_failed(res):
        return None, _stage_error(res), res
    obj, err = verdict.extract_structured(res["stdout"])
    return obj, err, res


def _run_freeform(role: str, sections: dict, job: str, node: str):
    """Free-form text stage (no --json-schema): the model reasons in prose.

    Forcing a schema on a creative stage makes weak models emit plain text and
    abort (exit 1, burning budget). The architect's output is advisory prose, so
    we let it think and capture the assistant text.
    """
    prompt = artifacts.build_prompt(role, sections)
    return runner.run_qwen_stage(
        prompt,
        cwd=worktree.worktree_path(job),
        output_format="json",
        wall_time_s=_wall(node),
        max_tool_calls=6,
        extra=["--exclude-tools", EXCLUDE_EXPLORE],
    )


def _run_editor(role: str, sections: dict, job: str, node: str):
    # Executing stage: edits files + runs shell under --yolo -> MUST be isolated.
    prompt = artifacts.build_prompt(role, sections)
    return runner.run_qwen_stage(
        prompt,
        cwd=worktree.worktree_path(job),
        isolate=True,
        output_format="json",
        wall_time_s=_wall(node),
        max_tool_calls=config.STAGE_MAX_TOOL_CALLS,
    )


# --- Individual stages ----------------------------------------------------
def _intake(job: str, ctx: dict) -> dict:
    # Fixture mode: task content supplied directly. (Jira-MCP mode: see intake.md.)
    if ctx.get("task_md") is None:
        return {"status": "error"}
    artifacts.write_text(job, "task.md", ctx["task_md"])
    artifacts.write_json(job, "task_meta.json", ctx["task_meta"])
    return {"status": "ok"}


def _triage(job: str, ctx: dict) -> dict:
    meta = artifacts.read_json(job, "task_meta.json", {}) or {}
    return {"triage": triage.triage_label(meta)}


def _architect(job: str, ctx: dict) -> dict:
    sections = {
        "Task": artifacts.read_text(job, "task.md"),
        "Reviewer verdict to address": artifacts.read_text(job, "verdict.json"),
        "What we already tried": artifacts.compressed_attempts(job),
    }
    res = _run_freeform("architect", sections, job, ARCHITECT)
    if _process_failed(res):
        err = _stage_error(res)
        artifacts.write_text(job, "plan.md", f"(architect failed: {err})")
        return {"status": "error", "error": err}
    plan = _last_assistant_text(res["stdout"]).strip()
    # The plan is advisory; an empty one is not fatal (the coder can plan itself).
    artifacts.write_text(job, "plan.md", plan or "(architect produced no plan text)")
    return {"status": "ok"}


def _coder(job: str, ctx: dict) -> dict:
    test_result = artifacts.read_json(job, "test_result.json", {}) or {}
    sections = {
        "Task": artifacts.read_text(job, "task.md"),
        "Plan": artifacts.read_text(job, "plan.md"),
        "Reviewer verdict - fix exactly this": artifacts.read_text(job, "verdict.json"),
        "Failing tests - make these pass": test_result.get("log_tail", "")
        if test_result.get("status") == "red" else "",
        "What we already tried": artifacts.compressed_attempts(job),
    }
    res = _run_editor("coder", sections, job, CODER)
    if res.get("sandbox") == "UNSANDBOXED":
        sandbox.record_unsandboxed_override(job, CODER)
    if _process_failed(res):
        return {"status": "error", "error": _stage_error(res), "signature": None}
    artifacts.write_text(job, "changes.md", _last_assistant_text(res["stdout"]))
    return {"status": "ok"}


def _review(job: str, ctx: dict, node: str) -> dict:
    diff_text = worktree.diff(job, ctx["base_sha"])
    artifacts.write_text(job, "diff.patch", diff_text)
    sections = {
        "Task": artifacts.read_text(job, "task.md"),
        "Plan": artifacts.read_text(job, "plan.md"),
        "Diff to review": diff_text or "(empty diff — no changes were made)",
    }
    obj, err, res = _run_structured("reviewer", sections, "verdict.json", job, node)
    if obj is None:  # one retry, then fail closed: a broken gate is not a pass.
        obj, err, res = _run_structured("reviewer", sections, "verdict.json", job, node)
    if obj is None and res is not None:
        # Soft fallback: the model may have emitted a valid verdict as plain text
        # instead of calling structured_output. salvage_verdict validates it.
        obj = verdict.salvage_verdict(res.get("stdout", ""))
        if obj is not None:
            err = None
    if obj is None:
        artifacts.write_json(job, "verdict.json", {"issues": [], "_stage_error": err})
        return {"status": "error", "error": err, "signature": None}
    verdict.validate_verdict(obj)
    artifacts.write_json(job, "verdict.json", obj)
    return {
        "status": "ok",
        "verdict": obj,
        "signature": verdict.verdict_signature(obj, diff_text),
    }


def _tester(job: str, ctx: dict) -> dict:
    sections = {
        "Task": artifacts.read_text(job, "task.md"),
        "Plan": artifacts.read_text(job, "plan.md"),
        "Diff so far": artifacts.read_text(job, "diff.patch"),
    }
    res = _run_editor("tester", sections, job, TESTER)
    if res.get("sandbox") == "UNSANDBOXED":
        sandbox.record_unsandboxed_override(job, TESTER)
    if _process_failed(res):
        return {"status": "error", "error": _stage_error(res), "signature": None}
    return {"status": "ok"}


def _test_run(job: str, ctx: dict) -> dict:
    result = testrun.run_tests(job, worktree.worktree_path(job), ctx.get("test_command"))
    return {"test": result, "signature": testrun.failure_signature(result)}


def make_run_node(ctx: dict):
    def run_node(node: str, state: dict) -> dict:
        job = state["job"]
        if node == INTAKE:
            return _intake(job, ctx)
        if node == TRIAGE:
            return _triage(job, ctx)
        if node == ARCHITECT:
            return _architect(job, ctx)
        if node == CODER:
            return _coder(job, ctx)
        if node in (CODE_REVIEW, FINAL_REVIEW):
            return _review(job, ctx, node)
        if node == TESTER:
            return _tester(job, ctx)
        if node == TEST_RUN:
            return _test_run(job, ctx)
        raise ValueError(f"no stage handler for node {node!r}")

    return run_node
