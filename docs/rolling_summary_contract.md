# Контракт Rolling Conversation Summary

[← Индекс документации](README.md) · [Аудит Context & Memory Core](context_memory_audit.md) ·
[Архитектура](architecture.md)

## Статус и scope

Этот документ задаёт контракт, реализованный на Этапе 3. Runtime implementation
находится в `conversation_summary.py`, а orchestration точек preflight,
post-turn и session lifecycle — в `main.py` и `runtime_state.py`.

Rolling summary сжимает старую часть только **текущей conversation session**.
Он является bounded conversation context, а не долговременной памятью:

- не является user memory и не даёт разрешения на запись user memory;
- не читает и не пишет `.arvis_mcp_memory/`;
- не создаёт и не обрабатывает `MEMORY_INTENT`;
- не создаёт `ACTION_INTENT`, не вызывает resolver/router и не выполняет actions;
- не смешивается с отдельным `command_history`.

## Реализованный flow

Успешный text turn выглядит так:

```text
user text
  → append current user message to active_history
  → hard-budget preflight
  → optional normal compaction, затем emergency eviction только при overflow
  → build bounded context with JSON-wrapped untrusted summary
  → OllamaClient.chat()
  → parse assistant response
  → resolver / Command Router / Response Renderer
  → append parsed assistant message or action-aware rendered response
  → one bounded post-turn compaction attempt at soft threshold
```

Normal compaction сохраняет восемь newest completed turns, удаляет только exact
oldest completed prefix после строгой проверки replacement summary и никогда не
режет половину turn. При Ollama error только что добавленный user message
удаляется, а незавершённый turn не остаётся в history. Hard-limit eviction
остаётся отдельным явно degraded path и не изменяет validated summary.

Только `/voice once` передаёт распознанный текст в тот же `process_user_text()`.
`/voice test` и `/voice diagnose` не входят в conversation history. Slash-команды
обрабатываются отдельно и также не являются conversation messages.

## Термины и инварианты

- **Message** — объект ровно с conversation role `user` или `assistant` и
  строковым `content`.
- **Completed turn** — две соседние записи `user`, затем `assistant`.
- **Pending user message** — последняя запись `user`, для которой ещё нет
  assistant response. Она не является completed turn.
- **Selected turns** — самый старый непрерывный prefix completed turns,
  выбранный кодом для одного summarization transaction.
- **Previous summary** — уже validated и bounded `session_summary` этой session.
- **Summary candidate** — недоверенный raw model output до validation.
- **Emergency eviction** — отдельная degraded операция удаления старого
  context при hard limit; она не является и не называется summarization.

Обязательные инварианты:

1. Summary и recent history принадлежат одному `session_id`.
2. Обычная compaction удаляет только целые selected turns.
3. Pending user message никогда не передаётся summarizer и не удаляется им.
4. Код, а не модель, выбирает exact messages и хранит их границы.
5. Selected turns удаляются только после полной validation нового summary.
6. Одно обновление summary и удаление его selected turns логически atomic.
7. `command_history`, debug panels, raw model response и voice capture state не
   являются входом summarizer.
8. Emergency eviction является отдельным явно degraded hard-limit path и не
   ослабляет validation rules normal summarization transaction.

## Утверждённые limits v1

| Limit | Значение | Семантика |
| --- | ---: | --- |
| Soft message limit | 32 messages | После completed turn запустить compaction при `>= 32` conversation messages. |
| Hard message limit | 40 messages | Перед normal Ollama request context не должен содержать больше 40 conversation messages. Это сохраняет текущую верхнюю границу. |
| Soft context character budget | 24 000 characters | После completed turn запустить compaction при `>= 24 000` characters, даже если messages меньше 32. |
| Hard context character budget | 32 000 characters | Перед normal Ollama request выполнить emergency compaction, если request превысил бы budget. |
| Recent turns to keep | 8 completed turns | В normal compaction последние 16 messages остаются verbatim. |
| Maximum validated summary | 4 000 characters | Bound после whitespace normalization и output secret filtering. |
| Maximum summarizer request | 24 000 characters | `sum(len(message["content"]))` для всего summarizer request, включая trusted prompt и serialized untrusted data. |
| Maximum raw model output | 8 000 characters | Более длинный output отклоняется до JSON parsing и не приводит к удалению history. |

Character означает результат Python `len(str)` после нормализации переносов
строк, а не tokens или bytes. Для normal Ollama context budget считается как
`sum(len(message["content"]))` уже построенного request: таким образом в него
входят fixed wrapper, summary, recent history и current user message. Message
limit считает только conversation entries и не считает synthetic system wrapper.

Character budgets — сознательная v1 approximation. Она deterministic, не
требует tokenizer или новой тяжёлой dependency. Разрыв между soft 24 000 и hard
32 000 оставляет место для одного крупного следующего сообщения. Soft limit в
32 messages запускает compaction до существующей границы 40, а восемь recent
turns сохраняют достаточно verbatim контекста для уточнений и action repair.

## Момент запуска и общий flow

Normal check выполняется только после успешного завершения полного turn и
добавления итогового assistant history text:

```text
complete conversation turn
  → check soft message/character limits
  → select oldest complete turns
  → sanitize a copy of previous summary and selected messages
  → call ConversationSummarizer
  → strictly validate and sanitize output
  → atomically replace session_summary
  → remove exactly the successfully summarized messages
```

Ниже обоих soft thresholds summarizer не вызывается. За один post-turn check
выполняется не больше одного summarizer request. Если один bounded batch не
снижает history ниже soft limit, следующий completed turn может обработать
следующий batch; hard-limit path не полагается на это ожидание.

До normal Ollama request выполняется отдельный preflight. Если только что
добавленный current user message сделал будущий request больше 40 conversation
messages или 32 000 characters, preflight пытается сжать старые completed turns
до отправки current message основной модели. Current user message всё время
остаётся pending и не попадает во вход summarizer.

## Turn boundaries и выбор старых turns

### Нормальная последовательность

History сканируется от начала. Valid normal форма:

```text
(user, assistant)* [, user]
```

Последний одиночный `user` допустим только во время normal request. Любая пара
для compaction должна быть завершена. Для normal compaction код:

1. Проверяет структуру всей history.
2. Отделяет pending user message, если он есть.
3. Резервирует восемь самых новых completed turns.
4. Берёт самый старый непрерывный prefix из оставшихся eligible turns.
5. Добавляет только целые turns, пока весь summarizer request остаётся не больше
   24 000 characters.
6. Запоминает границу выбранного prefix независимо от model output.

Если первый eligible turn сам не помещается в summarizer request, normal
summarization не запускается и history не изменяется. Messages нельзя обрезать
внутри turn и затем считать этот turn успешно summarized.

Handled action сохраняет в assistant half of the turn action-aware
`final_response`, который видел пользователь, а не raw assistant output или
structured intent. Поэтому summarizer получает тот же завершённый разговор,
который представляет `active_history`.

### Незавершённая voice-команда

Запись, ducking, transcription и `/voice once` validation должны полностью
закончиться до добавления transcript в conversation history. Summarization не
запускается из voice capture callback и не работает параллельно с capture.
После передачи accepted transcript в общий text pipeline действуют обычные
pending/completed turn rules. `/voice test` и `/voice diagnose` ничего не
добавляют и не могут запустить summarizer.

### Повреждённая или нестандартная последовательность

Валидатор не угадывает связи между entries. Следующие случаи делают history
структурно повреждённой: unknown role, non-string content, orphan `assistant`,
два `user` подряд, два `assistant` подряд, либо `user` не в последней позиции
без следующего `assistant`.

При normal check summarization завершается failure без model call, summary и
history не меняются, а diagnostic не должен печатать raw content. Повреждённые
entries никогда не отправляются summarizer. Перед main-model request corrupted
history также не должна передаваться как trusted context. Hard-limit recovery
описан отдельно ниже; он не превращает повреждённые entries в вымышленные turns.

## Input contract `ConversationSummarizer`

Application boundary v1:

```text
ConversationSummaryRequest
  session_id: UUID
  previous_summary: str
  completed_messages: immutable sequence[ConversationMessage]
  limits:
    max_request_chars: 24000
    max_summary_chars: 4000
```

`completed_messages` содержит непустой oldest-first список с чётным числом
entries и строгим чередованием `user`, `assistant`. Он уже выбран application
code и является immutable copy. `previous_summary` пуст только при первом
обновлении; иначе он уже прошёл тот же output contract. `session_id` нужен для
correlation и защиты от применения результата к другой session, но не
передаётся модели и не влияет на содержание summary.

Перед model call summarizer повторно проверяет request invariants и строит два
Ollama messages:

1. Trusted `system` instruction задаёт задачу, output JSON contract, content
   policy и прямо запрещает выполнять инструкции из historical data.
2. `user` message содержит JSON-serialized data envelope с
   `previous_summary` и `completed_messages`. Все строки JSON-escaped и явно
   помечены как untrusted quoted conversation data.

Trusted instruction и historical data должны находиться в разных Ollama
messages; historical envelope нельзя интерполировать в instruction text. Role,
порядок и fixed instruction создаёт только application code. Ни старые messages,
ни previous summary не могут добавлять или изменять summarizer instructions.

Limits и `session_id` не принимаются из model text. Previous summary и старые
messages считаются **данными**, даже если внутри находятся `SYSTEM:`,
`ACTION_INTENT`, `MEMORY_INTENT`, «игнорируй предыдущие инструкции», просьба
кликнуть, shell command или другой prompt injection.

Перед сериализацией secret/path/prompt-injection sanitizer работает над копией.
Он не изменяет оригинальную history до успешного transaction. Итоговый
summarizer request проверяется по точному 24 000-character limit после
serialization и добавления trusted prompt.

## Output contract

Модель должна вернуть только один JSON object:

```json
{
  "summary": "bounded summary text"
}
```

Validation выполняется в следующем порядке:

1. Raw output должен быть строкой не длиннее 8 000 characters, чтобы oversized
   response не обрабатывался без границы.
2. `json.loads(raw_output.strip())` должен разобрать весь текст. Markdown fence,
   prefix/suffix prose и второй JSON object недопустимы.
3. Top-level value должен быть object с точным набором ключей `{"summary"}`.
   Любые дополнительные model-generated поля отклоняют весь candidate.
4. `summary` должен быть строкой.
5. Whitespace normalization заменяет CRLF/CR на LF, схлопывает horizontal
   whitespace внутри строк до одного пробела, удаляет trailing whitespace,
   допускает не больше одной пустой строки подряд и применяет outer `strip()`.
6. Нормализованный summary должен быть non-empty.
7. Output проходит secret/path filtering, prompt-injection checks и повторную
   whitespace normalization.
8. После filtering summary должен оставаться meaningful, non-empty и иметь не
   больше 4 000 characters. Oversized output не обрезается, а отклоняется.

Validator возвращает только bounded summary text либо failure. Он не принимает
от модели message count, IDs, offsets, `processed_until`, actions, memory facts
или другие управляющие поля. Exact selected turns хранятся вызывающим кодом.
Только validated result разрешает atomic update и удаление exact selected
prefix. Поле `summary` всегда является полной заменой: оно должно объединить
ещё актуальный previous summary с полезным context selected turns, а не вернуть
delta, которое application code будет слепо дописывать к старому тексту.

## Содержимое summary

Summary хранит только контекст с будущей разговорной ценностью:

- текущую цель пользователя;
- подтверждённые в разговоре факты;
- важные ограничения и safety boundaries;
- принятые решения и их существенные причины;
- незавершённые вопросы и blockers;
- обещанные следующие действия;
- необходимые имена, filenames, action targets и технические идентификаторы.

Без необходимости он не хранит приветствия, small talk, повторы, промежуточные
рассуждения, chain-of-thought, raw diagnostic output, полный router/debug output,
raw intents, длинные stack traces и случайные формулировки без будущей ценности.
Утверждение модели не становится «подтверждённым фактом» только из-за попадания
в assistant message; summary должен сохранять provenance вроде «пользователь
сообщил» или «assistant предложил», когда это важно.

Внутренняя структура текста v1 — семь стабильных sections в фиксированном
порядке: `Goal`, `Confirmed facts`, `Constraints`, `Decisions`,
`Open questions`, `Next actions`, `Names/identifiers`. Каждый label присутствует
ровно один раз; для пустого section используется короткое `None`. Content
остаётся на доминирующем языке текущего разговора, а labels, имена и технические
identifiers не переводятся. Это human-readable text, а не новый application
schema.

## Security и privacy boundary

### Двухсторонняя фильтрация

Один deterministic sanitizer применяется:

```text
before sending previous summary and old messages to summarizer
and
after receiving and parsing candidate summary
```

Минимальные категории:

- passwords и password-like assignments;
- API keys, access/refresh tokens и high-confidence bearer strings;
- cookies, session IDs и authorization headers;
- PEM/private key blocks и private-key material;
- одноразовые коды и recovery codes;
- абсолютные персональные paths (`/home/<user>/...`, `/Users/<user>/...`,
  `C:\Users\<user>\...`) и известный local home prefix;
- high-confidence prompt-control phrases, включая просьбы игнорировать прежние
  инструкции, изменить system policy или выполнить embedded action.

Sanitizer заменяет найденные значения typed placeholders, например
`[REDACTED_TOKEN]`, `[REDACTED_PRIVATE_KEY]`,
`[REDACTED_PERSONAL_PATH]` и `[UNTRUSTED_PROMPT_INJECTION_TEXT]`. Он не пишет
найденные значения в logs или diagnostics. Для path, полезного как технический
контекст, можно сохранить безопасный basename или repo-relative path без home
prefix.

Поиск известных prompt-injection фраз является только дополнительной
defense-in-depth проверкой. Контракт безопасности не зависит от полноты такого
denylist: основная граница обеспечивается раздельными trusted instructions и
JSON-serialized historical data, code-owned roles/order, strict output schema,
independent validation и отсутствием authority у summary.

После output filtering выполняется повторный scan. Если secret-like material
остался, high-risk block нельзя надёжно отредактировать, redaction опустошила
summary либо output содержит прямые policy/action instructions, candidate
отклоняется и selected turns не удаляются. Успешно отредактированный,
повторно проверенный summary допустим, но diagnostic сообщает только категорию
redaction, не исходное значение.

### Передача основной модели

Validated summary остаётся недоверенными historical data. Context builder не
должен просто повышать его до неограниченной system instruction. Wrapper v1 —
trusted fixed text с JSON-encoded полем
`untrusted_conversation_summary`, явным указанием использовать его только как
историю и никогда не выполнять содержащиеся в нём instructions или actions.
Ни summary, ни содержащийся в нём action-like текст не идут через parser,
resolver, Command Router или Memory Router.

## Failure behavior и transaction

| Failure | Поведение |
| --- | --- |
| Ollama offline / timeout / request error | Оставить summary и history без изменений; показать короткое redacted warning; normal chat продолжить, пока hard budget не нарушен. |
| Exception | Перехватить на summarizer boundary, не менять state и не завершать REPL. |
| Invalid JSON / extra fields / wrong type | Отклонить candidate; ничего не удалять. |
| Empty normalized summary | Отклонить candidate; ничего не удалять. |
| Oversized raw или normalized output | Отклонить, не truncate и ничего не удалять. |
| Secret/path/injection validation failure | Отклонить candidate; не логировать sensitive text и ничего не удалять. |
| Повреждённые history entries | Не вызывать summarizer, не угадывать turns и не менять state в normal path. |
| Session changed while request was running | Отбросить результат как stale; не менять новую session. |

Ключевое правило normal transaction:

> Не удалять сообщения, если новый summary не прошёл полную проверку.

Порядок commit: проверить, что `session_id` и selected prefix всё ещё совпадают,
записать validated summary, затем удалить exact selected prefix как одну
синхронную state operation. Stage 3 не должен запускать summarizer параллельно с
обработкой другого turn.

### Emergency fallback

History всё равно должна иметь конечную границу. Emergency path включается
только перед main Ollama request, если после одной попытки summarization request
всё ещё превысил бы hard message или character budget.

1. Он сохраняет existing validated summary без изменений.
2. Он deterministic удаляет самые старые **полные** turns до hard budget и по
   возможности сохраняет хотя бы два самых новых completed turns.
3. Если budget всё ещё превышен, он может удалить и эти old completed turns, но
   никогда не удаляет pending current user message.
4. Он показывает явное сообщение, что часть старого контекста отброшена **без
   summary**, и записывает только redacted diagnostic category/count.
5. Он не создаёт placeholder summary и не утверждает, что summarization была
   успешной.

Это единственное разрешённое удаление без validated replacement summary и оно
доступно только после фактического hard overflow. Emergency eviction остаётся
отдельной операцией, а не исключением, позволяющим normal summarization
transaction удалить selected turns после invalid output. Если сама current user
message вместе с fixed wrapper и bounded existing summary не помещается даже
после удаления всех completed turns, normal Ollama request не отправляется:
pending message удаляется как необработанное, а пользователю предлагается
сократить или разделить ввод.

При structurally corrupted history hard recovery fail-closed отбрасывает
corrupted conversation history целиком, сохраняет previous validated summary и
заново добавляет только известный текущий user input. Это явно сообщается как
context reset из-за corruption, а не как summary. Восстановление reload state в
Этапе 3 должно отклонять corrupted history раньше, чтобы этот path был редким.

## Lifecycle

Контракт использует решения, уже принятые в context/memory audit:

| Событие | Целевая семантика Этапа 3 |
| --- | --- |
| Новый обычный запуск | Создать новый случайный UUID `session_id`, пустые `active_history` и `session_summary`. Ничего не восстанавливать из прошлой завершённой session. |
| `/reset` | Очистить history, summary и command history/counter; создать новый session UUID; сохранить dry-run/debug; не менять user или MCP memory. |
| `/reload`, `/restart` | Перенести bounded validated history, summary и тот же session UUID через one-shot reload state. |
| `/exit`, `/quit`, EOF, interrupt | Не сохранять conversation history или summary; они заканчиваются вместе с session/process. |
| Ollama/summarizer failure | Не менять session ID, user memory или MCP memory. |

Эта lifecycle-семантика реализована. Reload snapshot проверяет UUID, строгую
conversation sequence и bounds, записывается атомарно с private permissions и
удаляется после единственной попытки restore.

## Использование Ollama в v1

Первая реализация должна переиспользовать существующий `OllamaClient` и ту же
configured model (`ARVIS_MODEL`, default `arvis`). Summarization — отдельный
bounded call к `/api/chat` с собственным trusted prompt и strict JSON output
validation. Call использует Ollama structured output mode `format: "json"`, но
raw response всё равно проходит полный application validator и не может ослабить
его правила. Его raw output не проходит через `parse_assistant_response()`:
иначе model-generated intents могли бы смешаться с summary contract.

Новый provider, tokenizer и тяжёлые зависимости не нужны. Отдельный summarizer
model можно позже добавить как optional configuration после измерений качества,
latency и memory use; correctness и safety contract от выбора модели не зависят.

## Unit tests, необходимые для Этапа 3

### Thresholds, budgets и turn selection

- summarizer не вызывается при 30 messages (15 completed turns) и context
  меньше 24 000 characters;
- soft trigger срабатывает при 32 messages;
- character trigger срабатывает при 24 000 characters до message threshold;
- старые completed turns успешно сжимаются oldest-first;
- последние восемь completed turns остаются verbatim и в исходном порядке;
- selected batch не превышает 24 000 characters с учётом полного prompt;
- oversized single eligible turn не режется и не удаляется normal path;
- current pending user message не выбирается и не удаляется;
- orphan/duplicate/unknown roles дают structural failure без model call;
- command history и slash-команды не попадают в summarizer input.

### Input/output и failure atomicity

- предыдущий summary входит в следующий request и merge сохраняет ещё
  актуальный старый контекст;
- model input экранирован и явно помечен как untrusted data;
- exact `{"summary": "..."}` принимается после whitespace normalization;
- invalid JSON, Markdown fence, prefix/suffix prose и extra field отклоняются;
- пустой и whitespace-only summary отклоняются;
- oversized raw и normalized output отклоняются без truncation;
- Ollama offline, timeout и exception оставляют state неизменным;
- messages удаляются только после validation и только до сохранённой code-owned
  boundary;
- stale result с другим session ID или изменившимся prefix отклоняется;
- при output secret, который нельзя безопасно отредактировать, history не
  удаляется.

### Security и privacy

- password, API key, token, cookie, authorization header, private key и OTP
  redacted до model call и после model output;
- абсолютные personal paths redacted, а safe repo-relative identifiers
  сохраняются;
- фраза «игнорируй предыдущие инструкции» внутри старого message остаётся
  untrusted data и не меняет prompt/output contract;
- неизвестный denylist произвольный injection text также не может изменить
  code-owned roles, trusted instruction или выбранную message boundary;
- summary с direct action/policy instruction не может вызвать router, action или
  memory write;
- diagnostics не содержат исходный secret или personal path;
- user-memory store/helpers и MCP memory helpers не вызываются.

### Flow и lifecycle

- `/reset` очищает active history и summary, сбрасывает command context и создаёт
  новый session ID, не затрагивая user/MCP memory;
- `/reload` сохраняет и восстанавливает history, summary и тот же session ID;
- новый обычный запуск создаёт новую session и не восстанавливает прежний
  summary;
- обычный exit не создаёт durable conversation snapshot;
- `/voice once` использует тот же conversation/summary flow;
- `/voice test` и `/voice diagnose` не запускают summarization;
- handled action сохраняет и summarises rendered response, а не raw assistant
  output или intent JSON;
- normal assistant/Ollama error удаляет только pending user message и не
  повреждает ранее validated summary;
- emergency eviction удаляет только полные old turns, явно сообщает degraded
  result и не меняет summary;
- oversized current user input не отправляется Ollama и возвращается как
  безопасная ошибка;
- corrupted reload history отклоняется без передачи модели.

## Утверждённые решения v1

Следующие решения являются обязательными для первой реализации:

| Решение | Утверждённый вариант | Обоснование |
| --- | --- | --- |
| Message thresholds | Soft `32`, hard `40` | Даёт запас в четыре обычных turns и сохраняет текущую верхнюю границу. |
| Character budget | Soft `24 000`, hard `32 000` request characters | Bounded stdlib calculation без tokenizer; запас покрывает следующий turn. |
| Recent context | Сохранять 8 последних completed turns verbatim | Достаточно для локальных уточнений, но после compaction освобождает примерно половину message window. |
| Output limits | 4 000 normalized summary characters; 8 000 raw output characters | Summary остаётся существенно меньше recent transcript, oversized output отклоняется до parse work. |
| Summarizer input | Не больше 24 000 request characters | Ограничивает отдельный model call тем же deterministic character accounting. |
| Retry | Не делать automatic retry в v1 | Не удваивает latency и нагрузку на offline/unstable Ollama; следующий turn даёт новую normal attempt, hard path имеет явный fallback. |
| Emergency fallback | Только после hard overflow: warned eviction oldest complete turns; никогда не fake summary | Гарантирует конечный рост и сохраняет честную failure semantics. |
| Oversized current input | Отклонить до main Ollama request, если он не помещается после удаления old completed turns | Не удаляет pending message как якобы обработанный и сохраняет hard request bound. |
| Язык и структура | Dominant conversation language, семь labels в фиксированном порядке, exact identifiers unchanged | Минимизирует translation loss и оставляет summary читаемым и предсказуемым. |
| Ollama model | Тот же `OllamaClient` и configured model | Нет нового provider/config/dependency; отдельную модель можно обосновать позже измерениями. |

Этап 3 должен реализовать эти решения минимальным отдельным
summarizer/validator boundary, не смешивая его с user memory или action pipeline.

## Deferred decisions

Следующие расширения не нужны для первой реализации и не меняют утверждённый
v1 contract:

- отдельная configurable summarizer model;
- tokenizer-based budgets вместо bounded character accounting;
- добавление automatic retry после измерений latency и failure rate;
- долговременное сохранение и восстановление conversation sessions;
- более сложный machine-readable structured summary format вместо одного
  bounded text field с семью стабильными sections.
