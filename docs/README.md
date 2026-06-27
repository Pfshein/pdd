# docs/ — документация проекта

Сквозная документация, не привязанная к одной директории кода. Per-directory контракты живут
рядом с кодом (`orchestrator/README.md`, `sandbox/README.md`, ...).

## Contents

| Файл | Тип | Что внутри |
|---|---|---|
| `LOOP_ENGINEERING_PROJECT.md` | backlog | Дорожная карта: карточки PDD-XX (scope/файлы/контракт/тесты) развития в loop-рантайм. |
| `STATUS.md` | snapshot | Датированный хендофф-срез сделанного и плана. **Не источник истины** — см. git за актуальным. |
| `endpoint.md` | reference | Подтверждённый контракт вызова qwen (выведен probe-скриптами из `tools/`). |
| `gate.md` | proof | Доказательство sandbox-гейта (реальный e2e в Docker без утечек). |

## Как читать

- «Как устроен проект» → [../orchestrator/README.md](../orchestrator/README.md) (архитектура,
  граф стадий) и [../README.md](../README.md) (быстрый старт).
- «Что делаем дальше» → `LOOP_ENGINEERING_PROJECT.md`.
- «Что уже сделано» → `STATUS.md` (snapshot) + git-история.
- «Как реально ведёт себя модель» → `endpoint.md`.

## Convention

Evergreen reference пишется вне времени. Time-bound доки (`STATUS.md`) помечаются как snapshot.
Не дублируй архитектуру — ссылайся на `orchestrator/README.md`.

См. также [../AGENTS.md](../AGENTS.md) — правила для моделей.
