# tools/ — probe- и demo-скрипты (вне автотестов)

Скрипты для ручной эмпирики и демонстраций. В отличие от `tests/`, часть из них **требует
живого эндпоинта/модели и сети** — поэтому они не входят в `pytest`. Запуск из корня с
`PYTHONPATH=.`.

## Purpose

Две разные вещи под одной крышей:

- **probe_*** — эмпирически прогнать *одну реальную стадию* против живой модели и зафиксировать
  её фактический контракт (то, что потом описано в [../docs/endpoint.md](../docs/endpoint.md)).
- **demo_*** — показать поток целиком (issue → run → report → publish), для проверки и маркетинга.

## Contents

| Скрипт | Что делает | Нужна живая модель/сеть |
|---|---|---|
| `probe_review.py` | Один реальный прогон стадии ревью, проверка structured-вердикта | да |
| `probe_limits.py` | Эмпирика лимитов (wall-time / tool-calls, exit 55) | да |
| `probe_sandbox_model.py` | Достижимость модели изнутри песочницы (через proxy) | да + Docker |
| `demo_e2e.py` | Чинит баг в одноразовом репо через весь конвейер до DONE | да |
| `demo_issue_to_pr.py` | Offline smoke: issue JSON → run → report → publish (stub) | нет |

## Key concepts

- Probe-скрипты — источник истины для «как реально ведёт себя модель»; их выводы переносятся в
  `docs/endpoint.md`, а не остаются только в голове.
- `demo_issue_to_pr.py` намеренно **offline** (stub-модель) — это та же идея, что планируемый
  `pdd demo --stub`: показать луп без Docker/сети.

## Invariants & gotchas

- Не тащи probe-скрипты в `pytest`: они флапают от сети/модели и стоят денег. Детерминируемую
  часть их логики покрывай юнит-тестом в `tests/`.
- Креды берутся из `.qwen/.env` через `config.model_env()` — не хардкодь и не логируй их.

## Related

- [../docs/endpoint.md](../docs/endpoint.md) — контракт qwen, выведенный probe-скриптами
- [../tests/README.md](../tests/README.md) · [../orchestrator/README.md](../orchestrator/README.md)
