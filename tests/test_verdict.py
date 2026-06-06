"""Parser/validator tests using captured qwen output shapes."""
import json

import jsonschema
import pytest

from orchestrator import verdict


def _stdout(events):
    return json.dumps(events)


SUCCESS = _stdout([
    {"type": "system", "subtype": "init"},
    {"type": "assistant"},
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"issues":[{"class":"logic_bug","summary":"subtracts instead of adds","location":"calc.py:add"}]}',
        "structured_result": {
            "issues": [
                {"class": "logic_bug", "summary": "subtracts instead of adds", "location": "calc.py:add"}
            ]
        },
    },
])

PASS = _stdout([
    {"type": "system"},
    {"type": "result", "is_error": False, "structured_result": {"issues": []}},
])

ERROR = _stdout([
    {"type": "system"},
    {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "error": {"message": "Model produced plain text instead of calling structured_output"},
    },
])


def test_extract_structured_success():
    obj, err = verdict.extract_structured(SUCCESS)
    assert err is None
    assert obj["issues"][0]["class"] == "logic_bug"
    verdict.validate_verdict(obj)
    assert verdict.is_pass(obj) is False


def test_extract_structured_falls_back_to_result_string():
    # drop structured_result, keep only the "result" string
    events = json.loads(SUCCESS)
    for ev in events:
        ev.pop("structured_result", None)
    obj, err = verdict.extract_structured(json.dumps(events))
    assert err is None
    assert obj["issues"][0]["class"] == "logic_bug"


def test_pass_verdict_has_no_blocking():
    obj, err = verdict.extract_structured(PASS)
    assert err is None
    assert verdict.is_pass(obj) is True


def test_error_event_returns_message():
    obj, err = verdict.extract_structured(ERROR)
    assert obj is None
    assert "structured_output" in err


def test_empty_stdout_is_error():
    obj, err = verdict.extract_structured("")
    assert obj is None
    assert err


def test_validate_rejects_bad_class():
    with pytest.raises(jsonschema.ValidationError):
        verdict.validate_verdict({"issues": [{"class": "totally_made_up", "summary": "x"}]})


def test_signature_stable_and_diff_sensitive():
    v = {"issues": [{"class": "logic_bug", "summary": "Bad Thing"}]}
    s1 = verdict.verdict_signature(v, "diffA")
    s2 = verdict.verdict_signature({"issues": [{"class": "logic_bug", "summary": "bad thing"}]}, "diffA")
    s3 = verdict.verdict_signature(v, "diffB")
    assert s1 == s2  # case/whitespace-normalized
    assert s1 != s3  # different diff -> different signature


def test_salvage_verdict_from_plain_text():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": 'My review: {"issues": [{"class": "logic_bug", "summary": "off by one"}]}'}
        ]}},
        {"type": "result", "is_error": True, "error": {"message": "plain text instead of structured_output"}},
    ]
    obj = verdict.salvage_verdict(json.dumps(events))
    assert obj is not None
    assert obj["issues"][0]["class"] == "logic_bug"


def test_salvage_rejects_schema_invalid_json():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": 'noise {"issues": [{"class": "not_a_class", "summary": "x"}]} more'}
        ]}},
        {"type": "result", "is_error": True},
    ]
    assert verdict.salvage_verdict(json.dumps(events)) is None


def test_salvage_returns_none_without_json():
    events = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "no json here"}]}},
        {"type": "result", "is_error": True},
    ]
    assert verdict.salvage_verdict(json.dumps(events)) is None


def test_signature_ignores_nits():
    v_nit = {"issues": [{"class": "logic_bug", "summary": "x"}, {"class": "nit", "summary": "style"}]}
    v_plain = {"issues": [{"class": "logic_bug", "summary": "x"}]}
    assert verdict.verdict_signature(v_nit, "d") == verdict.verdict_signature(v_plain, "d")
