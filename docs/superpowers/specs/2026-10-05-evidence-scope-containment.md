# Ограничение подтверждений по общим evidence: первый этап

Изменение закрывает воспроизведённый дефект из
[аудита](../../acceptance/2026-10-05-requirements-coverage-audit.md).
Оно не заменяет последующее проектирование семантической связи со всеми
условиями исходного требования.

## Контракт

- Legacy Evidence получает `claim_scope`: `context` или `specific`.
  `context` — общая справка, область ответственности или проектное правило.
  `specific` — проверенное ограниченное утверждение о конкретной возможности
  либо ограничении. Scope не заменяет polarity, strength, version и provenance.
- В старой записи без `claim_scope` применяется `context`. Неизвестные значения,
  null, boolean, числа и коллекции отклоняются loader. Прямые объекты проходят
  ту же проверку в `validate_knowledge`.
- Context records сохраняются в retrieval, `get_evidence` и prompt payload.
  Агент не может изменить классификацию через proposal.
- Только официальные specific records участвуют в положительном/отрицательном
  подтверждении, проверке version conflict и корпусе grounding. Context text
  нельзя добавить к specific evidence, чтобы обосновать отсутствующую операцию.
- Только context evidence даёт `insufficient_evidence` с диагностикой
  `EVIDENCE_CONTEXT_ONLY`. Отсутствие доказательств не становится `not_supported`.
- В поставляемой KB 22 общие service records, факт наличия sysctl role и 7
  project-policy records являются context. Остальные 7 официальных записей
  имеют specific scope в пределах уже записанных claims. Новые claims не добавляются.
- Synthetic fixtures, в которых проверяется положительная конкретная операция,
  явно объявляют specific scope. Тесты на поставляемой KB сохраняют её реальные
  ограничения и больше не ожидают поддержанную Nova-операцию по scope record.

## Совместимость

Поле scope добавляется к Evidence в JSON schema 1.0 и MCP payload; строгие
потребители должны разрешить это поле. Старые snapshots загружаются, но их
неразмеченные evidence не подтверждают поддержку. Новый snapshot не совместим
со старым строгим loader: требуется обновить установленный пакет.

Версия legacy mapping prompt изменяется, чтобы обновились context IDs и
подпись CLI resume. Изменённый snapshot получает новый aggregate hash, поэтому
старые сессии на прежней базе не продолжаются с новой KB незаметно.
Deep schema, её собственные evidence и synthetic fixtures не переопределяются.

## Проверка

Регрессии воспроизводят 22 ошибочных подтверждения, ограничение времени и
submit/restart/finalize/get_result со сверкой JSON/XLSX/Markdown.
Дополнительно проверяются старые snapshots, неверные значения scope,
смесь context и specific records, отрицательное evidence и положительный
контроль конкретной операции. Полный suite и установка проверяются отдельно.
