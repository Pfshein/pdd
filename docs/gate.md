# PDD-11 — real sandboxed end-to-end gate

The gate that must pass before product features are built on top of the sandbox:
prove the **whole pipeline runs inside the Docker boundary against a real model**,
not just unit tests.

## Procedure (Docker Desktop running)

```
python -m orchestrator.cli sandbox-build      # pdd-sandbox:latest (node + python + qwen)
python -m orchestrator.cli sandbox-network     # pdd-internal (--internal, no direct egress)
python -m orchestrator.cli proxy-up            # squid sidecar, allowlist = model host
python -m orchestrator.cli proxy-smoke         # allowed vs denied egress

PYTHONPATH=. python tools/probe_sandbox_model.py   # qwen reaches the model via proxy?
PYTHONPATH=. python tools/demo_e2e.py              # full pipeline on a fixture repo
```

## Result — PASSED (2026-06-06)

- **Egress allowlist enforced.** From a container on `pdd-internal` (no direct route
  out): `https://opencode.ai` → `200` via the proxy; `https://example.com` →
  `CONNECT tunnel failed, 403` (squid deny). The internal network gives zero direct
  egress; the only path out is the allowlisted proxy.
- **qwen honours `HTTPS_PROXY`** (the key open question). A real qwen call inside the
  sandbox returned `exit 0` / `"OK"` reaching the model ONLY through the proxy. So the
  egress boundary actually works with qwen — no need to fall back to a network-level
  filter.
- **Full pipeline to `DONE`.** INTAKE → TRIAGE(simple) → CODER → CODE_REVIEW → TESTER →
  TEST_RUN → FINAL_REVIEW → DONE. CODER/TESTER and TEST_RUN ran in containers
  (`TEST_RUN` with `--network none`), ARCHITECT/reviewers on the host. The model fixed
  the bug (`a - b` → `a + b`), tests went green, the diff was clean (no `__pycache__`).
- **No leaks.** No `SECURITY.txt` (stages were sandboxed, not the unsandboxed
  fallback); no orphaned agent/test containers afterwards (only the persistent
  `pdd-proxy`) — `--rm` + the PDD-04b kill-on-timeout held.

## Open follow-up: project dependencies vs the allowlist

The fixture needs no third-party deps (stdlib + pytest, baked into the image). A real
target repo needs its own deps (`pip install -r requirements.txt` / `npm ci`), but the
egress allowlist only permits the **model host** — package registries (pypi, npm) are
blocked, and `TEST_RUN` runs with `--network none`. So a project-deps `SETUP_COMMAND`
cannot just `pip install` inside the locked agent network.

Options (decide in a follow-up PR): per-project image baked with deps; or a dedicated
setup phase on a wider, separate allowlist (pypi/npm) distinct from the agent's
model-only egress. Not needed for the gate; required for arbitrary real repos.
