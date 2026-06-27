# PDD Loop Engineering Project

Цель документа: превратить PDD из single-job dev pipeline в понятный self-hosted
Loop Engineering runtime, который могут развивать автономные модели уровня qwen
3.x/3.6 через маленькие, проверяемые задачи.

Документ намеренно написан как backlog для моделей: каждая карточка должна иметь
маленький scope, список файлов, контракт, критерии приемки и тесты. Если задача
не помещается в такой формат, ее надо сначала разделить.

## Numbering Note

Номера карточек ниже начинаются с `PDD-24`. Диапазон `PDD-19..PDD-23` уже занят
смерженными PR-ами с другим содержанием (live progress, `pdd` entrypoint,
interactive menu) и веткой `PDD-21-publish-fix`, поэтому повторно их использовать
нельзя — иначе ветка/коммит-префикс `PDD-XX` означали бы две разные вещи в одной
истории. Раньше этот backlog нумеровался с `PDD-19`; нумерация сдвинута, чтобы
ветки `PDD-XX-...` не конфликтовали.

Бывший `PDD-19 Python Bootstrap Contract` закрыт как фактически готовый: `doctor`
уже проверяет host Python, импортируемость pytest, git, docker и sandbox-стек.
Единственный остаток (детект битого `.venv`) вынесен в Backlog Parking Lot.

## North Star

PDD должен стать системой:

```text
Issue or task
  -> intake
  -> deterministic loop
  -> isolated code/test worktree
  -> review and test gates
  -> DONE with PR/report
     or NEEDS_HUMAN with precise handoff
```

Главное отличие от обычного agent runner: PDD проектирует сам цикл. LLM не
решает, куда идти дальше. LLM выполняет роль в рамках стадии, а deterministic
control plane управляет маршрутом, бюджетами, повторами, безопасностью,
артефактами и остановкой.

## Product Pillars

1. Deterministic loops
   - graph and router in code
   - explicit stage budgets
   - no-progress detection
   - terminal reasons are machine-readable

2. Safe execution
   - worktree per job
   - sandbox for stages that can execute code
   - no direct egress for tests
   - audit artifacts for every unsafe override

3. Observable automation
   - structured event log
   - attempts and transitions
   - cost/time/tool-call summaries
   - report for humans and machines

4. Product loop closure
   - issue intake
   - autonomous run
   - publish branch/PR
   - needs-human comment or handoff

5. Model-friendly development
   - small tasks
   - local tests
   - stable contracts
   - no broad refactors without a separate design task

## Rules For Agent Implementers

Use these rules in every task prompt for qwen/Codex-style models.

- Make the smallest change that satisfies the task.
- Prefer plain functions and data structures, matching the current codebase.
- Do not change public CLI behavior unless the task explicitly says so.
- Preserve existing artifacts and file formats unless a migration is specified.
- Add tests for every new routing, persistence, CLI, or report behavior.
- Do not touch sandbox security defaults unless the task is explicitly about
  sandbox behavior.
- Never read or write secrets into artifacts, argv, logs, reports, or tests.
- If a task needs network, Docker, GitHub CLI, Jira, or model credentials, add a
  deterministic unit test with stubs first.
- If the model sees unrelated dirty files, it must ignore them.

## Definition Of Done For Any Task

A task is done only when:

- the requested behavior is implemented;
- focused tests are added or updated;
- the CLI/help/report text is updated if the user-facing contract changed;
- error cases are deterministic and readable;
- artifacts are documented if a new artifact is introduced;
- the change does not require real model/network/Docker access to test its core.

## Milestone 0: Bootstrap And Developer Reliability

Goal: make local and agent development predictable before adding more loop
features.

Python Bootstrap Contract (`doctor` onboarding self-check) is already in place;
see Numbering Note above. The only remaining card in this milestone is test
command portability.

### PDD-24: Test Command Portability

Objective: make internal and target-repo test command behavior explicit.

Files:

- `orchestrator/config.py`
- `orchestrator/testrun.py`
- `orchestrator/run.py`
- `README.md`

Implementation notes:

- Keep default `python -m pytest -q`.
- Ensure job metadata records the exact test command.
- Ensure reports show setup/test commands.
- Add docs explaining host tests vs target repo tests in sandbox.
- Note: `--setup-command` already exists (PDD-15); verify whether `setup_command`
  is already surfaced in the report before adding new plumbing.

Acceptance criteria:

- Report shows `setup_command` if present.
- A red setup phase is distinguishable from red tests.
- Existing `TEST_RUN --network none` invariant remains unchanged.

Suggested tests:

- `tests/test_report.py`
- `tests/test_sandbox.py`

## Milestone 1: Queue And Workers

Goal: PDD becomes a loop runtime, not only a one-shot command.

### PDD-25: Queue Storage V1

Objective: add a minimal durable job queue using files under `runs/queue`.

Files:

- new `orchestrator/queue.py`
- `orchestrator/config.py`
- `tests/test_queue.py`

Data model:

```json
{
  "job": "DEMO-1",
  "repo": "/abs/path/to/repo",
  "task": "/abs/path/to/task.md",
  "meta": "/abs/path/to/task_meta.json",
  "base_ref": "HEAD",
  "test_command": null,
  "setup_command": null,
  "status": "queued",
  "created_ts": 0.0,
  "updated_ts": 0.0,
  "lease": null
}
```

Allowed statuses:

- `queued`
- `leased`
- `running`
- `done`
- `needs_human`
- `failed`

Implementation notes:

- Use atomic-ish file writes: write temp file then replace.
- Validate job ids through `state.validate_job_id`.
- Keep queue records JSON and human-readable.
- Do not introduce SQLite yet.

Acceptance criteria:

- enqueue writes one queue record.
- list returns records sorted by created time.
- acquire returns the oldest queued job and writes a lease token.
- release can mark final status.
- stale leases can be detected by timestamp.

Suggested tests:

- enqueue creates a record
- acquire skips already leased records
- stale lease returns to queued
- invalid job id is rejected

### PDD-26: CLI Enqueue/List

Objective: expose queue storage through CLI without running jobs yet.

Files:

- `orchestrator/cli.py`
- `orchestrator/queue.py`
- `tests/test_cli.py`
- `README.md`

CLI:

```text
pdd enqueue --job JOB --repo REPO --task task.md --meta task_meta.json
pdd queue
pdd queue --json
```

Acceptance criteria:

- `enqueue` validates inputs and prints the queued job id.
- `queue` prints a compact table.
- `queue --json` prints machine-readable records.
- No worker is started by this task.

Suggested tests:

- parser includes commands
- enqueue writes queue record
- queue prints queued job

### PDD-27: Single Worker Loop

Objective: add a worker command that processes queued jobs one at a time.

Files:

- new `orchestrator/worker.py`
- `orchestrator/cli.py`
- `orchestrator/queue.py`
- `orchestrator/run.py`
- `tests/test_worker.py`

CLI:

```text
pdd worker --once
pdd worker --poll-interval 5
```

Behavior:

- acquire one queued job;
- mark it running;
- call `run_pipeline`;
- mark `done` or `needs_human` based on final node;
- mark `failed` only for infrastructure exceptions;
- write worker events into the existing job `events.jsonl`.

Acceptance criteria:

- `worker --once` exits 0 if there is no work.
- `worker --once` processes one job.
- exceptions do not leave the job permanently leased.
- final queue status matches final pipeline node.

Suggested tests:

- no work returns cleanly
- successful stub pipeline marks done
- needs-human stub marks needs_human
- exception marks failed and stores error summary

### PDD-28: Lease TTL And Reaper Integration

Objective: make workers robust after crashes.

Files:

- `orchestrator/queue.py`
- `orchestrator/reaper.py`
- `orchestrator/cli.py`
- `tests/test_queue.py`
- `tests/test_reaper.py`

Behavior:

- `pdd queue-reap` or existing `pdd reap` also handles stale queue leases.
- stale `leased` or `running` records become `queued` or `failed` depending on
  whether a job state exists and is terminal.

Acceptance criteria:

- stale queued work can be picked up again.
- terminal job state updates queue final status.
- reaper output includes queue actions.

## Milestone 2: Loop Budgets And Stop Reasons

Goal: make loop stops precise enough for automation and marketing demos.

### PDD-29: Machine-Readable Stop Reasons

Objective: add explicit terminal reason fields to state and reports.

Files:

- `orchestrator/router.py`
- `orchestrator/driver.py`
- `orchestrator/state.py`
- `orchestrator/report.py`
- `tests/test_router.py`
- `tests/test_report.py`

State addition:

```json
{
  "terminal_reason": "done | stage_error | global_step_cap | no_progress | budget_exhausted | unknown"
}
```

Implementation notes:

- Preserve existing `node` behavior.
- Add reason without breaking old state files.
- Router should return stable reason strings or reason codes.

Acceptance criteria:

- DONE has `terminal_reason=done`.
- no-progress stop is distinguishable from budget exhausted.
- report shows terminal reason near outcome.

### PDD-30: Per-Stage Budget Summary

Objective: make budgets visible as product data.

Files:

- `orchestrator/report.py`
- `orchestrator/progress.py`
- `tests/test_report.py`
- `tests/test_progress.py`

Behavior:

- report shows used/max for ARCHITECT, CODER, TESTER.
- live progress line includes budget usage when entering a return target.

Acceptance criteria:

- report includes a `Loop budget` section.
- output remains ASCII-safe.

### PDD-31: Loop Policy Profiles

Objective: support named budget profiles without changing code.

Files:

- `orchestrator/config.py`
- `orchestrator/state.py`
- `orchestrator/run.py`
- `orchestrator/cli.py`
- `tests/test_state.py`
- `tests/test_cli.py`

CLI:

```text
pdd run --loop-profile conservative
pdd run --loop-profile standard
pdd run --loop-profile aggressive
```

Profiles:

- `conservative`: fewer retries, lower global cap
- `standard`: current defaults
- `aggressive`: more retries, higher cap

Acceptance criteria:

- profile is recorded in `job_meta.json`.
- unknown profile is rejected by argparse or validation.
- default behavior remains current standard.

## Milestone 3: Cost And Usage Telemetry

Goal: loops must know how expensive they are.

### PDD-32: Usage Extraction From Qwen Events

Objective: extract token/usage fields from qwen JSON events when present.

Files:

- new `orchestrator/usage.py`
- `orchestrator/runner.py`
- `orchestrator/stages.py`
- `tests/test_usage.py`

Artifact:

- `usage.jsonl`

Record shape:

```json
{
  "ts": 0.0,
  "job": "DEMO-1",
  "stage": "CODER",
  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "source": "qwen_event"
}
```

Implementation notes:

- Do not fail a stage because usage is missing.
- Support multiple possible field names with conservative parsing.
- Do not guess cost yet.

Acceptance criteria:

- if qwen output has usage, `usage.jsonl` receives a row.
- if usage is absent, nothing fails.

### PDD-33: Cost Estimation Config

Objective: estimate cost from usage with configurable rates.

Files:

- `orchestrator/config.py`
- `orchestrator/usage.py`
- `orchestrator/report.py`
- `tests/test_usage.py`
- `tests/test_report.py`

Config:

```text
PDD_MODEL_INPUT_PRICE_PER_1M
PDD_MODEL_OUTPUT_PRICE_PER_1M
```

Acceptance criteria:

- report shows total estimated cost when rates and usage are present.
- report clearly says estimate if not exact.
- missing rates do not produce bogus `$0.00`.

### PDD-34: Cost Budget Stop

Objective: allow a job to stop before spending too much.

Files:

- `orchestrator/config.py`
- `orchestrator/driver.py`
- `orchestrator/usage.py`
- `orchestrator/state.py`
- `tests/test_driver_stub.py`
- `tests/test_usage.py`

Behavior:

- optional max cost per job.
- driver checks cost after each stage.
- exceeding cost moves to `NEEDS_HUMAN` with stop reason `cost_budget_exhausted`.

Acceptance criteria:

- disabled by default.
- deterministic tests do not require real usage data.

## Milestone 4: Product Loop Closure

Goal: make PDD close the loop around Jira/GitHub and PRs.

### PDD-35: Issue Source Boundary

Objective: define a pure interface for issue providers.

Files:

- new `orchestrator/issues.py`
- `orchestrator/jira.py`
- `tests/test_issues.py`

Interface:

```python
def normalize_issue_payload(provider: str, payload: dict) -> tuple[str, dict]:
    ...
```

Implementation notes:

- Start with `provider="jira"`.
- Do not call network.
- Keep current `jira.normalize_issue` behavior.

Acceptance criteria:

- Jira JSON still works.
- unsupported provider returns clear error.

### PDD-36: GitHub Issue Intake From JSON

Objective: support GitHub issue JSON as another offline provider.

Files:

- `orchestrator/issues.py`
- `orchestrator/cli.py`
- `tests/test_issues.py`
- `tests/test_cli.py`

CLI:

```text
pdd intake-issue --provider github --issue issue.json --out .pdd-intake/GH-1
```

Acceptance criteria:

- converts `title`, `body`, `labels`, `number` into task/meta.
- no GitHub API access.

### PDD-37: Auto Publish On DONE

Objective: allow queue worker to publish successful jobs.

Files:

- `orchestrator/worker.py`
- `orchestrator/publish.py`
- `orchestrator/queue.py`
- `tests/test_worker.py`

CLI:

```text
pdd worker --once --publish
pdd worker --once --publish --push
```

Acceptance criteria:

- publish is opt-in.
- publish result is written to `publish.json`.
- worker queue status includes publish failure without losing DONE state.

### PDD-38: Needs-Human Handoff Artifact

Objective: produce a concise handoff artifact for issue comments.

Files:

- `orchestrator/run.py`
- `orchestrator/jira.py`
- `orchestrator/report.py`
- `tests/test_jira.py`
- `tests/test_report.py`

Artifact:

- `handoff.md`

Content:

- stopped stage
- stop reason
- last verdict summary
- last red test tail
- exact next human action if known

Acceptance criteria:

- `handoff.md` is created for `NEEDS_HUMAN`.
- Jira comment draft prefers `handoff.md` over full report.

## Milestone 5: Loop Recipes

Goal: let users choose an intent-specific loop instead of one graph for all work.

### PDD-39: Recipe Metadata

Objective: store recipe name in job metadata and state.

Files:

- `orchestrator/run.py`
- `orchestrator/state.py`
- `orchestrator/cli.py`
- `tests/test_run_hygiene.py`
- `tests/test_cli.py`

CLI:

```text
pdd run --recipe bugfix
```

Initial recipes:

- `bugfix`
- `test-generation`
- `docs-update`

Acceptance criteria:

- recipes are validated.
- current behavior maps to `bugfix`.
- recipe appears in report.

### PDD-40: Recipe-Specific Prompts

Objective: allow prompts to vary by recipe without duplicating code.

Files:

- `orchestrator/artifacts.py`
- `orchestrator/stages.py`
- `orchestrator/prompts/`
- `tests/test_stages.py`

Design:

```text
prompts/
  coder.md
  tester.md
  recipes/
    test-generation/coder.md
    docs-update/reviewer.md
```

Resolution:

- use recipe-specific prompt if present;
- otherwise fallback to default role prompt.

Acceptance criteria:

- fallback keeps current behavior.
- tests prove recipe override is used.

### PDD-41: Test-Generation Recipe

Objective: create a loop optimized for adding missing tests.

Behavior:

- skip ARCHITECT unless task is complex;
- tester is the main editor;
- reviewer treats weak tests as blocking;
- coder only enters if tests reveal required code changes.

Files:

- `orchestrator/graph.py`
- `orchestrator/router.py`
- `orchestrator/stages.py`
- `orchestrator/prompts/recipes/test-generation/`
- `tests/test_router.py`

Acceptance criteria:

- recipe can route first editor stage to TESTER.
- default bugfix graph is unchanged.

## Milestone 6: Evals And Demo

Goal: show that PDD loops work and do not regress.

### PDD-42: Fixture Task Suite

Objective: create a small offline eval suite of target repos and tasks.

Files:

- new `evals/`
- new `orchestrator/eval.py`
- `tests/test_eval.py`

Suite format:

```json
{
  "name": "simple-python-bug",
  "repo_fixture": "python_calc",
  "task": "Fix add()",
  "expected_files": ["calc.py"],
  "test_command": "python -m pytest -q"
}
```

Acceptance criteria:

- eval suite can run with stubbed model in tests.
- records success/failure per task.

### PDD-43: Eval CLI

Objective: expose evals through CLI.

CLI:

```text
pdd eval list
pdd eval run --suite smoke --stub
```

Files:

- `orchestrator/cli.py`
- `orchestrator/eval.py`
- `tests/test_cli.py`

Acceptance criteria:

- `--stub` does not call real model.
- results are written to `runs/evals/<timestamp>/results.json`.

### PDD-44: One-Command Demo

Objective: create a demo command that proves issue -> run -> report -> publish.

CLI:

```text
pdd demo --stub
```

Files:

- new `orchestrator/demo.py`
- `orchestrator/cli.py`
- `tools/demo_issue_to_pr.py`
- `tests/test_cli.py`

Acceptance criteria:

- demo creates a temporary fixture repo.
- demo runs without network/model when `--stub` is set.
- demo prints paths to report and worktree.

### PDD-45: Product README Rewrite

Objective: make the project understandable as a Loop Engineering runtime.

Files:

- `README.md`
- `docs/STATUS.md`
- optional `docs/ARCHITECTURE.md`

README first screen:

- what PDD is
- why deterministic loops
- quick demo
- safety model
- artifact model
- roadmap link to this document

Acceptance criteria:

- README explains the loop in under one minute.
- setup/run/demo commands are current.
- old low-level details move to docs if needed.

## Milestone 7: Dashboard

Goal: make the loop visible without reading JSONL by hand.

### PDD-46: Static HTML Report Index

Objective: generate an HTML index from existing run artifacts.

Files:

- new `orchestrator/dashboard.py`
- `orchestrator/cli.py`
- `tests/test_dashboard.py`

CLI:

```text
pdd dashboard --out runs/index.html
```

Acceptance criteria:

- no web server required.
- shows jobs, final nodes, steps, stop reasons, report links.
- safe HTML escaping.

### PDD-47: Live Local Dashboard

Objective: optional local status server for active workers.

Files:

- new `orchestrator/server.py`
- `orchestrator/cli.py`
- `tests/test_server.py`

CLI:

```text
pdd serve --host 127.0.0.1 --port 8765
```

Acceptance criteria:

- read-only.
- no dependency on external services.
- can serve events and reports.

## Milestone 8: Parallel Candidates

Goal: explore the more viral loop pattern: multiple candidates compete, review
chooses the best.

### PDD-48: Candidate Worktrees

Objective: allow one job to spawn N candidate worktrees for CODER.

Files:

- `orchestrator/worktree.py`
- new `orchestrator/candidates.py`
- `tests/test_worktree.py`
- `tests/test_candidates.py`

Design:

```text
%TEMP%/pdd-worktrees/JOB/
  candidate-1/
  candidate-2/
```

Acceptance criteria:

- existing single-worktree behavior remains default.
- candidate worktree path validates job and candidate id.

### PDD-49: Candidate Review Selection

Objective: run review over multiple candidate diffs and select one.

Files:

- `orchestrator/stages.py`
- `orchestrator/verdict.py`
- `orchestrator/candidates.py`
- `tests/test_stages.py`

Acceptance criteria:

- selection can be tested with stubbed reviewers.
- selected candidate is copied or promoted to the canonical worktree.
- losing candidates are retained as artifacts until cleanup.

### PDD-50: Cheap/Expensive Model Routing

Objective: support different models for different stages.

Files:

- `orchestrator/config.py`
- `orchestrator/runner.py`
- `orchestrator/stages.py`
- `tests/test_runner.py`

Config:

```text
PDD_MODEL_CODER
PDD_MODEL_REVIEWER
PDD_MODEL_ARCHITECT
```

Acceptance criteria:

- defaults preserve current `OPENAI_MODEL`.
- argv uses stage-specific model when configured.
- no secrets in argv.

## Suggested Execution Order

Recommended order for the next development sessions:

1. PDD-25 Queue Storage V1
2. PDD-26 CLI Enqueue/List
3. PDD-27 Single Worker Loop
4. PDD-29 Machine-Readable Stop Reasons
5. PDD-32 Usage Extraction From Qwen Events
6. PDD-38 Needs-Human Handoff Artifact
7. PDD-44 One-Command Demo
8. PDD-45 Product README Rewrite

This sequence gives the fastest product jump: real runtime loop, explainable
stops, cost visibility, and a demo people can run. (Test Command Portability,
PDD-24, is a small optional warm-up if `setup_command` is not yet in the report.)

## Prompt Template For A Single Task

Use this when assigning one task to a model.

```text
You are working in the PDD repository.

Task: <PDD-XX title>

Goal:
<one paragraph objective>

Files likely involved:
- <file>
- <file>

Constraints:
- Keep the change small.
- Preserve existing behavior unless explicitly changed.
- Add or update focused tests.
- Do not call network, Docker, Jira, GitHub, or real model in tests.
- Do not refactor unrelated code.

Implementation details:
<copy the card details from docs/LOOP_ENGINEERING_PROJECT.md>

Acceptance criteria:
<copy checklist>

Verification:
- Run focused tests for touched behavior.
- If full test suite cannot run, say why.
```

## Backlog Parking Lot

Ideas that are useful but should not block the main loop runtime:

- Broken `.venv` detection in `doctor` (leftover from the closed Python Bootstrap
  card): detect a stale checked-in `.venv` and show a direct fix.
- SQLite queue backend after file queue proves useful.
- Webhook receiver for GitHub/Jira.
- Retention policy for old runs and worktrees.
- Signed audit summaries.
- Mermaid graph export for the current pipeline.
- Prompt versioning in job metadata.
- Per-repo config file such as `.pdd.json`.
- Model quality leaderboard from eval results.
- Human approval gate before publish.
- Policy checks for forbidden file edits.
