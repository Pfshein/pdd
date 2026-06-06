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

The current network is a named Docker bridge (`pdd-egress` by default). The next
hardening layer is a proxy sidecar with an egress allowlist for the model host.
