# Живая приёмка контекста исходных строк — 09.10.2026

## Краткий вывод

Замороженный набор source-context обработан выбранной моделью через настоящие
MCP-инструменты подключённого Codex-клиента. Все 8 контрольных сценариев совпали
по статусам, числу атомов, диагностике контекста и ссылкам на строки-источники.
Сохранены 15 исходных строк и 16 атомов; `reqmap_finalize(allow_partial=false)`
и последующий `reqmap_get_result` вернули `finalized / SUCCESS`, revision 32.
Две строки / два атома получили `supported`, остальные 13 строк / 14 атомов —
`insufficient_evidence`. Ложных подтверждений и пропущенных положительных
контролей нет. `SUCCESS` означает завершение анализа и экспорта.

Живой этап согласованной приёмки source-context закрыт. Версия `8d2c09d`
из `maintenance/keystone-api-evidence` слита в `main` и опубликована 09.10.2026;
подробности проверки публикации приведены в конце отчёта.
Полный корпус из 1949 строк, deep с реальной моделью, другие IDE и визуальное
качество XLSX/Markdown этим запуском не проверены. Ручная проверка оформления
остаётся пропущенной по решению пользователя от 08.10.2026.

## Клиент, вход и способ исполнения

| Параметр | Фактическое значение |
| --- | --- |
| Проверенный код | `3df6923165a78e1aeda5816fe2d6e02e6abf3478` |
| Клиентский журнал | Codex `0.162.0`, `source=vscode`, `originator=codex-tui` |
| Модель / effort по журналу клиента | `gpt-6-astra` / `xhigh` |
| Клиентская сессия | `01a11ef1-20b2-7e21-8bd8-74d0e0875bfc` |
| Клиентский turn | `01a11f31-daac-7e92-807a-3e00a38c1826` |
| Reqmap session | `ebc363f9-bb02-4bbb-9319-e05789554687` |
| Профиль / result schema | `legacy` / `1.2` |
| Tool / workflow / binding engine | `3.0` / `3.0` / `2.0` |
| Prompts decomposition / mapping | `2.1` / `1.4` |
| Grammar / resolver | `1.2` / `1.0` |

Имя модели установлено по клиентскому журналу после финализации, поэтому
`reported_model` в submit-запросах отсутствует. Сервер сохраняет
`model=external-agent`, `reported_client=codex`, `identity_verified=false`;
метаданные клиента не являются криптографической аттестацией модели.
Отображение ответа в интерфейсе VS Code отдельно не проверялось.

Исполнение продолжило рабочую беседу после запроса пользователя «давай продолжим».
Применён [согласованный сценарий и его prompt](2026-10-08-source-context-runtime.md#пропущенная-ручная-проверка-и-живой-eval).
Вход — полный массив из
[`live_input.json`](../../tests/fixtures/source_context/live_input.json),
карта — заранее подготовленные canonical bytes `maps.mcp.mapping` из
[`live_eval.json`](../../tests/fixtures/source_context/live_eval.json).
Запущенный сервер использовал отдельную установленную копию в
`results/source-context-live-eval-2026-10-08/runtime/`.
Все 63 Python-модуля установки побайтово совпали с рабочими исходниками.
Вход, карта, KB, настройки и runtime-код во время этого прогона не изменялись.

Ожидаемые статусы из `expected` впервые показаны модели после finalize и
get_result. До этого операторская проверка загрузила fixture только для сверки
input/map bytes без вывода ожидаемых ответов. При подготовке читались также
инструкции и общий acceptance helper; после отказа proposal — код валидатора.
Это живая приёмка в рабочем контексте, а не эксперимент с изолированной моделью.
Предложения составлены моделью по актуальным `get_atom_context`; вспомогательный
JavaScript последовательно передавал их настоящим MCP-инструментам.
Scripted model transport, CLI `analyze` и отдельный LLM endpoint не применялись.

## Контрольные сценарии

| Сценарий | Строка | Атомов | Результат | Диагностика контекста | Строки-источники |
| --- | --- | --- | --- | --- | --- |
| independent-api | REQ-0001 | 1 | supported | нет | нет |
| missing-review | REQ-0002 | 1 | insufficient_evidence | SOURCE_CONTEXT_UNREVIEWED | нет |
| gui-list | REQ-0003 | 1 | insufficient_evidence | SOURCE_INTERFACE_UNPROVEN | 4 |
| api-list | REQ-0005 | 1 | supported | нет | 6 |
| two-level-and-after-list | REQ-0007 | 1 | insufficient_evidence | SOURCE_INTERFACE_UNPROVEN, SOURCE_CONDITION_UNPROVEN | 8, 9, 10 |
| own-api-versus-gui | REQ-0011 | 1 | insufficient_evidence | SOURCE_CONTEXT_CONFLICT | 12 |
| multi-atom-same-context | REQ-0013 | 2 | insufficient_evidence | SOURCE_INTERFACE_UNPROVEN у обоих атомов | 14 |
| draft | REQ-0015 | 1 | insufficient_evidence | SOURCE_CONTEXT_UNREVIEWED | нет |

Восемь сценариев охватывают девять атомов. Семь дополнительных строк-заголовков
сохранены отдельными атомами с `SOURCE_UNPARSED`, `BINDING_UNREVIEWED` и
`SOURCE_CONTEXT_UNREVIEWED`. Повторяющиеся тексты не объединены.
Для REQ-0007 сохранены GUI, отказ узла и срок в одну миллисекунду; для REQ-0013
общая GUI-ссылка применена отдельно к Nova и Neutron. Локальные цитаты и spans
не заменены цитатами внешнего контекста.

Frozen-контракт проверяет именно перечисленные коды контекста. Полный набор
диагностики также содержит `EVIDENCE_MISSING` с `field=prior_status` у
REQ-0002, REQ-0011 и REQ-0015: модель сама предложила недостаточность полного
обязательства. Эти дополнительные коды сохранены в отчёте и verification JSON,
не скрыты и не выданы за побайтовое совпадение всей диагностики с baseline.

Всего выполнены 52 MCP-вызова: start 1, get_session 1, submit_atoms 15,
get_atom_context 16, submit_mapping 17, finalize 1, get_result 1.
Один submit для REQ-0002 отклонён с `PROPOSAL_INVALID`: атом имел
`insufficient_evidence`, а вложенный mapping — `supported`. После проверки
контракта изменён только статус mapping; повтор с новым request_id принят.
Код, данные и ожидания ради этого не менялись.

Request IDs: `source-context-live-20261009-start-01`,
`source-context-live-20261009-atoms-REQ-NNNN`,
`source-context-live-20261009-map-REQ-NNNN-ANNN`,
повтор `source-context-live-20261009-map-REQ-0002-A001-r1`,
`source-context-live-20261009-finalize-01`.
Отклонение осталось в неизменяемом опубликованном журнале.

## Проверка артефактов и сохранённые свидетельства

Отдельный скрипт после завершения модели проверил полный текст, порядок,
координаты всех 15 строк, 16 canonical atoms, точные Unicode spans, source hashes
и обязательность атомов. Проверены 53 вхождения внешних source refs, наличие
всех текстов и atom IDs в пяти листах XLSX, отсутствие Excel-формул и наличие
идентичностей карты/resolver в XLSX и Markdown. Это программная проверка данных.
Четыре хеша сверены с manifest; SHA-256 всех пяти файлов записаны отдельно.
Серверный get_result также проверил опубликованный набор.

Локальные свидетельства находятся в `results/source-context-live-eval-2026-10-08/`:
`mcp-requests-2026-10-09.json`, `mcp-contexts-2026-10-09.json`,
`mcp-initial-session-2026-10-09.json`, `mcp-finalize-2026-10-09.json`,
`mcp-get-result-2026-10-09.json`, `verify-live-2026-10-09.py`,
`verification-2026-10-09.json`. Каталог результата:
`reports/8da16ccb-e1f3-4ae0-bc51-9ff07858ca68/`.
Файлы результата не редактировались; локальные свидетельства не включены в Git.
Клиентский журнал:
`~/.codex/sessions/2026/10/09/rollout-2026-10-09T07-34-41-01a11ef1-20b2-7e21-8bd8-74d0e0875bfc.jsonl`.

Повторить проверку готовых файлов из корня рабочей ветки:

```sh
.venv/bin/python results/source-context-live-eval-2026-10-08/verify-live-2026-10-09.py
```

| Объект | SHA-256 |
| --- | --- |
| Frozen fixture | `139335c3ea7f0b66e2f1d0ef70cb6cd7ebc9877b94fef25ed0721b73038df2c5` |
| Input | `d327bf223564dfe5f869c9df487881cccebae9b5b78700aab7c767658f79eea5` |
| MCP map | `c1d12d94ec8298093b4acaf5dae7575948608e8ad6302b566d1037173d00a469` |
| Resolver | `9df1599f759375bdcfb4ea8936b3c8c8e034c69c9ac14d52295d468e719e1e23` |
| Grammar | `932e7ec629d5b0c5859de7bc13cce129de646c452c997814310754c26b14a990` |
| KB | `533252fe09dc73c4fd1a9bd24b1ff60a1067c163547643ed1a0a1af311acc406` |
| Binding catalog | `f00f8103e3c0f7e94df58f0b3b63bf262932769c963d2bc5bf041b0f506fc662` |
| result.json | `e620e626ecd5c30071f824e324379f6650cd2c1971981bf58e8f22c2f75d7725` |
| result.xlsx | `a1c269fad6159a98cb14ae5a982095e6600451c88900c7c6352040762b79046b` |
| report.md | `4661acf295c19b8ae4217f435c6529715d30650974a7c0b5df88b5dbb8af5d7b` |
| run.jsonl | `0d0025b9b76928dbd9b5a73a40ca60d193c2aff2d729d24eb0f3a155c4b17d82` |
| manifest.json | `8aac9990c9a57a5baa2ed85231fc14cc01e0758f143dd0a889cd525fcbfc86c6` |

Полный suite с 1840 passed и независимое ревью относятся к
[проверке реализации 08.10.2026](2026-10-08-source-context-runtime.md).
После этого живого прогона runtime не изменён; повтор полного suite не требовался.
Для обновлённых документов выполнены `tests/test_docs_language.py` — 23 passed,
проверка 76 относительных ссылок — без отсутствующих файлов, `git diff --check` —
без ошибок. В плане source-context не осталось незакрытых пунктов.
Расширение грамматики и доказательств для исходных книг остаётся отдельным этапом
[общего плана](../superpowers/plans/2026-10-05-reqmap-coverage-remediation.md).

## Публикация 09.10.2026

Перед слиянием на `8d2c09d809ab50f121aab788aaab17aafac2508d` повторно выполнен
полный suite: **1840 passed, 3 warnings, 539.47 s**, exit 0. Журнал:
`results/publication-2026-10-09/full-suite.log`. Предупреждения относятся к fork
в многопоточном тестовом процессе и не являются результатами инфраструктурных проверок.

`main` обновлён fast-forward с `b3b5180` до указанного коммита; `git diff
--exit-code maintenance/keystone-api-evidence HEAD` подтвердил тождество
сливаемого дерева проверенному. После `git push origin main` команда
`git ls-remote origin refs/heads/main` вернула
`8d2c09d809ab50f121aab788aaab17aafac2508d`. Последующее обновление этих сведений
меняет только документацию. Рабочая копия сохранена: в ней находятся подключённая
MCP-установка и локальные проверенные артефакты, не входящие в Git.

Публикация не закрывает расширение грамматики и проверку реальных требований:
этот этап продолжается отдельно; первоначальная приоритетная выборка содержала
28 строк и не имела полного положительного доказательства в действующем каталоге.
