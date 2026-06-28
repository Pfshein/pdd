"""Prompt contract guardrails for weak-model failure modes."""
from orchestrator import artifacts


def test_architect_prompt_forbids_inventing_unspecified_details():
    prompt = artifacts.load_role_prompt("architect")

    assert "Do not invent filenames" in prompt
    assert "artifact formats" in prompt
    assert "coder must verify the existing code/artifacts" in prompt


def test_coder_prompt_treats_plan_as_advisory():
    prompt = artifacts.load_role_prompt("coder")

    assert "Treat the plan as advisory" in prompt
    assert "stronger sources" in prompt
    assert "guessed filenames" in prompt
