# tests/ — pytest-набор ядра

Фокусные юнит-тесты control plane. **Без реальных model / network / Docker / Jira / GitHub** —
всё ядро тестируется детерминированно через stubs и monkeypatch. Прогон: `pytest -q`
(из корня; интерпретатор проекта, см. [../README.md](../README.md)).

## Purpose

Зафиксировать контракты, которые легко сломать: маршруты `router`, бюджеты/эскалацию,
sandbox-argv (границу безопасности), persistence и форматы артефактов, CLI-парсер, отчёт.
Каждая новая логика routing / persistence / CLI / report **обязана** нести тест здесь.

## Contents

Один файл на модуль/поведение: `test_<module>.py` ↔ `orchestrator/<module>.py`
(`test_router`, `test_queue`, `test_sandbox`, `test_report`, `test_cli`, `test_worktree`,
`test_killtree`, `test_verdict`, `test_reaper`, `test_events`, ...). Плюс сквозные:
`test_pipeline_stub` (граф на stub-модели), `test_issue_to_pr_smoke` (offline продуктовый
smoke), `test_runner_secrets` (нет утечки секретов в argv).

## Key concepts

- **Изоляция через monkeypatch путей.** Тесты, пишущие артефакты, патчат
  `config.RUNS_DIR` (и `config.WORKTREES_DIR`) на `tmp_path`:
  ```python
  monkeypatch.setattr(config, "RUNS_DIR", tmp_path / "runs")
  ```
  Модули читают `config.RUNS_DIR` **в рантайме** (а не на импорте), поэтому патч работает —
  при добавлении новых путей сохраняй это свойство.
- **Детерминированное время/случайность.** Передавай `now=...` в функции, где есть таймстампы
  (см. `test_queue`), вместо реального `time.time()`.
- **Stub вместо модели.** Долгие/процессные сценарии — через `stubs/qwen_stub.py` и подмену
  спавна; реальный qwen в тестах не зовётся.

## Invariants & gotchas

- Нет `conftest.py` — общих фикстур нет намеренно; хелперы локальны для файла. Заводя общий
  фикстур, взвесь, не проще ли локальный хелпер (как `_seed_job` в `test_reaper`).
- Тест, требующий живого эндпоинта/Docker, — это **не** юнит-тест: ему место в `tools/` как
  probe-скрипту, а не здесь.
- `test_sandbox` проверяет argv границы (cap-drop, read-only, network none). Меняешь
  `sandbox.docker_run_argv` — обнови и assert'ы, не ослабляя инварианты.

## Related

- [../orchestrator/README.md](../orchestrator/README.md) · [../stubs/README.md](../stubs/README.md)
- Probe-скрипты против живой модели — [../tools/README.md](../tools/README.md)
