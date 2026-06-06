"""Gate probe: can qwen reach the model from inside the sandbox via HTTPS_PROXY?

Runs one qwen call in the pdd-sandbox container on the INTERNAL network (no direct
egress) with HTTPS_PROXY set. If qwen honours the proxy -> it reaches the model;
if it ignores it -> the internal network blocks egress and the call fails.

Run: PYTHONPATH=. python tools/probe_sandbox_model.py
"""
import tempfile

from orchestrator import config, runner, sandbox


def main():
    creds = config.model_env()
    argv = runner.build_qwen_argv(
        model=creds["OPENAI_MODEL"],
        base_url=creds["OPENAI_BASE_URL"],
        approval="yolo",
        output_format="json",
        wall_time_s=60,
        max_tool_calls=2,
        qwen_bin="qwen",
    )
    with tempfile.TemporaryDirectory() as td:
        res = sandbox.run_in_sandbox(
            argv, worktree=td, stdin="Reply with the single word: OK", timeout=120
        )
    print("network:", config.SANDBOX_NETWORK, "proxy:", config.SANDBOX_HTTPS_PROXY)
    print("exit_code:", res.get("exit_code"), "container:", res.get("container"))
    ev = __import__("orchestrator.verdict", fromlist=["last_result_event"]).last_result_event(res.get("stdout", ""))
    if ev:
        print("result subtype:", ev.get("subtype"), "is_error:", ev.get("is_error"))
        print("result text:", str(ev.get("result"))[:300])
    print("STDERR tail:", (res.get("stderr") or "")[-600:])


if __name__ == "__main__":
    main()
