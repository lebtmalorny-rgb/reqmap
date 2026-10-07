# KEYSTONE-ROLE: API capability notes

- Release: Epoxy 2025.1; keystone 27.0.0.
- Commit: `bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a`.
- URL: https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/roles.inc
- Release manifest: https://releases.openstack.org/epoxy/#keystone
- Retrieved: 2026-10-07; upstream file SHA-256: `e6ad252afa4d7d1c3241c793c7a77ca9ebea946d7080cbc6992e0eed9d6f1121`.
- Исходник по commit сверён побайтово с официальным GitHub-зеркалом по tag 27.0.0.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не гарантирует успешное выполнение запроса: действуют аутентификация, policy, backend-драйвер, состояние и параметры объекта. Пакет не доказывает GUI, интеграцию LDAP/AD, учётные записи гипервизора, назначение ролей или фактическую авторизацию.

Доказательства относятся к объекту роли. Role assignment, effective policy, наследование ролей, область токена и доступ к Nova/Cinder требуют самостоятельных доказательств. Переименование изменяет только name, а не набор политик.

- `POST /v3/roles` — Создание роли; Create role, L158–223.
- `GET /v3/roles/{role_id}` — Получение сведений о роли; Show role details, L224–279.
- `PATCH /v3/roles/{role_id}` — Переименование роли; Update role, L280–346. Поле запроса `name`.
- `DELETE /v3/roles/{role_id}` — Удаление роли; Delete role, L347–382.
