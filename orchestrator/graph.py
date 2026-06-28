"""State graph as plain data.

Nodes are strings. Routing logic lives in router.py; this module only declares
the topology, classifications, and small predicates over them.
"""

# --- Nodes ----------------------------------------------------------------
INTAKE = "INTAKE"
TRIAGE = "TRIAGE"
ARCHITECT = "ARCHITECT"
CODER = "CODER"
CODE_REVIEW = "CODE_REVIEW"
TESTER = "TESTER"
TEST_RUN = "TEST_RUN"
FINAL_REVIEW = "FINAL_REVIEW"
DONE = "DONE"
NEEDS_HUMAN = "NEEDS_HUMAN"

TERMINAL = frozenset({DONE, NEEDS_HUMAN})

# qwen one-shot stages vs deterministic code steps
QWEN_STAGES = frozenset({INTAKE, ARCHITECT, CODER, CODE_REVIEW, TESTER, FINAL_REVIEW})
DETERMINISTIC = frozenset({TRIAGE, TEST_RUN})

# Reviewer nodes that emit a machine-readable verdict.
REVIEW_NODES = frozenset({CODE_REVIEW, FINAL_REVIEW})

# Stages that are targets of loop-backs and therefore carry attempt budgets.
RETURN_TARGETS = frozenset({ARCHITECT, CODER, TESTER})

# --- Verdict classification → stage (mapping lives in code, not in the LLM) -
# The LLM only classifies; code names the stage.
CLASS_TO_STAGE = {
    "logic_bug": CODER,
    "weak_tests": TESTER,
    "wrong_design": ARCHITECT,
    # "nit" is non-blocking by construction → no stage.
}

# Priority when several blocking issues coexist (router picks one target).
CLASS_PRIORITY = ("wrong_design", "logic_bug", "weak_tests")

BLOCKING_CLASSES = frozenset(CLASS_TO_STAGE.keys())  # everything except "nit"

# Forward (happy-path) ordering. A move to a lower index is a "loop-back".
ORDER = {
    INTAKE: 0,
    TRIAGE: 1,
    ARCHITECT: 2,
    CODER: 3,
    CODE_REVIEW: 4,
    TESTER: 5,
    TEST_RUN: 6,
    FINAL_REVIEW: 7,
}


# --- Machine-readable terminal reasons (router sets one on every terminal hop) -
REASON_DONE = "done"
REASON_STAGE_ERROR = "stage_error"
REASON_GLOBAL_STEP_CAP = "global_step_cap"
REASON_NO_PROGRESS = "no_progress"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_UNKNOWN = "unknown"
TERMINAL_REASONS = frozenset({
    REASON_DONE, REASON_STAGE_ERROR, REASON_GLOBAL_STEP_CAP,
    REASON_NO_PROGRESS, REASON_BUDGET_EXHAUSTED, REASON_UNKNOWN,
})


def is_terminal(node: str) -> bool:
    return node in TERMINAL


def highest_priority_class(classes) -> str:
    """Return the most important blocking class present."""
    present = set(classes)
    for cls in CLASS_PRIORITY:
        if cls in present:
            return cls
    raise ValueError(f"no blocking class in {classes!r}")
