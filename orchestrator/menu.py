"""Interactive menu (PDD-23): bare `pdd` opens an arrow-key menu.

A thin questionary layer over the existing cli.cmd_* handlers — no engine logic
here. It gathers inputs by selection/prompt and dispatches to the same commands,
so behaviour stays identical to the flag-driven CLI.
"""
import json
import os
import types

import questionary

from . import cli, config

BACK = "< back"


def list_jobs() -> list:
    """Jobs that have run, with their current node. For the 'View jobs' screen."""
    base = config.RUNS_DIR
    if not base.exists():
        return []
    jobs = []
    for d in sorted(base.iterdir()):
        sj = d / "state.json"
        if not sj.exists():
            continue
        try:
            node = json.loads(sj.read_text(encoding="utf-8")).get("node", "?")
        except (OSError, json.JSONDecodeError):
            node = "?"
        jobs.append({"job": d.name, "node": node})
    return jobs


def _invoke(func, **kw) -> int:
    """Call a cli.cmd_* handler with an argparse-like namespace."""
    return func(types.SimpleNamespace(**kw))


def _run_job_flow() -> None:
    job = questionary.text("Job id:", default="PDD-1").ask()
    if not job:
        return
    repo = questionary.path("Target repo:", default=os.getcwd()).ask()
    task = questionary.path("Task file (task.md):").ask()
    meta = questionary.path("Meta file (task_meta.json):").ask()
    if not (repo and task and meta):
        print("cancelled (need repo, task and meta)")
        return
    test_cmd = questionary.text("Test command:", default=config.TEST_COMMAND).ask()
    setup_cmd = questionary.text("Setup command (optional):", default="").ask()
    _invoke(
        cli.cmd_run, job=job, repo=repo, task=task, meta=meta,
        test_command=(test_cmd or None), setup_command=(setup_cmd or None),
        base_ref="HEAD", drop_worktree=False, quiet=False,
    )


def _job_actions_flow(job: str) -> None:
    while True:
        action = questionary.select(
            f"{job}:", choices=["report", "diff", "publish", "resume", "retry", BACK]
        ).ask()
        if not action or action == BACK:
            return
        if action == "report":
            _invoke(cli.cmd_report, job=job, out=None)
        elif action == "diff":
            _invoke(cli.cmd_diff, job=job, live=False)
        elif action == "publish":
            push = questionary.confirm("Push branch to origin?", default=False).ask()
            _invoke(cli.cmd_publish, job=job, push=bool(push), pr=False, base=None, message=None)
        elif action == "resume":
            _invoke(cli.cmd_resume, job=job, quiet=False)
        elif action == "retry":
            stage = questionary.text("Stage to retry from (e.g. CODER):").ask()
            if stage:
                _invoke(cli.cmd_retry, job=job, stage=stage, quiet=False)


def _view_jobs_flow() -> None:
    jobs = list_jobs()
    if not jobs:
        print("no jobs yet — run one first")
        return
    choice = questionary.select(
        "Job:", choices=[f"{j['job']}  [{j['node']}]" for j in jobs] + [BACK]
    ).ask()
    if not choice or choice == BACK:
        return
    _job_actions_flow(choice.split()[0])


def _sandbox_flow() -> None:
    while True:
        action = questionary.select(
            "Sandbox setup:",
            choices=["sandbox-build", "sandbox-network", "proxy-up", "setup-proxy-up", BACK],
        ).ask()
        if not action or action == BACK:
            return
        if action == "sandbox-build":
            _invoke(cli.cmd_sandbox_build, image=None, qwen_package=None)
        elif action == "sandbox-network":
            _invoke(cli.cmd_sandbox_network, network=None)
        elif action == "proxy-up":
            _invoke(cli.cmd_proxy_up)
        elif action == "setup-proxy-up":
            _invoke(cli.cmd_setup_proxy_up)


def run() -> int:
    while True:
        choice = questionary.select(
            "PDD", choices=["Run a job", "View jobs", "Doctor", "Sandbox setup", "Quit"]
        ).ask()
        if choice in (None, "Quit"):
            return 0
        if choice == "Run a job":
            _run_job_flow()
        elif choice == "View jobs":
            _view_jobs_flow()
        elif choice == "Doctor":
            _invoke(cli.cmd_doctor)
        elif choice == "Sandbox setup":
            _sandbox_flow()
