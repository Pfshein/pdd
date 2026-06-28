"""User-facing CLI: run jobs and inspect their artifacts.

This is deliberately thin. The orchestrator remains the control plane; Docker is
an internal execution detail for dangerous stages.
"""
import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

from . import artifacts, config, events, progress, run as run_mod, sandbox, state as state_mod, worktree
from .graph import DONE


@contextlib.contextmanager
def _live_progress(enabled: bool = True):
    """Stream stage events to the console for the duration of a blocking run."""
    if not enabled:
        yield
        return
    sub = progress.console_printer()
    events.subscribe(sub)
    try:
        yield
    finally:
        events.unsubscribe(sub)


DEFAULT_SHOW = (
    "job_meta.json",
    "state.json",
    "transitions.jsonl",
    "attempts.jsonl",
    "plan.md",
    "diff.patch",
    "verdict.json",
    "test_result.json",
    "escalation.md",
)


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _print_path(label: str, path: Path) -> None:
    print(f"{label}: {path}")


def cmd_run(args) -> int:
    repo = args.repo or os.getcwd()  # default to the current directory
    task_md = Path(args.task).read_text(encoding="utf-8")
    task_meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    with _live_progress(not args.quiet):
        final = run_mod.run_pipeline(
            args.job,
            repo,
            task_md=task_md,
            task_meta=task_meta,
            test_command=args.test_command,
            setup_command=args.setup_command,
            base_ref=args.base_ref,
            keep_worktree=not args.drop_worktree,
            loop_profile=args.loop_profile,
        )
    print(f"\n=== {args.job} finished at: {final['node']} ===")
    print(f"branch:    pdd/{args.job}  (lives in the job checkout, not your repo)")
    _print_path("worktree", worktree.worktree_path(args.job))
    _print_path("artifacts", state_mod.job_dir(args.job))
    print(f"next:      pdd report {args.job}  |  pdd diff {args.job}  |  pdd publish {args.job} --push")
    return 0 if final["node"] == DONE else 2


def cmd_status(args) -> int:
    job = state_mod.validate_job_id(args.job)
    jd = state_mod.job_dir(job)
    st = _read_json(jd / "state.json")
    meta = _read_json(jd / "job_meta.json", {}) or {}
    if st is None:
        print(f"No state found for job {job} at {jd}", file=sys.stderr)
        return 2

    print(f"job: {job}")
    print(f"node: {st.get('node')}")
    print(f"steps: {st.get('global_steps')}/{st.get('global_step_cap')}")
    if meta.get("repo"):
        print(f"repo: {meta['repo']}")
    if meta.get("branch"):
        print(f"branch: {meta['branch']}")
    print(f"worktree: {meta.get('worktree') or worktree.worktree_path(job)}")
    print(f"artifacts: {jd}")
    return 0


def cmd_show(args) -> int:
    job = state_mod.validate_job_id(args.job)
    jd = state_mod.job_dir(job)
    names = args.artifacts or DEFAULT_SHOW
    found = False
    for name in names:
        path = jd / name
        if not path.exists():
            continue
        found = True
        print(f"\n--- {name} ---")
        print(path.read_text(encoding="utf-8", errors="replace").rstrip())
    if not found:
        print(f"No requested artifacts found for job {job} at {jd}", file=sys.stderr)
        return 2
    return 0


def cmd_diff(args) -> int:
    job = state_mod.validate_job_id(args.job)
    jd = state_mod.job_dir(job)
    diff_path = jd / "diff.patch"
    if diff_path.exists() and not args.live:
        print(diff_path.read_text(encoding="utf-8", errors="replace").rstrip())
        return 0

    meta = _read_json(jd / "job_meta.json", {}) or {}
    base_sha = meta.get("base_sha")
    if not base_sha:
        print(f"No diff.patch or base_sha found for job {job}", file=sys.stderr)
        return 2
    print(worktree.diff(job, base_sha).rstrip())
    return 0


def cmd_cleanup(args) -> int:
    job = state_mod.validate_job_id(args.job)
    meta = _read_json(state_mod.job_dir(job) / "job_meta.json", {}) or {}
    repo = args.repo or meta.get("repo")
    if not repo:
        print("cleanup needs --repo or job_meta.json with repo", file=sys.stderr)
        return 2
    worktree.remove(repo, job)
    print(f"removed worktree for {job}")
    return 0


def cmd_publish(args) -> int:
    from . import publish as publish_mod

    try:
        res = publish_mod.publish(
            args.job, push=args.push, make_pr=args.pr, base=args.base, message=args.message
        )
    except publish_mod.PublishError as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if res.get("pr_create_url"):
        print(f"\nOpen a PR: {res['pr_create_url']}", file=sys.stderr)
    elif res.get("committed") and not res.get("pushed"):
        print(f"\nCommitted to {res['branch']} (not pushed). Push: pdd publish {args.job} --push",
              file=sys.stderr)
    return 0 if (res.get("committed") or res.get("pushed")) else 1


def cmd_report(args) -> int:
    from . import report as report_mod

    job = state_mod.validate_job_id(args.job)
    if not (state_mod.job_dir(job) / "state.json").exists():
        print(f"no job state for {job}", file=sys.stderr)
        return 2
    md = report_mod.build_report(job)
    artifacts.write_text(job, "report.md", md)  # also keep it as an artifact
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        print(md)
    return 0


def cmd_doctor(args) -> int:
    from . import doctor as doctor_mod

    checks = doctor_mod.run_checks()
    print(doctor_mod.format_checks(checks))
    return 1 if doctor_mod.has_failures(checks) else 0


def cmd_resume(args) -> int:
    try:
        with _live_progress(not args.quiet):
            final = run_mod.resume_pipeline(args.job)
    except run_mod.ResumeError as exc:
        print(f"resume failed: {exc}", file=sys.stderr)
        return 2
    print(f"{args.job} -> {final['node']}")
    return 0 if final["node"] == DONE else 2


def cmd_retry(args) -> int:
    try:
        with _live_progress(not args.quiet):
            final = run_mod.retry_pipeline(args.job, args.stage)
    except run_mod.ResumeError as exc:
        print(f"retry failed: {exc}", file=sys.stderr)
        return 2
    print(f"{args.job} -> {final['node']}")
    return 0 if final["node"] == DONE else 2


def cmd_reap(args) -> int:
    from . import reaper

    job_rows = reaper.reap(dry_run=not args.apply, ttl_s=args.ttl)
    queue_rows = reaper.reap_queue(dry_run=not args.apply)
    if not job_rows and not queue_rows:
        print("no stale jobs")
        return 0
    print(json.dumps({"jobs": job_rows, "queue": queue_rows}, indent=2, ensure_ascii=False))
    return 0


def cmd_intake_jira(args) -> int:
    from . import jira

    issue = json.loads(Path(args.issue).read_text(encoding="utf-8"))
    res = jira.write_intake(issue, args.out)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


def cmd_jira_comment_draft(args) -> int:
    from . import jira

    job = state_mod.validate_job_id(args.job)
    report = artifacts.read_text(job, "report.md")
    escalation = artifacts.read_text(job, "escalation.md")
    comment = jira.needs_human_comment(job, report_md=report, reason=escalation[:500])
    if args.out:
        Path(args.out).write_text(comment, encoding="utf-8")
        print(f"jira comment draft written to {args.out}")
    else:
        print(comment)
    return 0


def cmd_enqueue(args) -> int:
    from . import queue as queue_mod

    # Store absolute paths: a worker may run from a different cwd later.
    repo = Path(args.repo or os.getcwd()).resolve()
    task = Path(args.task).resolve()
    meta = Path(args.meta).resolve()
    for label, path in (("repo", repo), ("task", task), ("meta", meta)):
        if not path.exists():
            print(f"enqueue: {label} not found: {path}", file=sys.stderr)
            return 2
    try:
        rec = queue_mod.enqueue(
            args.job,
            repo=str(repo),
            task=str(task),
            meta=str(meta),
            base_ref=args.base_ref,
            test_command=args.test_command,
            setup_command=args.setup_command,
        )
    except ValueError as exc:
        print(f"enqueue: {exc}", file=sys.stderr)
        return 2
    print(rec["job"])
    return 0


def _fmt_ts(ts) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "-"


def cmd_queue(args) -> int:
    from . import queue as queue_mod

    records = queue_mod.list_jobs()
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("queue is empty")
        return 0
    headers = ("JOB", "STATUS", "CREATED", "REPO")
    rows = [
        (r.get("job", ""), r.get("status", ""), _fmt_ts(r.get("created_ts")), r.get("repo", ""))
        for r in records
    ]
    widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(line.rstrip())
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(row))).rstrip())
    return 0


def cmd_worker(args) -> int:
    from . import worker as worker_mod

    return worker_mod.run_worker(
        once=args.once, poll_interval=args.poll_interval, worker=args.name
    )


def _run_command(argv: list[str]) -> int:
    from subprocess import run

    print(" ".join(argv), flush=True)
    return run(argv).returncode


def cmd_sandbox_build(args) -> int:
    argv = sandbox.docker_build_argv(
        image=args.image,
        qwen_package=args.qwen_package,
    )
    return _run_command(argv)


def cmd_sandbox_network(args) -> int:
    net = args.network or config.SANDBOX_NETWORK
    internal = sandbox.network_is_internal(net)
    if internal is True:
        print(f"sandbox network already exists and is internal: {net}")
        return 0
    if internal is False:
        print(
            f"REFUSING: network '{net}' exists but is NOT internal (free egress).\n"
            f"Recreate it as internal:\n"
            f"  docker network rm {net}\n"
            f"  python -m orchestrator.cli sandbox-network",
            file=sys.stderr,
        )
        return 2
    return _run_command(sandbox.docker_network_create_argv(network=args.network))


def cmd_sandbox_smoke(args) -> int:
    argv = sandbox.docker_smoke_argv(args.worktree, network=args.network)
    return _run_command(argv)


def cmd_proxy_up(args) -> int:
    conf = sandbox.write_squid_conf()
    print(f"squid conf ({len(config.model_host_allowlist())} allowed host(s)): {conf}")
    rc = _run_command(sandbox.proxy_run_argv())
    if rc != 0:
        return rc
    # Give the proxy egress so it can actually reach the allowlisted host.
    return _run_command(sandbox.proxy_connect_external_argv())


def cmd_proxy_status(args) -> int:
    return _run_command(sandbox.proxy_status_argv())


def cmd_proxy_smoke(args) -> int:
    hosts = config.model_host_allowlist()
    if not hosts:
        print("no allowlist host configured (set OPENAI_BASE_URL or PDD_MODEL_HOST_ALLOWLIST)",
              file=sys.stderr)
        return 2
    print(f"-- allowed host {hosts[0]} (expect an HTTP code) --")
    _run_command(sandbox.proxy_smoke_argv(hosts[0], allowed=True))
    print("\n-- denied host example.com (expect proxy denial / non-2xx) --")
    _run_command(sandbox.proxy_smoke_argv("example.com", allowed=False))
    return 0


def cmd_setup_proxy_up(args) -> int:
    conf = sandbox.write_setup_squid_conf()
    print(f"setup squid conf ({len(config.setup_host_allowlist())} allowed host(s)): {conf}")
    rc = _run_command(sandbox.setup_proxy_run_argv())
    if rc != 0:
        return rc
    return _run_command(sandbox.setup_proxy_connect_external_argv())


def cmd_setup_proxy_status(args) -> int:
    return _run_command(sandbox.setup_proxy_status_argv())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pdd", description="PDD pipeline CLI.")
    sub = p.add_subparsers(dest="cmd")  # not required: bare `pdd` prints help

    run_p = sub.add_parser("run", help="run one job end-to-end")
    run_p.add_argument("--job", required=True, help="correlation id (Jira key)")
    run_p.add_argument("--repo", default=None, help="target git repo path (default: cwd)")
    run_p.add_argument("--task", required=True, help="path to task.md")
    run_p.add_argument("--meta", required=True, help="path to task_meta.json")
    run_p.add_argument("--test-command", default=None)
    run_p.add_argument("--setup-command", default=None, help="dependency install command before TEST_RUN")
    run_p.add_argument("--base-ref", default="HEAD")
    run_p.add_argument("--loop-profile", default=config.DEFAULT_LOOP_PROFILE,
                       choices=sorted(config.LOOP_PROFILES),
                       help=f"budget/cap profile (default: {config.DEFAULT_LOOP_PROFILE})")
    run_p.add_argument("--drop-worktree", action="store_true")
    run_p.add_argument("--quiet", action="store_true", help="suppress live stage progress")
    run_p.set_defaults(func=cmd_run)

    enqueue_p = sub.add_parser("enqueue", help="add a job to the durable queue (does not run it)")
    enqueue_p.add_argument("--job", required=True, help="correlation id (Jira key)")
    enqueue_p.add_argument("--repo", default=None, help="target git repo path (default: cwd)")
    enqueue_p.add_argument("--task", required=True, help="path to task.md")
    enqueue_p.add_argument("--meta", required=True, help="path to task_meta.json")
    enqueue_p.add_argument("--base-ref", default="HEAD")
    enqueue_p.add_argument("--test-command", default=None)
    enqueue_p.add_argument("--setup-command", default=None, help="dependency install command before TEST_RUN")
    enqueue_p.set_defaults(func=cmd_enqueue)

    queue_p = sub.add_parser("queue", help="list queued jobs")
    queue_p.add_argument("--json", action="store_true", help="machine-readable records")
    queue_p.set_defaults(func=cmd_queue)

    worker_p = sub.add_parser("worker", help="process queued jobs one at a time")
    worker_p.add_argument("--once", action="store_true", help="process at most one job and exit")
    worker_p.add_argument("--poll-interval", type=float, default=5.0,
                          help="seconds between polls when idle (ignored with --once)")
    worker_p.add_argument("--name", default=None, help="worker id recorded in the lease")
    worker_p.set_defaults(func=cmd_worker)

    status_p = sub.add_parser("status", help="print job status")
    status_p.add_argument("job")
    status_p.set_defaults(func=cmd_status)

    show_p = sub.add_parser("show", help="print job artifacts")
    show_p.add_argument("job")
    show_p.add_argument("artifacts", nargs="*")
    show_p.set_defaults(func=cmd_show)

    diff_p = sub.add_parser("diff", help="print saved or live job diff")
    diff_p.add_argument("job")
    diff_p.add_argument("--live", action="store_true", help="recompute from worktree")
    diff_p.set_defaults(func=cmd_diff)

    cleanup_p = sub.add_parser("cleanup", help="remove the job worktree")
    cleanup_p.add_argument("job")
    cleanup_p.add_argument("--repo", default=None)
    cleanup_p.set_defaults(func=cmd_cleanup)

    publish_p = sub.add_parser("publish", help="commit the job worktree to its branch (+optional push/PR)")
    publish_p.add_argument("job")
    publish_p.add_argument("--push", action="store_true", help="push the branch to origin")
    publish_p.add_argument("--pr", action="store_true", help="open a PR via gh (requires --push)")
    publish_p.add_argument("--base", default=None, help="PR base branch (default: job base_ref)")
    publish_p.add_argument("--message", default=None, help="commit title override")
    publish_p.set_defaults(func=cmd_publish)

    report_p = sub.add_parser("report", help="render a human-readable job report (Markdown)")
    report_p.add_argument("job")
    report_p.add_argument("--out", default=None, help="write to a file instead of stdout")
    report_p.set_defaults(func=cmd_report)

    doctor_p = sub.add_parser("doctor", help="check the environment (tools, creds, sandbox)")
    doctor_p.set_defaults(func=cmd_doctor)

    resume_p = sub.add_parser("resume", help="continue a job from its saved state")
    resume_p.add_argument("job")
    resume_p.add_argument("--quiet", action="store_true", help="suppress live stage progress")
    resume_p.set_defaults(func=cmd_resume)

    retry_p = sub.add_parser("retry", help="rewind a job to a stage and drive forward")
    retry_p.add_argument("job")
    retry_p.add_argument("--stage", required=True, help="stage to re-run from (e.g. CODER)")
    retry_p.add_argument("--quiet", action="store_true", help="suppress live stage progress")
    retry_p.set_defaults(func=cmd_retry)

    reap_p = sub.add_parser("reap", help="mark stale jobs NEEDS_HUMAN and remove their worktrees")
    reap_p.add_argument("--apply", action="store_true", help="perform cleanup; default is dry-run")
    reap_p.add_argument("--ttl", type=int, default=None, help=f"default: {config.JOB_TTL_S}s")
    reap_p.set_defaults(func=cmd_reap)

    intake_jira_p = sub.add_parser("intake-jira", help="normalize a Jira issue JSON into task files")
    intake_jira_p.add_argument("--issue", required=True, help="path to Jira issue JSON")
    intake_jira_p.add_argument("--out", required=True, help="directory for task.md/task_meta.json")
    intake_jira_p.set_defaults(func=cmd_intake_jira)

    jira_comment_p = sub.add_parser("jira-comment-draft", help="draft a Jira comment for NEEDS_HUMAN")
    jira_comment_p.add_argument("job")
    jira_comment_p.add_argument("--out", default=None, help="write draft to file instead of stdout")
    jira_comment_p.set_defaults(func=cmd_jira_comment_draft)

    build_p = sub.add_parser("sandbox-build", help="build the Docker sandbox image")
    build_p.add_argument("--image", default=None, help=f"default: {config.SANDBOX_IMAGE}")
    build_p.add_argument("--qwen-package", default=None, help="override npm package for qwen")
    build_p.set_defaults(func=cmd_sandbox_build)

    network_p = sub.add_parser("sandbox-network", help="create the Docker sandbox network")
    network_p.add_argument("--network", default=None, help=f"default: {config.SANDBOX_NETWORK}")
    network_p.set_defaults(func=cmd_sandbox_network)

    smoke_p = sub.add_parser("sandbox-smoke", help="run a cheap sandbox smoke test")
    smoke_p.add_argument("worktree", help="writable directory mounted as /work")
    smoke_p.add_argument("--network", default=None, help=f"default: {config.SANDBOX_NETWORK}")
    smoke_p.set_defaults(func=cmd_sandbox_smoke)

    proxy_up_p = sub.add_parser("proxy-up", help="start the egress allowlist proxy")
    proxy_up_p.set_defaults(func=cmd_proxy_up)

    proxy_status_p = sub.add_parser("proxy-status", help="show the proxy container status")
    proxy_status_p.set_defaults(func=cmd_proxy_status)

    proxy_smoke_p = sub.add_parser("proxy-smoke", help="check allowed vs denied egress via proxy")
    proxy_smoke_p.set_defaults(func=cmd_proxy_smoke)

    setup_proxy_up_p = sub.add_parser("setup-proxy-up", help="start dependency setup proxy")
    setup_proxy_up_p.set_defaults(func=cmd_setup_proxy_up)

    setup_proxy_status_p = sub.add_parser("setup-proxy-status", help="show dependency setup proxy status")
    setup_proxy_status_p.set_defaults(func=cmd_setup_proxy_status)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):  # bare `pdd` -> interactive menu in a TTY
        if sys.stdin.isatty():
            try:
                from . import menu
            except ImportError:
                pass
            else:
                return menu.run()
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
