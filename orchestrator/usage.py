"""Per-stage token usage accounting (estimate-first).

The zen endpoint does not surface token usage in its result event (see
docs/endpoint.md), so usage is estimated from the strings we already hold: the
prompt we assembled (input) and the model's generated text (output). If a future
endpoint DOES return usage, extract_usage() prefers that authoritative count and
tags the row source="qwen_event" instead of "estimate".

These are estimates for a budget guardrail, not exact billing: the tokenizer and
any hidden provider tokens (system prompt, tool schemas, retries) are unknown.

Accounting must never break a run: record() swallows every error.
"""
import json
import time

from . import config, state as state_mod

USAGE_FILE = "usage.jsonl"

# Rough chars-per-token for English/code. Good enough for a guardrail.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Heuristic token count of a known string (~chars/4)."""
    if not text:
        return 0
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


def _response_text(stdout: str) -> str:
    """Model-generated text from a qwen `-o json` event array (assistant + result)."""
    try:
        events = json.loads(stdout or "")
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(events, list):
        return ""
    parts = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
        elif e.get("type") == "result" and isinstance(e.get("result"), str):
            parts.append(e["result"])
    return "\n".join(parts)


def extract_usage(result: dict):
    """Authoritative {input,output,total}_tokens from qwen events, or None.

    Conservatively scans events (newest first) for a usage object under several
    possible field names. Absence is normal -> None.
    """
    try:
        events = json.loads((result or {}).get("stdout") or "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None
    if not isinstance(events, list):
        return None
    for e in reversed(events):
        if not isinstance(e, dict):
            continue
        usage = e.get("usage") or e.get("token_usage") or (e.get("metadata") or {}).get("usage")
        if not isinstance(usage, dict):
            continue
        inp = usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("promptTokens")
        out = usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("completionTokens")
        if inp is None and out is None:
            continue
        inp, out = int(inp or 0), int(out or 0)
        tot = usage.get("total_tokens") or usage.get("totalTokens") or (inp + out)
        return {"input_tokens": inp, "output_tokens": out, "total_tokens": int(tot)}
    return None


def record(job: str, stage: str, prompt: str, result: dict) -> dict | None:
    """Append one usage row for a stage invocation. Never raises."""
    try:
        authoritative = extract_usage(result)
        if authoritative:
            counts = {**authoritative, "source": "qwen_event"}
        else:
            inp = estimate_tokens(prompt)
            out = estimate_tokens(_response_text((result or {}).get("stdout", "")))
            counts = {"input_tokens": inp, "output_tokens": out,
                      "total_tokens": inp + out, "source": "estimate"}
        row = {"ts": time.time(), "job": job, "stage": stage, **counts}
        path = state_mod.job_dir(job) / USAGE_FILE
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception:
        return None  # usage accounting must never break a stage


def read(job: str) -> list[dict]:
    path = state_mod.job_dir(job) / USAGE_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def totals(job: str) -> dict:
    """Sum usage across all recorded stage rows for a job."""
    rows = read(job)
    inp = sum(r.get("input_tokens", 0) for r in rows)
    out = sum(r.get("output_tokens", 0) for r in rows)
    estimated = any(r.get("source") == "estimate" for r in rows)
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
            "estimated": estimated, "rows": len(rows)}


def estimate_cost(input_tokens: int, output_tokens: int,
                  input_rate=None, output_rate=None):
    """USD cost from token counts and per-1M rates. None if no rate is configured.

    Returning None (not 0.0) when rates are absent keeps the report from showing a
    bogus $0.00.
    """
    if input_rate is None and output_rate is None:
        return None
    return (input_tokens / 1_000_000) * (input_rate or 0.0) \
        + (output_tokens / 1_000_000) * (output_rate or 0.0)


def cost_summary(job: str) -> dict:
    """Token totals + estimated USD cost (using config rates) for a job."""
    t = totals(job)
    t["cost_usd"] = estimate_cost(
        t["input_tokens"], t["output_tokens"],
        config.MODEL_INPUT_PRICE_PER_1M, config.MODEL_OUTPUT_PRICE_PER_1M,
    )
    return t
