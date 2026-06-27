# PDD Docker Sandbox

This image is the execution plane for dangerous stages: `CODER`, `TESTER`, and
`TEST_RUN`. The orchestrator stays on the host and starts short-lived containers
with only the job worktree mounted at `/work`.

Build:

```powershell
python -m orchestrator.cli sandbox-build
```

Create the default bridge network:

```powershell
python -m orchestrator.cli sandbox-network
```

Smoke test against any writable directory:

```powershell
python -m orchestrator.cli sandbox-smoke C:\path\to\worktree
```

Runtime hardening is applied by `orchestrator.sandbox.docker_run_argv()`:

- only the job worktree is mounted;
- root filesystem is read-only;
- `/tmp` is writable tmpfs;
- capabilities are dropped;
- `no-new-privileges` is enabled;
- process, memory, and CPU limits are set;
- secret values are passed through env, never embedded in argv.

Egress is restricted, not open: agent containers join an **internal** Docker
network (`pdd-internal` by default, no direct route to the internet) and reach
the model host only through a squid sidecar proxy with a host allowlist
(`pdd-proxy`). Project dependency installs use a separate setup proxy; `TEST_RUN`
gets no network at all.

## Invariants

These are the security boundary — do not weaken them without a task explicitly
about sandbox behavior (see [../AGENTS.md](../AGENTS.md)):

- the container, not the worktree or the review stage, is the boundary;
- only the job worktree is mounted; no host `$HOME`, creds, or other repos;
- no host env is inherited except the `OPENAI_*` creds passed by name;
- `--cap-drop ALL`, `--read-only` rootfs, `--security-opt no-new-privileges`,
  non-root `--user`;
- agents have **no direct egress** — only the allowlist proxy;
- **`TEST_RUN` always runs with `--network none`**;
- fail-closed: no Docker and no explicit `PDD_ALLOW_UNSANDBOXED=1` /
  `PDD_REQUIRE_SANDBOX=0` → the stage refuses to start (`SandboxUnavailable`).

Knobs (image, network, proxy, user, seccomp, limits) live in `orchestrator/config.py`
(`PDD_SANDBOX_*`). Each job-bound run appends `sandbox_audit.jsonl` to the job
artifacts.

See [proxy/README.md](proxy/README.md) for the egress proxy and
[../orchestrator/README.md](../orchestrator/README.md) for how stages route here.
