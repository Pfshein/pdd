# PDD — Please Drop Database

Автоматизированный дев-пайплайн поверх форка **qwen code**: по ссылке на Jira-таску сам ведёт
разработку до готового результата через многостадийный прогон
**архитектор → кодер → ревьюер → тестировщик → ревьюер** с возвратами на предыдущие стадии.

Оркестрация — **детерминированный код**, не LLM-оркестратор: граф состояний и маршруты
известны заранее (наблюдаемо, тестируемо, восстанавливаемо). Состояние между стадиями — только
через артефакты на диске; агенты — headless one-shot вызовы qwen (живых сессий нет → нет зомби).

> Имя — ирония: пайплайн запускает unattended-агентов с авто-аппрувом, поэтому вся защита от
> «please drop database» вынесена в песочницу, worktree-изоляцию и стадию ревью, а не в человека
> у терминала.

## Раскладка

```
orchestrator/      # control plane (простые функции + данные, без ООП)
  graph.py         # узлы, таблица переходов, класс→стадия — данные
  router.py        # decide_next() — чистая функция: маршруты, бюджеты, эскалация
  triage.py        # детерминированный триаж: нужен ли первичный архитектор
  state.py         # state.json + transitions/attempts.jsonl
  driver.py        # главный цикл: run node → route → persist
  runner.py        # спавн qwen-стадии: argv, stdin-промпт, двойной таймаут
  killtree.py      # kill всего дерева процессов (win+posix)
  verdict.py       # парсинг/валидация структурного вывода qwen → вердикт
  config.py        # бюджеты, таймауты, креды модели
  schemas/         # JSON-схемы для --json-schema (verdict, task_meta)
stubs/             # фейковый qwen для тестов графа и kill-tree
tools/probe_review.py  # эмпирический прогон одной реальной стадии
tests/             # pytest: граф, kill-tree, парсер вердикта
docs/endpoint.md   # подтверждённый контракт вызова qwen
.qwen/.env         # креды модели (в .gitignore — НЕ коммитится)
```

## Статус

- [x] Фаза 0-1 — детерминированное ядро машины состояний + юнит-тесты
- [x] Фаза 2 — рантайм процессов + kill_tree (защита от зависаний)
- [x] Фаза 3 — артефакты + сборка промптов стадий
- [x] Фаза 4 — git worktree на джоб + дифф
- [x] Фаза 5 — реальный контракт qwen-стадии подтверждён живым прогоном
- [x] Фаза 6 — полный end-to-end на фикстуре доходит до DONE на живой модели
- [ ] Jira-замыкание — INTAKE через Jira MCP + коммент при `needs-human`
      (готов промпт `prompts/intake.md`; нужен подключённый Jira MCP-сервер)

Прогон демо: `PYTHONPATH=. python tools/demo_e2e.py` — чинит баг в одноразовом
репозитории через весь конвейер (INTAKE → TRIAGE → CODER → CODE_REVIEW → TESTER →
TEST_RUN → FINAL_REVIEW → DONE).

## Запуск

```bash
python -m pytest -q                         # тесты ядра, kill-tree, парсера
```

Реальный пользовательский поток:

```bash
python -m orchestrator.cli intake-jira --issue issue.json --out .pdd-intake/DEMO-1
python -m orchestrator.cli run --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json
python -m orchestrator.cli status DEMO-1
python -m orchestrator.cli show DEMO-1
python -m orchestrator.cli diff DEMO-1
python -m orchestrator.cli reap                 # dry-run stale job cleanup
python -m orchestrator.cli cleanup DEMO-1
```

`intake-jira` работает с issue JSON, полученным любым способом (Jira export/MCP/REST), и пишет
`task.md` + `task_meta.json`. Боевой Jira MCP позже должен только заменить источник JSON, а не
формат артефактов.

Для репозиториев, где перед тестами нужно поставить зависимости, используйте отдельную
setup-фазу:

```bash
python -m orchestrator.cli setup-proxy-up
python -m orchestrator.cli run --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json \
  --setup-command "pip install -r requirements.txt" \
  --test-command "python -m pytest -q"
```

`--setup-command` выполняется в контейнере до `TEST_RUN`. У этой фазы отдельный proxy/allowlist
для package registry, а сам `TEST_RUN` по-прежнему запускается с `--network none`.

`run` оставляет за собой:

```text
runs/<JOB>/              # state, transitions, attempts, plan, diff, verdict, tests
%TEMP%/pdd-worktrees/<JOB>  # рабочий git worktree задачи
```

`events.jsonl` внутри `runs/<JOB>/` — единый структурный timeline job: старт/конец run,
старт/конец стадий, transition, duration и короткий summary результата.

Docker — внутренняя граница исполнения для опасных стадий (`CODER`, `TESTER`, `TEST_RUN`),
а не место, куда пользователь должен заходить руками. Оркестратор, маршрутизация,
артефакты и CLI остаются на хосте.

Эмпирический прогон одной реальной стадии:

```bash
PYTHONPATH=. python tools/probe_review.py   # одна реальная стадия ревью на модели
```

Подготовка Docker sandbox:

```bash
python -m orchestrator.cli sandbox-build
python -m orchestrator.cli sandbox-network
python -m orchestrator.cli sandbox-smoke <worktree-or-temp-dir>
python -m orchestrator.cli proxy-up
python -m orchestrator.cli setup-proxy-up
```

Опциональный seccomp-профиль для agent/test containers:

```bash
PDD_SECCOMP_PROFILE=sandbox/seccomp.json python -m orchestrator.cli run --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json
```

Каждый sandbox-запуск с привязкой к job пишет `sandbox_audit.jsonl` в артефакты job и попадает
в `report`.

На Windows лучше закрепить проектный интерпретатор через venv, чтобы не зависеть от
WindowsApps/PATH alias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip pytest jsonschema
python -m pytest -q
```

Креды модели — в `.qwen/.env` (OpenAI-совместимый эндпоинт). Подробности контракта вызова
qwen — в [`docs/endpoint.md`](docs/endpoint.md), архитектура — в плане проекта.
