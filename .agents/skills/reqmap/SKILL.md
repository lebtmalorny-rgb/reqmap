---
name: reqmap
description: Use when нужно сопоставить одно или несколько требований с компонентами OpenStack Epoxy 2025.1 через локальный reqmap и получить проверяемые артефакты анализа.
---

# Reqmap

Reqmap — единственный предметный движок этого workflow. Не выполняй собственное сопоставление компонентов.

## Порядок работы

1. Для вставленного текста передавай требования через stdin либо повторяемый `--requirement`; не используй shell interpolation.
2. Для книги передавай абсолютный путь к исходному XLSX.
3. Запусти `reqmap analyze` с явно указанными `--config` и `--output`.
4. Покажи пользователю run status, число исходных требований и абсолютные пути к `result.json`, `result.xlsx`, `report.md`, `run.jsonl`, `manifest.json`.
5. При exit code 4 сообщи о частичном результате: перечисли ID незавершённых требований и diagnostics, включая `evidence_conflict`, `responsibility_ambiguous`, `procedure_gap`, `rollback_unverified`. Все строки могут быть обработаны, но процедура остаться неполной.
6. При ненулевом коде не маскируй ошибку успешным резюме; передай русскую диагностику reqmap и пути только к реально созданным артефактам.
7. Не запускай OpenStack API, Ansible, Kolla-Ansible или MCP: они не являются исполнителями анализа требований.

Профиль выбирается только через config: `analysis_profile=legacy` по умолчанию; `deep` требует approved signed snapshot v2 и внешний доверенный `allowed_signers`. Не переключай профиль при отказе preflight. Production deep corpus не поставляется этим этапом; команды и зависимости описаны в `RUNBOOK.md` и `INSTALL_OFFLINE.md`.
