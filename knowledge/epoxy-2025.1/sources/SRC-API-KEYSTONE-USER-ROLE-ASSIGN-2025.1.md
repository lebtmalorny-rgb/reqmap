# KEYSTONE-USER-ROLE-ASSIGN: API capability notes

- Release: Epoxy 2025.1; keystone 27.0.0.
- Commit: `bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a`.
- URL: https://opendev.org/openstack/keystone/raw/commit/bd2b97a0412b8ea01ef47fd8c0c4908ca9fcee8a/api-ref/source/v3/roles.inc
- Release manifest: https://releases.openstack.org/epoxy/#keystone
- Retrieved: 2026-10-07; upstream file SHA-256: `e6ad252afa4d7d1c3241c793c7a77ca9ebea946d7080cbc6992e0eed9d6f1121`.
- Исходник по commit сверён побайтово с официальным GitHub-зеркалом по tag 27.0.0.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

- `PUT /v3/projects/{project_id}/users/{user_id}/roles/{role_id}` — Назначение роли пользователю проекта; Assign role to user on project, L936–975. Параметры пути определяют проект, пользователя и роль. Документированы успешный ответ 204 и ошибки 400, 401, 403, 404, 409.
- `PUT /v3/domains/{domain_id}/users/{user_id}/roles/{role_id}` — Назначение роли пользователю домена; Assign role to user on domain, L606–642. Параметры пути определяют домен, пользователя и роль. В данном закреплённом API-источнике документированы успешный ответ 200 и ошибки 400, 401, 403.

Наличие операции не гарантирует успешный вызов: действуют аутентификация, policy, существование и допустимость указанных объектов. Project assignment и domain assignment — разные области назначения. Назначение роли на домен само по себе не подтверждает наследование роли проектами.

Операции создают назначения существующих ролей пользователям. Они не доказывают создание или модификацию объекта роли, конкретный набор эффективных разрешений, корректность policy.yaml других сервисов, управление сущностями региона, предопределённые роли, GUI, LDAP/AD либо фактический доступ пользователя к Nova/Cinder на стенде.
