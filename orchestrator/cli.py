"""User-facing CLI: run jobs and inspect their artifacts.

This is deliberately thin. The orchestrator remains the control plane; Docker is
an internal execution detail for dangerous stages.
"""
import argparse
import json
import sys
from pathlib import Path

from . import artifacts, config, run as run_mod, sandbox, state as state_mod, worktree
from .graph import DONE


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
    task_md = Path(args.task).read_text(encoding="utf-8")
    task_meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    final = run_mod.run_pipeline(
        args.job,
        args.repo,
        task_md=task_md,
        task_meta=task_meta,
        test_command=args.test_command,
        base_ref=args.base_ref,
        keep_worktree=not args.drop_worktree,
    )
    print(f"\n=== {args.job} finished at: {final['node']} ===")
    _print_path("worktree", worktree.worktree_path(args.job))
    _print_path("artifacts", state_mod.job_dir(args.job))
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
    return 0 if res.get("committed") else 1


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PDD pipeline CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="run one job end-to-end")
    run_p.add_argument("--job", required=True, help="correlation id (Jira key)")
    run_p.add_argument("--repo", required=True, help="target git repo path")
    run_p.add_argument("--task", required=True, help="path to task.md")
    run_p.add_argument("--meta", required=True, help="path to task_meta.json")
    run_p.add_argument("--test-command", default=None)
    run_p.add_argument("--base-ref", default="HEAD")
    run_p.add_argument("--drop-worktree", action="store_true")
    run_p.set_defaults(func=cmd_run)

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
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
