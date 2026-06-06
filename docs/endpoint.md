# Контракт вызова qwen-стадии (подтверждено эмпирически)

`qwen-code 0.17.1`, удалённая модель `big-pickle` на `https://opencode.ai/zen/v1`.

## Запуск стадии

```
OPENAI_API_KEY=<key> \            # КЛЮЧ ТОЛЬКО через env, не в argv (утечка в ps/cmdline)
qwen --bare --approval-mode yolo -m big-pickle \
     --openai-base-url <url> \
     --max-wall-time <s> --max-tool-calls <N> \
     [--json-schema @schemas/verdict.json -o json] [--json-file events.jsonl] \
     [--exclude-tools a,b,c]
# промпт подаётся через STDIN, не позиционно
```

> ✓ Подтверждено: qwen берёт `OPENAI_API_KEY` из env процесса **даже с `--bare`** —
> `--openai-api-key` в argv не нужен (и убран ради безопасности). `model`/`base_url` —
> не секреты, остаются флагами.

## Подтверждённые факты / грабли

1. **Промпт — через stdin.** Позиционный промпт сжирается массив-опциями yargs
   (`--exclude-tools` и т.п.) → «No input provided via stdin». stdin развязывает
   текст промпта от парсинга аргументов. (`run_process(stdin_input=prompt)`.)

2. **`--approval-mode auto` НЕ работает на zen.** Нет «Classifier stage 1»
   (`Auto mode classifier unavailable ... action blocked for safety`) → все
   tool-вызовы блокируются. Используем **`--approval-mode yolo`**. Безопасность —
   песочница (`--sandbox`/контейнер) + worktree + стадия ревью, не классификатор.
   Warning про отсутствие песочницы глушится `QWEN_CODE_SUPPRESS_YOLO_WARNING=1`.

3. **`--json-schema` строго форсит структурный вывод.** Регистрируется синтетический
   tool `structured_output`; сессия завершается на первом валидном вызове. Если модель
   выдала текст вместо вызова — qwen падает (`is_error: true`, exit 1,
   `Model produced plain text instead of calling the structured_output tool`).

4. **Слабая модель уходит «исследовать ФС».** Для ревьюера, которому не нужны файлы,
   исключаем tools: `--exclude-tools run_shell_command,glob,read_file,edit,...` и в
   промпте явно «весь дифф ниже, не используй tools».

## Формат вывода (`-o json`)

stdout = JSON-массив событий. Берём ПОСЛЕДНИЙ `{"type":"result"}`:

```jsonc
// success
{"type":"result","subtype":"success","is_error":false,
 "result":"{\"issues\":[...]}",          // JSON-строка
 "structured_result":{"issues":[...]}}    // уже распарсенный объект  ← берём это
// error
{"type":"result","is_error":true,
 "error":{"message":"..."}}               // провал стадии → ретрай/эскалация
```

Парсер: `orchestrator/verdict.py::extract_structured()` →
`(obj | None, error_message | None)`, далее `validate_verdict()` по JSON-схеме.

## Коды возврата

- `0` — успех.
- `1` — ошибочный `result` (например, схема не выполнена).
- `55` — превышен `--max-wall-time` / `--max-tool-calls` (внутренний лимит).
- Внешний watchdog (`run_process` timeout → `kill_tree`) — поверх, на случай зависания
  до срабатывания внутренних лимитов.
