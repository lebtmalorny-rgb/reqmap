# Исследование связи обязательства и доказательства — 05–06.10.2026

Краткий вывод: один лишь переход на deep не закрывает дефект. Оба текущих
профиля принимают корректную операцию из synthetic KB как подтверждение
другого исходного обязательства. Проверяемый прототип общего source binding
отклоняет изменённые и нераспознанные условия, сохраняя разрешённые формы.
Runtime reqmap пока не изменён.

Исходный код: `c35cc65fe9eae12738133057493954e020a4d707`, после защиты
от общих context records. Здесь использованы **synthetic specific evidence**,
а не поставляемые общие описания сервисов и не пользовательские XLSX.

## Воспроизведение исходного дефекта

Через `retrieve`/`retrieve_deep` найдены кандидаты; затем в соответствующий
`accept_mapping`/`accept_deep_mapping` подано штатное proposal создания VM.
Это искусственный некорректный proposal, а не измерение поведения настоящей LLM.

| Исходное обязательство, сокращённо | Legacy | Deep | Прототип обоих адаптеров |
| --- | --- | --- | --- |
| Создавать VM через API | supported | supported | supported |
| Обеспечивать создание VM через API | supported | supported | supported |
| Телепортировать VM | supported | supported | insufficient_evidence |
| Создавать пользователя | supported | supported | insufficient_evidence |
| Не создавать VM | supported | supported | insufficient_evidence |
| Создавать VM за одну миллисекунду | supported | supported | insufficient_evidence |
| Создавать VM через GUI | supported | supported | insufficient_evidence |
| Создавать VM при отказе узла | supported | supported | insufficient_evidence |
| Создавать VM с GPU | supported | supported | insufficient_evidence |
| Создавать VM без ограничений | supported | supported | insufficient_evidence |

В полном наборе 28 текстов: 5 положительных контролей и 23 случая, для которых
политика конечной грамматики требует воздержания. Оба текущих профиля вернули
supported для 27 текстов и отклонили proposal для одного контекстного фрагмента
из-за отсутствия кандидата. Среди 23 случаев воздержания 22 получили supported
в baseline; **это не 22 доказанных предметных ложноположительных результата**:
часть случаев — допустимые, но пока не распознаваемые перефразировки.
В таблице выше показаны конкретные изменения обязательства и два контроля.

Отдельно `accept_decomposition` принял атом без хвоста `за одну миллисекунду`
и с `mandatory=False`. Цитата была точной подстрокой, но полное требование
исчезло из обязательной части анализа. Поэтому проверка только mapping
недостаточна: источник нужно связывать целиком до декомпозиции.

## Что проверяет прототип

Файлы: [probe](../../tools/research/obligation_binding_probe.py),
[набор фраз](../../tools/research/obligation_binding_cases.json).

- Полный match конечной грамматики без удаления суффиксов, чисел и отрицаний.
- Точное сохранение source quote/hash/span; неизвестный текст остаётся gap.
- Сравнение action/object/direction/interface и условий с вручную заданным
  synthetic predicate. Для обоих профилей задан один и тот же semantic input;
  это не автоматическое извлечение смысла из KB.
- Отсутствие evidence, context-only, unreviewed, версия, contour и обратная
  применимость условного claim проверяются отдельно.
- Два claims «создание API» и «удаление GUI» не складываются в «создание GUI».
- Полнота spans, пересечения, потерянный хвост и повторное вхождение цитаты.

Итого прошли **56 проверок текст/profile**, **8 evidence controls** и
**5 source-coverage controls**. Пять разрешённых формулировок сохраняют supported
в каждом адаптере; остальные 23 получают insufficient в каждом адаптере.
Это контролируемая проверка механизма на данном наборе, не оценка полноты KB.

Baseline для затронутых существующих подсистем:
`tests/test_decomposition.py`, `tests/test_mapping.py`, `tests/test_deep_mapping.py`
— **138 passed**. После изменения только исследовательских файлов runtime и
knowledge совпадают с `c35cc65`. Полный suite из 1091 теста был проверен на
первом этапе; в этом исследовании его результат не выдаётся за новый запуск.

## Воспроизводимый запуск

Из корня репозитория после обычной offline-установки:

```bash
PYTHONPATH=src .venv/bin/python tools/research/obligation_binding_probe.py \
  --output results/obligation-binding/probe.json
```

Скрипт использует проектные test helpers и временную копию synthetic deep
snapshot с временным SSH signer. Сеть, внешняя LLM, OpenStack и IDE не нужны.
Он проверяет исходное поведение на зафиксированном baseline: после внедрения
нового runtime ожидания baseline нужно пересмотреть, не скрывая изменение.

| Артефакт | SHA-256 |
| --- | --- |
| `obligation_binding_probe.py` | `49b397d1e6241518ad911840207e86f39ebb9c9b3741bcaa4fe674b229436efe` |
| `obligation_binding_cases.json` | `3d6f23911a0a8c4cd727ca1c4f6e2613ed36068f8504710f7178824d5f5c02b8` |
| Локальный `probe.json` | `97fa5134d29d3ba809f063928b6a9b1d80a8716624aeae421cf59e292ede0bdc` |

## Границы и решение

Рекомендуется [общий контракт](../superpowers/specs/2026-10-05-obligation-binding-design.md)
с source binding до декомпозиции и отдельной проверенной разметкой claims.
[План внедрения](../superpowers/plans/2026-10-05-obligation-binding-implementation.md)
фиксирует миграцию и RED/GREEN критерии.

Прототип исследовательский: не содержит production loader/trust каталога,
типизированной модели всех constraints, отрицательных доказательств,
интеграции CLI/MCP, replay и новых export schemas. Coverage helper проверен
отдельно и ещё не встроен в реальную декомпозицию. Исследовательские annotations
не проверялись по внешним источникам и не предназначены для реальной KB.
Неизвестная допустимая перефразировка остаётся insufficient, а не not_supported.
Политика точного совпадения численных условий намеренно не реализует
математические импликации между разными границами/единицами.

Самопроверка проекта выполнена автором; отдельное агентное ревью исследования
не запускалось. Настоящая IDE/model приёмка и заполнение базы остаются
следующими этапами после внедрения согласованного контракта.
