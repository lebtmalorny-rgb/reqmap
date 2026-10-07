# KEYSTONE-PROJECT: API capability notes

- Release: Epoxy 2025.1; keystone 27.0.0.
- Commit: `bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a`.
- URL: https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/projects.inc
- Release manifest: https://releases.openstack.org/epoxy/#keystone
- Retrieved: 2026-10-07; upstream file SHA-256: `7e19c70746bcdd7bb51915438fd9bde49881d8617880ff460f24425c2afc31c0`.
- Исходник по commit сверён побайтово с официальным GitHub-зеркалом по tag 27.0.0.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не гарантирует успешное выполнение запроса: действуют аутентификация, policy, backend-драйвер, состояние и параметры объекта. Пакет не доказывает GUI, интеграцию LDAP/AD, учётные записи гипервизора, назначение ролей или фактическую авторизацию.

Projects, L7–41: проект задаёт владение ресурсами и принадлежит домену; имя уникально в пределах домена и ограничено 64 символами. Здесь рассматривается обычный проект. Создание проекта не доказывает назначение ролей, изменение квот или прав доступа.

- `POST /v3/projects` — Создание проекта; Create project, L109–181.
- `GET /v3/projects/{project_id}` — Получение сведений о проекте; Show project details, L182–257.
- `PATCH /v3/projects/{project_id}` — Переименование проекта; Update project, L258–332. Поле запроса `name`.
- `DELETE /v3/projects/{project_id}` — Удаление проекта; Delete project, L333–368.
