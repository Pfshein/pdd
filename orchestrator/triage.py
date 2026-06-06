"""Deterministic triage: decide whether a task needs an upfront ARCHITECT pass.

Code decides by thresholds over task metadata. The LLM never picks the stage.
"""

# Issue types that are "small" by default → skip the architect, go straight to coder.
SIMPLE_ISSUE_TYPES = frozenset({"bug", "task", "sub-task", "subtask"})
# Issue types that always warrant a plan.
COMPLEX_ISSUE_TYPES = frozenset({"story", "epic", "feature", "spike"})

# Labels that force a plan regardless of type/size.
COMPLEX_LABELS = frozenset({"architecture", "design", "refactor", "migration", "breaking"})

# Description-size threshold (chars) above which even a "bug" gets a plan.
DESCRIPTION_COMPLEX_CHARS = 1200
# Story-point estimate at/above which we plan.
ESTIMATE_COMPLEX_POINTS = 5


def is_complex(task_meta: dict) -> bool:
    """Return True if the task warrants an upfront ARCHITECT stage."""
    issue_type = str(task_meta.get("issue_type", "")).strip().lower()
    labels = {str(x).strip().lower() for x in task_meta.get("labels", [])}

    if issue_type in COMPLEX_ISSUE_TYPES:
        return True
    if labels & COMPLEX_LABELS:
        return True

    estimate = task_meta.get("estimate")
    if isinstance(estimate, (int, float)) and estimate >= ESTIMATE_COMPLEX_POINTS:
        return True

    if int(task_meta.get("description_chars", 0) or 0) >= DESCRIPTION_COMPLEX_CHARS:
        return True

    if issue_type in SIMPLE_ISSUE_TYPES:
        return False

    # Unknown type, small/no other signal → default to simple (cheaper path).
    return False


def triage_label(task_meta: dict) -> str:
    return "complex" if is_complex(task_meta) else "simple"
