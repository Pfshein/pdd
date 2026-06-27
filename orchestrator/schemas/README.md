# orchestrator/schemas/ — JSON-схемы structured-вывода

Схемы для qwen `--json-schema` (форсируют структурный вывод стадии) и для валидации артефактов.

| Схема | Что описывает |
|---|---|
| `verdict.json` | Вердикт ревьюера: `issues[]` с `class` (`logic_bug`/`weak_tests`/`wrong_design`/`nit`). Драйвит маршрут. |
| `plan.json` | План архитектора. |
| `task_meta.json` | Метаданные задачи (`task_meta.json` джоба): пороги триажа, флаги. |

`verdict.json` — самый «горячий»: его поле `class` через `graph.CLASS_TO_STAGE` определяет
loop-back. Меняешь схему вердикта — правь синхронно `../prompts/reviewer.md` и `verdict.py`.

См. [../prompts/README.md](../prompts/README.md), [../README.md](../README.md).
