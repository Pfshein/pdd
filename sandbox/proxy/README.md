# Sandbox egress allowlist (proxy)

Agent containers join an **internal** Docker network (`pdd-internal`, created with
`--internal`) and have **no direct route to the internet**. Their only way out is a
squid sidecar (`pdd-proxy`) that allowlists the model endpoint host(s).

```
[ agent container ]  --HTTPS_PROXY-->  [ pdd-proxy (squid) ]  --allowlist-->  model host
   pdd-internal only                    pdd-internal + bridge
```

- The proxy denies everything except `CONNECT` on 443 to the allowlisted hosts
  (default: the host of `OPENAI_BASE_URL`; override with `PDD_MODEL_HOST_ALLOWLIST`).
- `TEST_RUN` runs with `--network none` — tests never need the model, so they get
  zero egress.

## Bring-up

```
python -m orchestrator.cli sandbox-build
python -m orchestrator.cli sandbox-network     # creates pdd-internal (--internal)
python -m orchestrator.cli proxy-up            # renders squid.conf, starts pdd-proxy
python -m orchestrator.cli proxy-smoke         # allowed host ok, others denied
```

The squid.conf is generated at `proxy-up` time from the allowlist (see
`orchestrator/sandbox.py::render_squid_conf`); `squid.conf.example` is only a sample.

> NOTE (validated at PDD-11): this assumes qwen's HTTP client honours `HTTPS_PROXY`.
> If it does not, egress control must move to a network-level filter instead of a
> proxy. Confirm during the first real sandboxed run.
