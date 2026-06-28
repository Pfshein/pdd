# PDD — Please Drop Database

Self-hosted **Loop Engineering**-рантайм поверх форка **qwen code**: по задаче
(`task.md` + `task_meta.json`, в т.ч. из Jira/GitHub issue) сам ведёт разработку до
готового результата через многостадийный прогон
**INTAKE → TRIAGE → ARCHITECT → CODER → CODE_REVIEW → TESTER → TEST_RUN → FINAL_REVIEW**
с возвратами на предыдущие стадии.

Главное: **цикл проектирует код, а не LLM**. Граф состояний и маршруты известны заранее
(наблюдаемо, тестируемо, восстанавливаемо); LLM исполняет роль внутри стадии, а `router`
решает, куда идти дальше, сколько бюджета осталось и когда остановиться. Состояние между
стадиями — только через артефакты на диске; агенты — headless one-shot вызовы qwen
(живых сессий нет → нет зомби). Опасные стадии исполняются в Docker-песочнице, fail-closed.

> Имя — ирония: пайплайн запускает unattended-агентов с авто-аппрувом, поэтому вся защита от
> «please drop database» вынесена в песочницу, worktree-изоляцию и стадию ревью, а не в человека
> у терминала.

Архитектура и карта модулей — в [orchestrator/README.md](orchestrator/README.md).
Дорожная карта развития в Loop Engineering-рантайм — в
[docs/LOOP_ENGINEERING_PROJECT.md](docs/LOOP_ENGINEERING_PROJECT.md).

## Раскладка

```text
orchestrator/   # control plane: граф, router, стадии, sandbox-маршрутизация, CLI
  prompts/      # промпты ролей (контракт стадии)
  schemas/      # JSON-схемы для --json-schema (verdict, plan, task_meta)
sandbox/        # Docker-образ + egress-proxy (граница исполнения опасных стадий)
tools/          # probe-скрипты (живой эндпоинт) + offline demo-скрипты
stubs/          # детерминированный фейковый qwen для тестов
tests/          # pytest: граф, router, sandbox-argv, очередь, отчёт, ...
docs/           # STATUS (snapshot), endpoint-контракт, gate, backlog
.qwen/.env      # креды модели (в .gitignore — НЕ коммитится)
```

У каждой директории есть свой `README.md` с контрактом и инвариантами.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .        # ставит консольную команду `pdd`
pdd doctor                        # self-check окружения (python/git/qwen/docker/sandbox)
```

Требуется Python >= 3.12. Креды модели — в `.qwen/.env` (OpenAI-совместимый эндпоинт);
контракт вызова qwen — в [`docs/endpoint.md`](docs/endpoint.md).

## Запуск

```powershell
pytest -q          # тесты ядра (без сети/Docker/модели)
pdd                # без аргументов — интерактивное arrow-key меню
```

Пользовательский поток (всё доступно и как `python -m orchestrator.cli <cmd>`):

```powershell
pdd intake-jira --issue issue.json --out .pdd-intake/DEMO-1
pdd run --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json
pdd status DEMO-1
pdd show DEMO-1
pdd diff DEMO-1
pdd report DEMO-1
pdd reap            # dry-run чистки зависших джоб
pdd cleanup DEMO-1
```

`intake-jira` работает с issue JSON, полученным любым способом (Jira export/MCP/REST), и пишет
`task.md` + `task_meta.json`. Боевой Jira MCP позже должен только заменить источник JSON, а не
формат артефактов.

### Очередь (queue)

Джоб можно поставить в durable-очередь, не запуская сразу, и затем обработать воркером:

```powershell
pdd enqueue --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json
pdd queue            # компактная таблица
pdd queue --json     # машиночитаемые записи

pdd worker --once               # взять один джоб из очереди, прогнать, выйти
pdd worker --poll-interval 5    # крутиться, опрашивая очередь раз в N секунд
```

Записи лежат по одной на джоб в `runs/queue/<job>.json`. `enqueue` валидирует пути и печатает
id поставленного джоба; повторная постановка ещё активного джоба отклоняется. `worker` берёт
старейший `queued`-джоб, помечает `running`, зовёт `run_pipeline` и выставляет финальный статус
(`done`/`needs_human`); infra-исключение → `failed` (джоб не остаётся залоченным). Финал воркера
пишется в `events.jsonl` джоба.

Для репозиториев, где перед тестами нужно поставить зависимости, — отдельная setup-фаза:

```powershell
pdd setup-proxy-up
pdd run --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json `
  --setup-command "pip install -r requirements.txt" `
  --test-command "python -m pytest -q"
```

`--setup-command` выполняется в контейнере до `TEST_RUN`. У этой фазы отдельный proxy/allowlist
для package registry, а сам `TEST_RUN` по-прежнему запускается с `--network none`.

`run` оставляет за собой:

```text
runs/<JOB>/                 # state, transitions, attempts, plan, diff, verdict, tests, events
%TEMP%/pdd-worktrees/<JOB>  # рабочий git worktree задачи
```

`events.jsonl` внутри `runs/<JOB>/` — единый структурный timeline job: старт/конец run,
старт/конец стадий, transition, duration и короткий summary результата.

## Песочница

Docker — внутренняя **граница исполнения** опасных стадий (`CODER`, `TESTER`, `TEST_RUN`),
а не место, куда пользователь заходит руками. Оркестратор, маршрутизация, артефакты и CLI
остаются на хосте. Подробности и инварианты — в [sandbox/README.md](sandbox/README.md).

```powershell
pdd sandbox-build
pdd sandbox-network
pdd sandbox-smoke <worktree-or-temp-dir>
pdd proxy-up
pdd setup-proxy-up
```

Опциональный seccomp-профиль для agent/test containers:

```powershell
$env:PDD_SECCOMP_PROFILE="sandbox/seccomp.json"; pdd run --job DEMO-1 --repo <repo> --task task.md --meta task_meta.json
```

Каждый sandbox-запуск с привязкой к job пишет `sandbox_audit.jsonl` в артефакты job и попадает
в `report`. Для доверенного локального дебага без Docker есть громкий opt-out
`PDD_ALLOW_UNSANDBOXED=1` (пишет `SECURITY.txt`).

## Демо и probe-скрипты

```powershell
$env:PYTHONPATH="."; python tools/demo_e2e.py          # фикс бага в одноразовом репо через весь конвейер
$env:PYTHONPATH="."; python tools/demo_issue_to_pr.py  # offline issue JSON -> run -> report -> publish smoke
$env:PYTHONPATH="."; python tools/probe_review.py      # одна реальная стадия ревью на живой модели
```

## Статус

Детерминированное ядро, sandbox-граница, артефакты, worktree-изоляция, publish/PR и offline
issue→report→publish smoke — реализованы и покрыты тестами (`pytest -q`). Развитие из
single-job команды в полноценный loop-рантайм (очередь + воркеры, машиночитаемые stop-reasons,
cost-телеметрия, демо, parallel candidates) ведётся по карточкам
[docs/LOOP_ENGINEERING_PROJECT.md](docs/LOOP_ENGINEERING_PROJECT.md). Срез по сделанному —
[docs/STATUS.md](docs/STATUS.md) (snapshot, см. git за актуальным).
