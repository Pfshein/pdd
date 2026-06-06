"""Empirical probe: run ONE real reviewer stage and dump raw output.

Confirms (a) qwen tool calling works against the remote model, (b) --json-schema
forces a schema-valid verdict, (c) what the stdout/json-file payload looks like
so we can write the parser. Run:  python tools/probe_review.py
"""
import json
import tempfile
from pathlib import Path

from orchestrator import config, runner

SCHEMA = config.SCHEMAS_DIR / "verdict.json"

DIFF = """\
--- a/calc.py
+++ b/calc.py
@@
-def add(a, b):
-    return a + b
+def add(a, b):
+    return a - b   # BUG: subtraction instead of addition
"""

PROMPT = f"""You are a strict code reviewer. The complete diff to review is included
below in this message. Do NOT use any tools to explore the filesystem; there are no
other files. Review ONLY the diff text below, then call the structured_output tool
exactly once with your findings.

Classify each issue with one of: logic_bug, weak_tests, wrong_design, nit.
If there are no problems, call structured_output with an empty issues list.

DIFF TO REVIEW:
{DIFF}
"""

EXCLUDE = "run_shell_command,glob,read_file,edit,notebook_edit,read_many_files,web_fetch,web_search"


def _print_result_event(stdout):
    try:
        events = json.loads(stdout)
    except json.JSONDecodeError:
        print("!! stdout is not a JSON array")
        return
    for ev in events:
        if ev.get("type") == "result":
            print("=== RESULT EVENT ===")
            print(json.dumps(ev, indent=2, ensure_ascii=False))


def main():
    with tempfile.TemporaryDirectory() as td:
        json_file = Path(td) / "events.jsonl"
        result = runner.run_qwen_stage(
            PROMPT,
            cwd=td,
            approval="yolo",
            json_schema=f"@{SCHEMA}",
            output_format="json",
            json_file=json_file,
            wall_time_s=180,
            max_tool_calls=5,
            extra=["--exclude-tools", EXCLUDE],
        )
        print("=== exit_code:", result["exit_code"], "timed_out:", result["timed_out"])
        _print_result_event(result["stdout"])
        print("=== STDERR (tail) ===")
        print(result["stderr"][-800:])


if __name__ == "__main__":
    main()
