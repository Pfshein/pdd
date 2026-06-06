"""Spawn qwen one-shot stages with a double timeout and tree-kill.

Two guards (see plan §4):
  inner: qwen --max-wall-time / --max-tool-calls (exit 55, graceful)
  outer: Popen + wait(timeout); on TimeoutExpired -> kill_tree (mandatory,
         because a process can hang before its own limits fire)
"""
import os
import shutil
import subprocess
import sys

from . import config
from .killtree import popen_kwargs, kill_tree

QWEN_BIN = shutil.which("qwen") or "qwen"
QWEN_EXIT_LIMIT = 55  # qwen aborts with 55 when wall-time / tool-calls exceeded


def classify_limit(result: dict) -> str | None:
    """Disambiguate qwen's exit-55 budget abort (FatalBudgetExceededError).

    Both limits share exit 55; the reason is only in the error text (on stderr):
      tool-calls -> "...tool-call budget of N exceeded (--max-tool-calls)..."  -> "stuck"
      wall-time  -> "...wall-clock budget of Ns exceeded (--max-wall-time)."   -> "slow"
    Returns "tool_calls" | "wall_time" | "unknown" | None (not a limit abort).
    """
    if result.get("exit_code") != QWEN_EXIT_LIMIT:
        return None
    blob = (result.get("stderr") or "") + (result.get("stdout") or "")
    if "max-tool-calls" in blob or "tool-call budget" in blob:
        return "tool_calls"
    if "max-wall-time" in blob or "wall-clock budget" in blob:
        return "wall_time"
    return "unknown"


def stage_env(extra: dict | None = None) -> dict:
    """Full child environment = inherited env + OPENAI_* creds (+ overrides).

    The API key is delivered ONLY here (env), never on argv — argv is visible in
    ps / /proc/<pid>/cmdline and tends to leak into logs. We do NOT suppress the
    yolo "no sandbox" warning: per the sandbox invariant (#1) an executing stage
    refuses to start without isolation, so there is nothing to silence.
    """
    env = dict(os.environ)
    env.update({k: v for k, v in config.model_env().items() if v})
    if extra:
        env.update(extra)
    return env


def run_process(argv, cwd=None, env=None, timeout_s=None, stdin_input=None) -> dict:
    """Run argv to completion or kill its tree on timeout. Returns a dict."""
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdin=subprocess.PIPE if stdin_input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs(),
    )
    try:
        out, err = proc.communicate(input=stdin_input, timeout=timeout_s)
        return {"exit_code": proc.returncode, "stdout": out, "stderr": err, "timed_out": False}
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return {"exit_code": None, "stdout": out, "stderr": err, "timed_out": True}


def build_qwen_argv(
    *,
    model: str,
    base_url: str,
    approval: str = "yolo",
    json_schema: str | None = None,
    output_format: str | None = None,
    json_file=None,
    wall_time_s: int | None = None,
    max_tool_calls: int | None = None,
    extra: list | None = None,
    qwen_bin: str | None = None,
) -> list:
    """Assemble the qwen CLI argv for a stage (plan §3).

    The prompt is NOT a positional arg — it is fed via stdin. Array-type qwen
    options (e.g. --exclude-tools) otherwise greedily swallow a trailing
    positional prompt. stdin decouples the prompt from arg parsing entirely.

    The API key is intentionally absent here — it goes via env only (see
    stage_env). model/base_url are not secrets and stay as flags.
    """
    argv = [
        qwen_bin or QWEN_BIN,  # "qwen" inside the sandbox image; host path otherwise
        "--bare",
        "--approval-mode", approval,
        "-m", model,
        "--openai-base-url", base_url,
    ]
    if wall_time_s:
        argv += ["--max-wall-time", str(wall_time_s)]
    if max_tool_calls is not None:
        argv += ["--max-tool-calls", str(max_tool_calls)]
    if json_schema:
        argv += ["--json-schema", json_schema]
    if output_format:
        argv += ["-o", output_format]
    if json_file:
        argv += ["--json-file", str(json_file)]
    if extra:
        argv += list(extra)
    return argv


def run_qwen_stage(
    prompt: str,
    *,
    cwd=None,
    isolate: bool = False,
    approval: str = "yolo",
    json_schema: str | None = None,
    output_format: str | None = None,
    json_file=None,
    wall_time_s: int | None = None,
    max_tool_calls: int | None = None,
    extra: list | None = None,
    job: str | None = None,
    stage: str | None = None,
) -> dict:
    """High-level: build argv from config creds and run with the outer watchdog.

    approval defaults to "yolo": the zen endpoint has no auto-mode classifier,
    so --approval-mode auto blocks every tool call. yolo auto-approves; the real
    safety boundary is the sandbox (`isolate=True`), not yolo.

    isolate=True (executing stages: coder/tester): run qwen INSIDE the sandbox
    container. Fail-closed via sandbox.ensure_ready() — no Docker and no override
    -> SandboxUnavailable, the stage does not start.
    """
    from . import sandbox  # lazy: avoid import cycle

    mode = sandbox.ensure_ready() if isolate else "host"
    creds = config.model_env()
    base_wall = wall_time_s if wall_time_s is not None else config.STAGE_WALL_TIME_S
    tools = max_tool_calls if max_tool_calls is not None else config.STAGE_MAX_TOOL_CALLS

    def _execute(wall: int) -> dict:
        argv = build_qwen_argv(
            model=creds["OPENAI_MODEL"],
            base_url=creds["OPENAI_BASE_URL"],
            approval=approval,
            json_schema=json_schema,
            output_format=output_format,
            json_file=json_file,
            wall_time_s=wall,
            max_tool_calls=tools,
            extra=extra,
            qwen_bin="qwen" if mode == "docker" else None,
        )
        outer_timeout = wall + config.STAGE_KILL_MARGIN_S
        if mode == "docker":
            res = sandbox.run_in_sandbox(
                argv, worktree=cwd, stdin=prompt, timeout=outer_timeout,
                job=job, stage=stage,
            )
        else:
            if mode == "UNSANDBOXED":
                sys.stderr.write(
                    "!! PDD SECURITY: executing stage running WITHOUT sandbox "
                    "(PDD_ALLOW_UNSANDBOXED). The agent has full host privileges.\n"
                )
            res = run_process(
                argv, cwd=cwd, env=stage_env(), timeout_s=outer_timeout, stdin_input=prompt
            )
        res["argv"] = argv
        res["sandbox"] = mode
        return res

    result = _execute(base_wall)
    limit = classify_limit(result)
    # wall-time exceeded == "slow", not "stuck": one retry with a bigger budget.
    if limit == "wall_time":
        bigger = min(int(base_wall * config.STAGE_WALL_RETRY_FACTOR), config.STAGE_WALL_MAX_S)
        if bigger > base_wall:
            result = _execute(bigger)
            limit = classify_limit(result)
    result["limit"] = limit
    return result
