"""Job state + loop policy profiles (PDD-31)."""
import pytest

from orchestrator import config, state as state_mod


def test_standard_profile_matches_current_defaults():
    p = config.loop_profile("standard")
    assert p["budgets"] == config.DEFAULT_BUDGETS
    assert p["global_step_cap"] == config.GLOBAL_STEP_CAP


def test_conservative_is_tighter_than_aggressive():
    c = config.loop_profile("conservative")
    a = config.loop_profile("aggressive")
    assert c["global_step_cap"] < config.GLOBAL_STEP_CAP < a["global_step_cap"]
    assert c["budgets"]["CODER"] < config.DEFAULT_BUDGETS["CODER"] < a["budgets"]["CODER"]


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        config.loop_profile("turbo")


def test_loop_profile_returns_copies():
    p = config.loop_profile("standard")
    p["budgets"]["CODER"] = 999
    assert config.DEFAULT_BUDGETS["CODER"] != 999  # mutating the result must not leak


def test_new_state_uses_profile_budgets_and_cap():
    p = config.loop_profile("conservative")
    st = state_mod.new_state("JOB-P", budgets=p["budgets"], global_step_cap=p["global_step_cap"])
    assert st["budgets"]["CODER"]["max"] == p["budgets"]["CODER"]
    assert st["global_step_cap"] == p["global_step_cap"]
