# PDD — статус и план (хендофф)

Срез: 2026-06-07. Этот файл — самодостаточный вход для продолжения с нуля контекста.

## Что это

**PDD (Please Drop Database)** — self-hosted дев-пайплайн поверх форка **qwen-code**.
По задаче (`task.md` + `task_meta.json`) сам прогоняет
`INTAKE → TRIAGE → ARCHITECT → CODER → CODE_REVIEW → TESTER → TEST_RUN → FINAL_REVIEW`
с возвратами, опасные стадии — в Docker-песочнице, на выходе диффы/ветка/PR + отчёт.

Принципы: **детерминированная оркестрация** (маршрут в коде, не LLM), состояние **только
через файлы-артефакты**, агенты — **headless one-shot** вызовы qwen, **fail-closed** на
ошибках/без изоляции.

## Архитектура (модули `orchestrator/`)

- `graph.py` — узлы, таблица переходов, `CLASS_TO_STAGE`, `ORDER`. Данные.
- `router.py` — `decide_next(node, result, state)` чистая функция: маршруты, бюджеты,
  детектор «нет прогресса» (signature), лестница эскалации. LLM только классифицирует.
- `triage.py` — нужен ли первичный ARCHITECT (пороги по `task_meta`, без LLM).
- `driver.py` — главный цикл: `run_job(state, run_node)` → пока не терминал: run → route → persist.
- `stages.py` — реальный `run_node`: structured-стадии (reviewer) через `--json-schema`,
  free-form (architect) текстом, editor (coder/tester) правят worktree `isolate=True`.
- `runner.py` — спавн qwen: argv, **ключ только через env**, stdin-промпт, двойной таймаут,
  `classify_limit` (exit 55: wall-time→ретрай / tool-calls→эскалация), sandbox-маршрутизация.
- `sandbox.py` — Docker-граница: `ensure_ready` (fail-closed), `run_in_sandbox` (`--init`,
  уникальное `--name`, kill контейнера на таймауте, `--user`, `--read-only`, `--cap-drop ALL`),
  internal-сеть `--internal` + детект non-internal, squid egress-allowlist (`proxy-*`).
- `killtree.py` — kill всего дерева процессов (win/posix).
- `worktree.py` — git worktree на джоб (ветка `pdd/<job>`), дифф (без pycache), force-rmtree осиротевшего.
- `testrun.py` — `TEST_RUN` в контейнере с `--network none`.
- `verdict.py` — парс/валидация `structured_output` + `salvage_verdict` (JSON из текста),
  `verdict_signature`.
- `state.py` — `state.json` + `transitions/attempts.jsonl`, `validate_job_id`.
- `events.py` — единый структурный `events.jsonl` timeline job.
- `artifacts.py` — чтение/запись артефактов + сборка промпта стадии.
- `publish.py` — коммит worktree в ветку (+ опц. push / PR через `gh`).
- `report.py` — человекочитаемый Markdown-отчёт по джобу (ASCII).
- `doctor.py` — self-check окружения.
- `run.py` — `run_pipeline` / `resume_pipeline` / `retry_pipeline`.
- `cli.py` — единая точка входа.

## CLI

```
run --job --repo --task --meta [--setup-command --test-command --base-ref --drop-worktree]
status | show | diff | cleanup | publish [--push --pr --base --message] | report [--out]
resume <JOB> | retry <JOB> --stage CODER | reap [--apply --ttl] | doctor
intake-jira --issue issue.json --out <dir> | jira-comment-draft <JOB>
sandbox-build | sandbox-network | sandbox-smoke | proxy-up | proxy-status | proxy-smoke
setup-proxy-up | setup-proxy-status
```

## Сделано (влито в `main`, кроме отмеченного)

| Задача | Суть |
|---|---|
| PDD-01 | fail-closed core: `status:error`→NEEDS_HUMAN, валидный job_id |
| PDD-02 | sandbox runtime boundary (fail-closed), ключ через env |
| PDD-03 | CLI: run/status/show/diff/cleanup, `job_meta.json` |
| PDD-04 / 04b | Docker-образ + build/network/smoke; **kill контейнера на таймауте** (`--init`/`--name`) |
| PDD-05 / 05c | egress-allowlist (internal-сеть + squid-прокси), `TEST_RUN --network none`; **non-root** |
| #4 (PDD-14) | развод exit 55: wall-time→ретрай, tool-calls→эскалация |
| #5 (PDD-13) | free-form architect + soft JSON-fallback вердикта |
| PDD-11 | **реальный sandboxed e2e — ГЕЙТ ПРОЙДЕН** (см. `docs/gate.md`) |
| PDD-06 | publish: коммит worktree → ветка (+push/PR) |
| PDD-08 / 08b | resume/retry из `state.json`; восстановление CLI после конфликта |
| PDD-09 | report: Markdown-отчёт по джобу |
| PDD-10 | doctor: self-check окружения |
| PDD-15 | зависимости проекта в sandbox: `--setup-command` + отдельный setup proxy |
| PDD-16 | job hygiene: `reap`, TTL cleanup, fresh per-run artifacts |
| PDD-12 | sandbox hardening: opt-in seccomp + `sandbox_audit.jsonl` |
| PDD-17 | structured events: `events.jsonl` job timeline |

**Гейт (PDD-11) доказал**: qwen уважает `HTTPS_PROXY` (модель достижима ТОЛЬКО через прокси),
egress-allowlist работает (allowed→200, denied→403), полный конвейер в Docker → DONE, без
утечек и осиротевших контейнеров. Прокси-подход рабочий — сетевой фильтр не нужен.

Тесты: **126 passed** (`python -m pytest -q`). Образ `pdd-sandbox:latest`, сеть `pdd-internal`,
прокси `pdd-proxy` уже подняты на машине.

## Конвенции

- Ветки: **`PDD-XX-short-name`** (без префикса). Один маленький PR на задачу.
- Процесс: `git switch main; git pull --ff-only; git switch -c PDD-XX-...; ...; pytest; commit; push`.
- Коммит-сообщение через файл (`git commit -F`) — в нём бывают кавычки/`->`.
  Окончание: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Автор — Pfshein.
- Секрет-хук `.githooks/pre-commit` (включён `core.hooksPath`). `.qwen/.env` в `.gitignore`.
- **Грабли мёржа:** конфликт в `cli.py build_parser` уже однажды съел сабкоманды (PDD-08b чинил).
  При мёрже двух веток, трогающих CLI, — проверять, что все сабкоманды на месте.
- Эмпирика qwen — в `docs/endpoint.md`. Контракт стадии — там же.

## Открытые мелочи (техдолг)

- Корреляционный ID (ключ Jira/job id) теперь есть в `events.jsonl`; следующий шаг — использовать
  этот timeline в боевом Jira/PR flow.

---

# План на ближайшие задачи

### 1. PDD-15 — зависимости проекта в sandbox  *(готово)*
`SETUP_COMMAND` (напр. `pip install -r requirements.txt` / `npm ci`), исполняемый в контейнере
**до** `TEST_RUN` с тем же монтированием worktree.
**Решение:** отдельная setup-фаза с отдельным proxy/allowlist для package registry.
`TEST_RUN` остаётся без сети (`--network none`), agent/model proxy не расширяется.
Файлы: `config.py`, `sandbox.py` (setup-сеть/allowlist), `stages.py`/`testrun.py`, тесты.

### 2. PDD-12 — hardening песочницы  *(готово)*
Opt-in seccomp-профиль на agent/test containers через `PDD_SECCOMP_PROFILE`; **audit log** всех
sandbox-исполнений с correlation id/job artifact (`sandbox_audit.jsonl`). (read-only/cap-drop/
no-new-priv/non-root уже есть.)
Файлы: `sandbox.py`, `sandbox/seccomp.json`, `report.py`, тесты на argv/audit.

### 3. PDD-16 — техдолг-гигиена: reaper + чистка артефактов на старте  *(готово)*
Подключить TTL-reaper (`JOB_TTL_S`): помечать зависшие non-terminal job как `NEEDS_HUMAN`,
убирать worktree; `_reset_job_logs` расширить до очистки per-run артефактов.
Файлы: `run.py`, новый `reaper.py`, `cli.py`, тесты.

### 4. PDD-07 — Jira intake  *(в работе)*
Jira adapter boundary: нормализовать issue JSON → `task.md` + `task_meta.json`; при
`NEEDS_HUMAN` — драфт Jira-коммента. Реальный Jira MCP позже должен только поставлять issue JSON.
Не коммитить Jira-креды. Файлы: новый `jira.py`, `cli.py`, тесты.

### 5. #3 — портабельность контракта  *(нужен локальный OpenAI-эндпоинт)*
Перепрогнать `tools/probe_review.py` + `probe_limits.py` против локального сервера
(vLLM/llama.cpp/Ollama), зафиксировать дельту в `endpoint.md` (auto-classifier, форс
`structured_output`, коды). Сделать `approval-mode`/эндпоинт явно конфигурируемыми проверками.

### 6. Параллельность / очередь воркеров  *(из исходных открытых вопросов)*
Несколько джоб разом: пул воркеров + lease на джоб. worktree/sandbox уже изолируют по
correlation id. Решить хранилище состояния (файлы → возможно sqlite). Файлы: новый `queue.py`,
`run.py`, `cli.py` (`worker`/`enqueue`).

### 7. PDD-17 — наблюдаемость: структурный лог с correlation id  *(готово)*
Единый JSONL-лог событий джоба (старт/конец run, стадия, длительность, transition, status/limit,
sandbox summary) с ключом job/Jira во всех строках. Подключить к `report`.
Файлы: новый `events.py`, `driver.py`, `run.py`, `report.py`, тесты.

### 8. End-to-end на реальном Jira-тикете → PR
Когда готовы #4(deps) и PDD-07: прогнать настоящую задачу из Jira до PR в реальном репо,
зафиксировать как воспроизводимый сценарий (расширить `docs/gate.md`).

### 9. (бэклог) settings.json/конфиг-профили, web-UI/status-дашборд, ретеншн артефактов.

**Рекомендуемый порядок:** 4 (Jira) →
6 (очередь) → 8 (боевой e2e). #5 — когда появится локальный эндпоинт.

**Первый шаг для следующей сессии:** `git switch main && git pull --ff-only &&
git switch -c PDD-07-jira-intake-adapter` и реализовать задачу 4.
