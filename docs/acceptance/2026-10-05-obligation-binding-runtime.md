# Приёмка source-to-evidence binding — 07.10.2026

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
`git diff --check`: чисто. Это прогон до независимого ревью; итог исправлений
приведён ниже.

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

Session ID: `dae775b4-510e-4b21-9091-4154406e0897`.

| Файл | Проверенный SHA-256 |
| --- | --- |
| `result.json` | `eef838533445ea4da0e07b34123ed7a1cde0c02d0c8ccf1cb34bd0c6233db184` |
| `result.xlsx` | `11300f8b4ea11a748e277d7be3d53d5ce11decacfb42e027c0428a8fe13a6cee` |
| `report.md` | `48a0aa9c500b867388f806bedf3dfde22b50ffc5042efbf52cce111504a2e2bf` |
| `run.jsonl` | `6419c7bfba56f0aa1e18cb548b5de6ff2a2a355b25e27e572f88834b790aa1a8` |
| `manifest.json` | `b253435634851eadd5985295f749ee644cd607be8aa68a1c4d8960f0fe49a68c` |

Все четыре hashes в manifest совпали с байтами файлов; сам manifest проверен
отдельно, get_result подтвердил комплект после restart.

Synthetic KB manifest: `3c49180ab934f8dbb447a8a5f240bbcfc2a891327e2af8241ec23df5c52a080b`.
Catalog manifest: `6638433d58d6371b7131a82d02cd82cd46ace2266f9fd4885b97f7ead7150564`.
Grammar: `bd73e418f45178658a0519571194f4c7852449cdef92d76f3b8e47bd27050f17`.


## Независимое ревью и исправления

Один новый reviewer `gpt-6-astra` проверил всю ветку `434c76e..32a26cb`
read-only, без подагентов. Выявлены четыре Important, Critical/Minor отсутствуют.
В одном проходе исправлены все четыре:

| Замечание | Проверка исправления |
| --- | --- |
| Агент мог скрыть negative predicate и получить supported | Backend проверяет противоречия полных предикатов выбранной relation независимо от выбранных refs. Отдельный контраст скрывает также negative evidence; конфликт остаётся. Persisted decision воспроизводится без изменения |
| Negative API подтверждал not_supported без ограничения интерфейса | Общее отрицание и общий запрет не выводятся из одного интерфейса; positive capability existence остаётся допустимым |
| Assumption о duration превращалось в гарантию duration | Непустые assumptions блокируют proof; сравнение constraints использует только гарантии |
| Исходная строка 8061 символ ломала finalize из-за одной XLSX-ячейки | Длинные ячейки разбиваются на фрагменты листа «Запуск» с SHA-256. Оба профиля публикуются, исходные строки/JSON восстанавливаются точно, изменение фрагмента отклоняется |

Адресный RED: 13 падений; два дополнительных стрессовых случая сначала упирались
в отдельный лимит MCP, затем с увеличенным тестовым лимитом воспроизвели именно
ошибку XLSX. Все четыре проверки длинных строк были RED на публикации. Первый
GREEN: 128 passed. Сокрытие одновременно evidence и predicate — отдельный RED,
после исправления binding/session checks: 98 passed. Повторное независимое ревью
не проводилось; исправления подтверждаются регрессионными тестами.

После исправлений повторены offline install, pip check, knowledge validate и
installed MCP control выше; файлы `binding_engine.py`/`xlsx_text.py` установленного
пакета побайтно совпали с проверяемой исходной версией. Производственная конфигурация
и исходные книги этим тестом не изменялись.

Дополнительные решения и ограничения:

5. `unspecified` допускает existence proof только для positive capability.
   Цена консервативного правила — недостаточность для общих отрицаний до
   появления проверенного правила scope.
6. Assumptions пока нельзя считать доказанными из исходного требования.
   Цена — дополнительные insufficient для корректных условных случаев;
   отдельная модель предусловий остаётся будущей работой.
7. Reviewer не оценивал production predicates, реальный импорт XLSX и IDE:
   они явно принадлежат этапам 3–5. Эти этапы остаются невыполненными;
   предметное покрытие и живая работа клиента после обновления не заявляются.


Итоговый полный прогон после единственного fix pass:
`PYTHONPATH=src .venv/bin/python -m pytest -q` — **1246 passed**, три прежних
предупреждения fork, 127.27 s. Документация и проверка скрытого конфликта
дополнительно прошли после записи отчёта. Незакрытых Important/Critical нет;
отложенных Minor нет. Это основание для включения ветки в main.
