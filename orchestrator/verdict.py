"""Parse + validate qwen stage output into a machine-readable verdict.

qwen output contract (confirmed empirically against qwen-code 0.17.1 + the
zen `big-pickle` model, with `-o json --json-schema @schemas/verdict.json`):

  stdout = a JSON array of event objects.
  The last object with type == "result" is the stage outcome:
    success: {"type":"result","is_error":false,
              "structured_result": {...},          # already-parsed object
              "result": "<json string>"}           # same, as a string
    error:   {"type":"result","is_error":true,
              "error": {"message": "..."}}          # e.g. schema not satisfied
"""
import hashlib
import json

import jsonschema

from . import config, graph

_SCHEMA = json.loads((config.SCHEMAS_DIR / "verdict.json").read_text(encoding="utf-8"))


def last_result_event(stdout: str):
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        events = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(events, list):
        return None
    result = None
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "result":
            result = ev
    return result


def extract_structured(stdout: str):
    """Return (obj, error_message). Exactly one is non-None."""
    ev = last_result_event(stdout)
    if ev is None:
        return None, "no result event in stage output"
    if ev.get("is_error"):
        msg = (ev.get("error") or {}).get("message") or ev.get("subtype") or "stage error"
        return None, msg
    obj = ev.get("structured_result")
    if obj is None:
        raw = ev.get("result")
        if isinstance(raw, str):
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                return None, "result field is not valid JSON"
    if obj is None:
        return None, "no structured_result in result event"
    return obj, None


def validate_verdict(obj: dict) -> dict:
    """Raise jsonschema.ValidationError if obj is not a valid verdict."""
    jsonschema.validate(obj, _SCHEMA)
    return obj


def assistant_text(stdout: str) -> str:
    """Concatenate all assistant text blocks from the qwen event stream."""
    try:
        events = json.loads((stdout or "").strip())
    except json.JSONDecodeError:
        return ""
    parts = []
    for ev in events if isinstance(events, list) else []:
        if ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
    return "\n".join(parts)


def _iter_json_objects(text: str):
    """Yield top-level {...} substrings via brace matching (ignores nesting)."""
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield text[start:i + 1]
                start = None


def salvage_verdict(stdout: str):
    """Last-ditch: find a SCHEMA-VALID verdict JSON in the assistant text.

    Used when --json-schema did not yield a structured result (weak models often
    emit the JSON as plain text instead of calling structured_output). Returns a
    validated verdict dict or None — never raw/garbage JSON.
    """
    best = None
    for candidate in _iter_json_objects(assistant_text(stdout)):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        try:
            validate_verdict(obj)
        except jsonschema.ValidationError:
            continue
        best = obj  # keep the last valid one
    return best


def blocking_issues(verdict: dict) -> list:
    return [i for i in verdict.get("issues", []) if i.get("class") in graph.BLOCKING_CLASSES]


def is_pass(verdict: dict) -> bool:
    return not blocking_issues(verdict)


def verdict_signature(verdict: dict, diff_text: str = "") -> str:
    """Stable fingerprint of (blocking complaint + diff) for the no-progress
    detector. Two returns with the same complaint on the same diff collide."""
    parts = sorted(
        f'{i.get("class")}:{(i.get("summary") or "").strip().lower()}'
        for i in blocking_issues(verdict)
    )
    h = hashlib.sha256()
    h.update("\n".join(parts).encode("utf-8"))
    h.update(b"\x00")
    h.update(hashlib.sha256((diff_text or "").encode("utf-8")).digest())
    return h.hexdigest()[:16]
