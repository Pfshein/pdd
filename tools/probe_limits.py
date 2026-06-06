"""Probe: how does qwen signal wall-time vs max-tool-calls exhaustion?

Both abort with exit 55, but the router must tell them apart (tool-limit ->
stuck, escalate; wall-time -> slow, retry bigger). Run: PYTHONPATH=. python tools/probe_limits.py
"""
import json
import tempfile

from orchestrator import runner, verdict


def _dump(label, res):
    print(f"\n===== {label} =====")
    print("exit_code:", res.get("exit_code"), "timed_out:", res.get("timed_out"))
    ev = verdict.last_result_event(res.get("stdout", ""))
    if ev:
        print("subtype:", ev.get("subtype"), "is_error:", ev.get("is_error"))
        print("error:", json.dumps(ev.get("error"), ensure_ascii=False))
    else:
        print("no result event; stderr tail:", (res.get("stderr") or "")[-400:])


def main():
    with tempfile.TemporaryDirectory() as td:
        # A: tool-call limit — force a tool call but allow zero.
        res_a = runner.run_qwen_stage(
            "Use the run_shell_command tool to run `echo hi`. You MUST call a tool.",
            cwd=td, output_format="json", max_tool_calls=0, wall_time_s=120,
        )
        _dump("max-tool-calls=0", res_a)

        # B: wall-time limit — 1 second budget.
        res_b = runner.run_qwen_stage(
            "Write a short poem about databases.",
            cwd=td, output_format="json", max_tool_calls=40, wall_time_s=1,
        )
        _dump("max-wall-time=1", res_b)


if __name__ == "__main__":
    main()
