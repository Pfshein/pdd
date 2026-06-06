"""Spawn qwen one-shot stages with a double timeout and tree-kill.

Two guards (see plan §4):
  inner: qwen --max-wall-time / --max-tool-calls (exit 55, graceful)
  outer: Popen + wait(timeout); on TimeoutExpired -> kill_tree (mandatory,
         because a process can hang before its own limits fire)
"""
import os
import shutil
import subprocess

from . import config
from .killtree import popen_kwargs, kill_tree

QWEN_BIN = shutil.which("qwen") or "qwen"
QWEN_EXIT_LIMIT = 55  # qwen aborts with 55 when wall-time / tool-calls exceeded


def stage_env(extra: dict | None = None) -> dict:
    """Full child environment = inherited env + OPENAI_* creds (+ overrides)."""
    env = dict(os.environ)
    env.update({k: v for k, v in config.model_env().items() if v})
    env.setdefault("QWEN_CODE_SUPPRESS_YOLO_WARNING", "1")
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
    api_key: str,
    approval: str = "yolo",
    json_schema: str | None = None,
    output_format: str | None = None,
    json_file=None,
    wall_time_s: int | None = None,
    max_tool_calls: int | None = None,
    extra: list | None = None,
) -> list:
    """Assemble the qwen CLI argv for a stage (plan §3).

    The prompt is NOT a positional arg — it is fed via stdin. Array-type qwen
    options (e.g. --exclude-tools) otherwise greedily swallow a trailing
    positional prompt. stdin decouples the prompt from arg parsing entirely.
    """
    argv = [
        QWEN_BIN,
        "--bare",
        "--approval-mode", approval,
        "-m", model,
        "--openai-base-url", base_url,
        "--openai-api-key", api_key,
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
    approval: str = "yolo",
    json_schema: str | None = None,
    output_format: str | None = None,
    json_file=None,
    wall_time_s: int | None = None,
    max_tool_calls: int | None = None,
    extra: list | None = None,
) -> dict:
    """High-level: build argv from config creds and run with the outer watchdog.

    approval defaults to "yolo": the zen endpoint has no auto-mode classifier,
    so --approval-mode auto blocks every tool call ("Classifier stage 1
    unavailable"). yolo auto-approves; safety comes from the sandbox + worktree.
    """
    creds = config.model_env()
    wall = wall_time_s if wall_time_s is not None else config.STAGE_WALL_TIME_S
    tools = max_tool_calls if max_tool_calls is not None else config.STAGE_MAX_TOOL_CALLS
    argv = build_qwen_argv(
        model=creds["OPENAI_MODEL"],
        base_url=creds["OPENAI_BASE_URL"],
        api_key=creds["OPENAI_API_KEY"],
        approval=approval,
        json_schema=json_schema,
        output_format=output_format,
        json_file=json_file,
        wall_time_s=wall,
        max_tool_calls=tools,
        extra=extra,
    )
    outer_timeout = wall + config.STAGE_KILL_MARGIN_S
    result = run_process(
        argv, cwd=cwd, env=stage_env(), timeout_s=outer_timeout, stdin_input=prompt
    )
    result["argv"] = argv
    return result
