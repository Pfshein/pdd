# orchestrator/prompts/ — контракты ролей стадий

Промпт = контракт роли. Поведение стадии описано **здесь**, а не зашито в код: чтобы изменить,
как ведёт себя архитектор/кодер/ревьюер, правь соответствующий `.md`, а не Python.

## Contents

| Промпт | Роль / стадия | Тип вывода |
|---|---|---|
| `intake.md` | INTAKE — нормализация задачи | текст/статус |
| `architect.md` | ARCHITECT — план изменений | free-form текст (план) |
| `coder.md` | CODER — правит worktree | editor (правки файлов) |
| `tester.md` | TESTER — правит/добавляет тесты | editor (правки файлов) |
| `reviewer.md` | CODE_REVIEW и FINAL_REVIEW — вердикт | **structured** (JSON по схеме) |

## Key concepts

- **Reviewer обязан давать машиночитаемый вердикт** по `../schemas/verdict.json`: список
  `issues` с `class` (`logic_bug`/`weak_tests`/`wrong_design`/`nit`). Класс → стадию переводит
  **код** (`graph.CLASS_TO_STAGE`), не модель — промпт лишь классифицирует.
- Промпт стадии собирается в `artifacts.py` (роль + контекст джоба + артефакты) и передаётся в
  `runner` как stdin.

## Invariants & gotchas

- Меняешь контракт вывода ревьюера — синхронно правь `../schemas/verdict.json`, `verdict.py`
  (парс/валидация) и тесты; иначе вердикт не пройдёт валидацию и стадия уйдёт в `error`.
- Не проси в промпте называть следующую стадию — маршрут не дело модели.
- Recipe-специфичные промпты (`prompts/recipes/<recipe>/<role>.md`) — запланированы в backlog
  (PDD-40), с fallback на дефолтный промпт роли.

## Related

- [../README.md](../README.md) — поток стадий · [../schemas/README.md](../schemas/README.md)
