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
- `artifacts.py` — чтение/запись артефактов + сборка промпта стадии.
- `publish.py` — коммит worktree в ветку (+ опц. push / PR через `gh`).
- `report.py` — человекочитаемый Markdown-отчёт по джобу (ASCII).
- `doctor.py` — self-check окружения.
- `run.py` — `run_pipeline` / `resume_pipeline` / `retry_pipeline`.
- `cli.py` — единая точка входа.

## CLI

```
run --job --repo --task --meta [--test-command --base-ref --drop-worktree]
status | show | diff | cleanup | publish [--push --pr --base --message] | report [--out]
resume <JOB> | retry <JOB> --stage CODER | doctor
sandbox-build | sandbox-network | sandbox-smoke | proxy-up | proxy-status | proxy-smoke
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
| **PDD-10** | doctor: self-check окружения. **Запушен, ждёт мёржа** (ветка `PDD-10-doctor`) |

**Гейт (PDD-11) доказал**: qwen уважает `HTTPS_PROXY` (модель достижима ТОЛЬКО через прокси),
egress-allowlist работает (allowed→200, denied→403), полный конвейер в Docker → DONE, без
утечек и осиротевших контейнеров. Прокси-подход рабочий — сетевой фильтр не нужен.

Тесты: **108 passed** (`python -m pytest -q`). Образ `pdd-sandbox:latest`, сеть `pdd-internal`,
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

- `run.py::_reset_job_logs` чистит только `transitions/attempts.jsonl`; `verdict.json` /
  `diff.patch` / `escalation.md` от прошлого прогона остаются (report это уже гейтит, но
  артефакты стоит чистить на старте джоба).
- `JOB_TTL_S` в конфиге есть, **reaper не подключён** (TTL-добивание зависших джоб/worktree).
- Корреляционный ID (ключ Jira) пока в путях артефактов, но нет единого структурного лог-файла.

---

# План на ближайшие задачи

### 1. PDD-DEPS — зависимости проекта в sandbox  *(самый ценный для реальных репо)*
`SETUP_COMMAND` (напр. `pip install -r requirements.txt` / `npm ci`), исполняемый в контейнере
**до** `TEST_RUN` с тем же монтированием worktree.
**Развилка (решить):** egress-allowlist пускает только хост модели, `TEST_RUN` вообще без сети
→ `pip`/`npm` к pypi/npm заблокированы. Варианты: (a) per-project образ с deps на build-time;
(b) отдельная **setup-фаза с расширенным allowlist** (pypi/npm) до основной работы агента;
(c) оффлайн-кэш/зеркало. Рекоменд.: (b) — отдельный allowlist только на setup-контейнер.
Файлы: `config.py`, `sandbox.py` (setup-сеть/allowlist), `stages.py`/`testrun.py`, тесты.

### 2. PDD-12 — hardening песочницы
seccomp/apparmor-профиль на agent-контейнер, `--security-opt seccomp=...`; **audit log** всех
`docker run`/исполнений с correlation id. (read-only/cap-drop/no-new-priv/non-root уже есть.)
Файлы: `sandbox.py`, `sandbox/seccomp.json`, тесты на argv. Docker для smoke есть.

### 3. Техдолг-гигиена: reaper + чистка артефактов на старте
Подключить TTL-reaper (`JOB_TTL_S`): добивать зависшие контейнеры/worktree; `_reset_job_logs`
расширить до очистки per-run артефактов. Файлы: `run.py`, новый `reaper.py` или в `sandbox.py`.

### 4. PDD-07 — Jira intake  *(нужен подключённый Jira MCP)*
Реальный `INTAKE` через Jira MCP: по ключу тянуть issue → нормализованные `task.md` +
`task_meta.json`; при `NEEDS_HUMAN` — драфт Jira-коммента. Промпт `prompts/intake.md` готов.
Не коммитить Jira-креды. Файлы: `stages.py::_intake`, `run.py`, новый `jira.py`.

### 5. #3 — портабельность контракта  *(нужен локальный OpenAI-эндпоинт)*
Перепрогнать `tools/probe_review.py` + `probe_limits.py` против локального сервера
(vLLM/llama.cpp/Ollama), зафиксировать дельту в `endpoint.md` (auto-classifier, форс
`structured_output`, коды). Сделать `approval-mode`/эндпоинт явно конфигурируемыми проверками.

### 6. Параллельность / очередь воркеров  *(из исходных открытых вопросов)*
Несколько джоб разом: пул воркеров + lease на джоб. worktree/sandbox уже изолируют по
correlation id. Решить хранилище состояния (файлы → возможно sqlite). Файлы: новый `queue.py`,
`run.py`, `cli.py` (`worker`/`enqueue`).

### 7. Наблюдаемость: структурный лог с correlation id
Единый JSONL-лог событий джоба (стадия, длительность, токены, sandbox-режим, exit/limit) с
ключом Jira во всех строках. Подключить к `report`. Файлы: `state.py`/новый `events.py`, `driver.py`.

### 8. End-to-end на реальном Jira-тикете → PR
Когда готовы #4(deps) и PDD-07: прогнать настоящую задачу из Jira до PR в реальном репо,
зафиксировать как воспроизводимый сценарий (расширить `docs/gate.md`).

### 9. (бэклог) settings.json/конфиг-профили, web-UI/status-дашборд, ретеншн артефактов.

**Рекомендуемый порядок:** 1 (deps) → 3 (гигиена) → 2 (hardening) → 7 (логи) → 4 (Jira) →
6 (очередь) → 8 (боевой e2e). #5 — когда появится локальный эндпоинт.

**Первый шаг для следующей сессии:** смёржить `PDD-10-doctor`, затем
`git switch main && git pull --ff-only && git switch -c PDD-DEPS-setup-command` и реализовать
задачу 1, начав с развилки про setup-сеть.
