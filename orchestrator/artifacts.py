"""Job artifacts on disk + assembly of the per-stage input prompt.

State between stages flows ONLY through these files. Each stage is handed just
the artifacts it needs plus a compressed "what we already tried" log, so the
agent's context stays thin.
"""
import json
from pathlib import Path

from . import config, state as state_mod

PROMPTS_DIR = config.PROMPTS_DIR


def path(job: str, name: str) -> Path:
    return state_mod.job_dir(job) / name


def write_text(job: str, name: str, text: str) -> None:
    path(job, name).write_text(text, encoding="utf-8")


def read_text(job: str, name: str, default: str = "") -> str:
    p = path(job, name)
    return p.read_text(encoding="utf-8") if p.exists() else default


def write_json(job: str, name: str, obj) -> None:
    path(job, name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(job: str, name: str, default=None):
    p = path(job, name)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def read_user_json(path: str | Path):
    """Read user-supplied JSON, accepting UTF-8 files with or without a BOM."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def exists(job: str, name: str) -> bool:
    return path(job, name).exists()


def load_role_prompt(role: str) -> str:
    return (PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8")


def compressed_attempts(job: str, limit: int = 12) -> str:
    """The 'what we already tried' log — one terse line per prior stage run."""
    rows = state_mod.read_attempts(job)[-limit:]
    return "\n".join(f"- [{r.get('stage')}] {r.get('note')}" for r in rows)


def build_prompt(role: str, sections: dict) -> str:
    """role system prompt + only the non-empty artifact sections."""
    parts = [load_role_prompt(role).strip(), ""]
    for title, content in sections.items():
        content = (content or "").strip()
        if content:
            parts.append(f"## {title}\n{content}\n")
    return "\n".join(parts)
