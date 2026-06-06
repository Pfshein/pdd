"""TTL cleanup for stale jobs/worktrees.

The reaper is intentionally conservative: it does not delete job artifacts and
does not guess Docker containers by broad name patterns. It marks stale
non-terminal jobs as NEEDS_HUMAN and tears down their worktree.
"""
import json
import shutil
import time

from . import config, graph, state as state_mod, worktree

TERMINAL_NODES = {graph.DONE, graph.NEEDS_HUMAN}


def _safe_rmtree_worktree(job: str) -> bool:
    """Remove WORKTREES_DIR/<job> only after proving it stays under WORKTREES_DIR."""
    wt = worktree.worktree_path(job)
    if not wt.exists():
        return False
    root = config.WORKTREES_DIR.resolve()
    target = wt.resolve()
    if target == root or root not in target.parents:
        raise RuntimeError(f"refusing to remove worktree outside {root}: {target}")
    shutil.rmtree(target, ignore_errors=True)
    return True


def stale_jobs(*, now: float | None = None, ttl_s: int | None = None) -> list[dict]:
    """Return stale, non-terminal jobs based on state.json mtime."""
    now = time.time() if now is None else now
    ttl_s = config.JOB_TTL_S if ttl_s is None else ttl_s
    if not config.RUNS_DIR.exists():
        return []

    out = []
    for jd in config.RUNS_DIR.iterdir():
        state_path = jd / "state.json"
        if not jd.is_dir() or not state_path.exists():
            continue
        try:
            st = json.loads(state_path.read_text(encoding="utf-8"))
            job = state_mod.validate_job_id(st.get("job"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        age_s = now - state_path.stat().st_mtime
        if st.get("node") not in TERMINAL_NODES and age_s >= ttl_s:
            out.append({"job": job, "node": st.get("node"), "age_s": int(age_s), "path": str(jd)})
    return out


def reap(*, dry_run: bool = True, now: float | None = None, ttl_s: int | None = None) -> list[dict]:
    """Reap stale jobs. Returns one result row per stale job."""
    rows = []
    for item in stale_jobs(now=now, ttl_s=ttl_s):
        job = item["job"]
        row = dict(item)
        if dry_run:
            row["action"] = "would-reap"
            rows.append(row)
            continue

        meta_path = state_mod.job_dir(job) / "job_meta.json"
        repo = None
        if meta_path.exists():
            try:
                repo = json.loads(meta_path.read_text(encoding="utf-8")).get("repo")
            except (OSError, json.JSONDecodeError):
                repo = None

        if repo:
            worktree.remove(repo, job)
            row["worktree"] = "removed-via-git"
        else:
            row["worktree"] = "removed-dir" if _safe_rmtree_worktree(job) else "missing"

        st = state_mod.load_state(job)
        st["node"] = graph.NEEDS_HUMAN
        state_mod.save_state(st)
        (state_mod.job_dir(job) / "reaped.json").write_text(
            json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        row["action"] = "reaped"
        rows.append(row)
    return rows

