# CINDER-VOLUME: API capability notes

- Release: Epoxy 2025.1; cinder 26.0.0.
- Commit: `06e8c0b5049cd978018740f4794cb162c7043540`.
- URL: https://opendev.org/openstack/cinder/raw/commit/06e8c0b5049cd978018740f4794cb162c7043540/api-ref/source/v3/volumes-v3-volumes.inc
- Retrieved: 2026-10-07; upstream file SHA-256: `a6a7042c174a6fffab8ecb2add510e6863c5520bacfd7be070b52891c2f31ed5`.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не означает успех вызова на произвольном стенде: действуют аутентификация, policy, состояние ресурсов и параметры запроса.

Создание и удаление асинхронны (202). Создание требует доступной квоты и volume type (возможен default). Удаление ограничено состоянием, миграцией, attachments, snapshots и группой. PUT name не доказывает resize; чтение требует существующего тома.

- `POST /v3/{project_id}/volumes` — Создание блочного тома; Create a volume, L160-282.
- `GET /v3/{project_id}/volumes/{volume_id}` — Получение сведений о блочном томе; Show a volume's details, L347-426.
- `PUT /v3/{project_id}/volumes/{volume_id}` — Переименование блочного тома; Update a volume, L427-505. Поле запроса `name`.
- `DELETE /v3/{project_id}/volumes/{volume_id}` — Удаление блочного тома; Delete a volume, L506-563.
