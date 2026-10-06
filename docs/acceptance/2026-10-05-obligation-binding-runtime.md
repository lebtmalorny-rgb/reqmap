# Приёмка source-to-evidence binding — 06.10.2026

## Краткий вывод

Общий binding validator реализован для legacy/deep, CLI/MCP, replay и экспорта.
Backend сохраняет полную исходную строку, определяет обязательства конечной
грамматикой и требует один проверенный предикат для всего обязательства.
Условия, отрицания и неизвестные фрагменты нельзя удалить предложением модели.
Неизвестный текст завершает анализ с `insufficient_evidence`.

Это этап 2 [общего плана](../superpowers/plans/2026-10-05-reqmap-coverage-remediation.md).
Импорт реальных книг, предметное обогащение KB и новая живая IDE-приёмка
(этапы 3–5) не выполнены. Проверенный runtime не означает готовность предметной
базы: production binding catalog в поставке отсутствует.

## Объём и проверка

База ветки: `434c76e385bda3d988947b33b522a228ed59fcb5`.
Коммиты задач 1–4: `f073ad8`, `bdf0202`, `5e30aa7`, `e684ccb`.
[Спецификация](../superpowers/specs/2026-10-05-obligation-binding-design.md),
[план реализации](../superpowers/plans/2026-10-05-obligation-binding-implementation.md).

- Source tests: точный SHA-256 UTF-8, позиции Unicode code points, повторные
  цитаты, emoji/скрытые символы, отрицания, удалённые условия и mandatory=false.
- Catalog tests: точная KB identity, подпись deep в отдельном SSH namespace,
  официальный direct source, reviewed state, строгие refs, hash/locator,
  duplicate keys, symlink, path traversal и изменение файлов.
- Оба публичных acceptor проверены положительными synthetic controls и
  контрастами create/delete, VM/user, API/GUI, отрицание, latency, отказ узла,
  GPU. Разные частичные predicates не объединяются в новую гарантию.
- CLI/MCP сравнивают одинаковые decisions; restart/checkpoints не восстанавливают
  старый вывод после смены контракта. Историческая finalized-фикстура создана
  опубликованным пакетом 434c76e и читается без изменения прежних пяти файлов.
- JSON/XLSX/Markdown сохраняют исходные spans, predicate/evidence refs и gaps;
  изменение одного gap обнаруживается crosscheck. Journal/manifest содержат
  ту же identity контракта. Остались пять файлов и пять/семь листов XLSX.
- Старые 19 deep gold cases сохраняют прежние ожидания на нижней границе
  evidence/procedure validator. Отдельный новый сквозной тест требует сохранить
  каждую неизвестную строку целиком и вернуть недостаточность без model POST.

Новые проверки проходили RED → GREEN. Полная команда
`PYTHONPATH=src .venv/bin/python -m pytest -q`: **1232 passed**, три прежних
предупреждения Python 3.14 о fork в многопоточном процессе, 126.86 s.
Дополнительная проверка документации, экспорта и crosscheck: **78 passed**.
`git diff --check`: чисто. Независимое ревью фиксируется ниже после выполнения.

## Совместимость

Результаты новых запусков: legacy `1.1`, deep `2.1`. Proposal v2,
MCP tool/workflow contract `2.0`; имена девяти tools сохранены. Active-сессии
прежнего контракта получают `SESSION_CONTRACT_MISMATCH`; нужен новый анализ.
Готовые 1.0/2.0 остаются историческими, а не автоматически перепроверенными.
Без binding catalog рост `insufficient_evidence` ожидаем и означает отсутствие
проверенной связи с исходным требованием. Финальные аспекты создаёт backend.

Порядок восстановления после обрыва и обновления: [RUNBOOK](../../RUNBOOK.md).
Точные поля: [OUTPUT_SCHEMA](../../OUTPUT_SCHEMA.md).
Каталог/доверие: [KNOWLEDGE_BASE](../../KNOWLEDGE_BASE.md).

## Решения реализации

1. V2 временно вводился рядом с V1 в промежуточных коммитах; Task 4 одновременно
   переключил все runtime entrypoints. Промежуточные версии не публикуются отдельно.
2. `; ` разделяет только самостоятельные полностью распознанные предложения.
   Любой неизвестный фрагмент оставляет всю строку unresolved; это сознательно
   ограничивает покрытие, сохраняя исходный смысл.
3. Predicates требуют direct evidence в обоих профилях. Indirect остаётся
   контекстом поиска; цена ограничения — дополнительные insufficient результаты.
4. Исторические gold expectations сохранены на evidence/procedure границе;
   новый runtime дополнительно проверяет полную исходную строку. Старые
   положительные ожидания не объявляются доказательством source binding.

Инструкция skill обновлена по проверке применения: прежняя редакция заставляла
проверяющего агента останавливаться на unresolved placeholder. С новой редакцией
он передал фактический canonical proposal целиком, сохранил условие времени,
не изменил обязательность и выбрал новую сессию при смене контракта. Проверка
frontmatter через skill-creator `quick_validate.py`: PASS. Это проверка
инструкции на сценарии, а не новая живая приёмка VS Code.


## Offline install и установленный MCP

`./install.sh /private/tmp/reqmap-obligation-installed` использовал только
`vendor/wheels` (`--no-index`). Проверены импорт из установленного
`site-packages` без development PYTHONPATH, `pip check` и installed
`knowledge validate`: legacy KB schema 1, 30 components, 30 capabilities,
37 evidence, SHA-256
`2d4d1440c32e827bcd1cc1b192d5ab88c1eee1350eab93431135d2fde406b92a`.
Среда: macOS, Python 3.14; повторная Linux-приёмка этого этапа не выполнялась.

В реальном stdio-процессе установленного пакета проверены 9 tools, submit atoms,
restart, submit mapping, restart, finalize, повтор того же finalize и get_result.
Подключения socket запрещены тестовым guard. Deep KB и каталог синтетические,
подписаны одноразовым ключом в временном каталоге; доверие не переносится в поставку.

Результат: один точный запрос создания ВМ через API — `supported`; семь
контрастов (latency, delete, user, запрет, GUI, отказ узла, GPU) —
`insufficient_evidence`. Итог `PARTIAL` корректен для deep.

Session ID: `75a4102e-6de4-47a6-ae77-62b65f6a908c`.

| Файл | Проверенный SHA-256 |
| --- | --- |
| `result.json` | `2ca0a83116d696b2a8acbc295881c2231ba67046d3bcab673b3c2948d31d86b2` |
| `result.xlsx` | `2e1c320c07c2e2e23fd32a805f6410e22e86aa6e95237537364322765b9daf67` |
| `report.md` | `3784333ee16f47e8ac8bd9b8d74183b76a980dd897f1bf279b8b53f2214d13b8` |
| `run.jsonl` | `cb0eb272447f7f95231913589c9959b58b3521b598f6c457c05a6fcf63e92740` |
| `manifest.json` | `7788d6aa08cdce12555b721424513c8caa800a962c25552085397e374ffbda90` |

Все четыре hashes в manifest совпали с байтами файлов; сам manifest проверен
отдельно, get_result подтвердил комплект после restart.

Synthetic KB manifest: `3c49180ab934f8dbb447a8a5f240bbcfc2a891327e2af8241ec23df5c52a080b`.
Catalog manifest: `6638433d58d6371b7131a82d02cd82cd46ace2266f9fd4885b97f7ead7150564`.
Grammar: `bd73e418f45178658a0519571194f4c7852449cdef92d76f3b8e47bd27050f17`.
