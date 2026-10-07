# KEYSTONE-USER: API capability notes

- Release: Epoxy 2025.1; keystone 27.0.0.
- Commit: `bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a`.
- URL: https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/users.inc
- Release manifest: https://releases.openstack.org/epoxy/#keystone
- Retrieved: 2026-10-07; upstream file SHA-256: `a6db31166611abbb864284352fdd04b3576011adcba815e489f1ea7fc2d13a5a`.
- Исходник по commit сверён побайтово с официальным GitHub-зеркалом по tag 27.0.0.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не гарантирует успешное выполнение запроса: действуют аутентификация, policy, backend-драйвер, состояние и параметры объекта. Пакет не доказывает GUI, интеграцию LDAP/AD, учётные записи гипервизора, назначение ролей или фактическую авторизацию.

Users, L7–17: учётная запись относится к домену; назначение ролей связано с доступом к ресурсам. Само создание пользователя не подтверждает выдачу прав. Update user, L224–226: backend-драйвер может не поддерживать изменение, тогда возможен HTTP 501. Это не доказательство записи в LDAP/AD или создания учётной записи ОС.

- `POST /v3/users` — Создание пользователя; Create user, L87–159.
- `GET /v3/users/{user_id}` — Получение сведений о пользователе; Show user details, L160–217.
- `PATCH /v3/users/{user_id}` — Переименование пользователя; Update user, L218–294. Поле запроса `name`.
- `DELETE /v3/users/{user_id}` — Удаление пользователя; Delete user, L295–332.
