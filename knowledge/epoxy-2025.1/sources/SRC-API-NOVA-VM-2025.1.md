# NOVA-VM: API capability notes

- Release: Epoxy 2025.1; nova 31.0.0.
- Commit: `6042300453411133652dd8d3b789f5057a084075`.
- URL: https://opendev.org/openstack/nova/raw/commit/6042300453411133652dd8d3b789f5057a084075/api-ref/source/servers.inc
- Retrieved: 2026-10-07; upstream file SHA-256: `4d7cf81d85fab7a3e348e0e26bef650f27b76ffd304c91c37bdf2ce3d41e3887`.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не означает успех вызова на произвольном стенде: действуют аутентификация, policy, состояние ресурсов и параметры запроса.

Создание асинхронно (202), зависит от квот, образа, ресурсов и scheduler. Удаление зависит от policy/lock и reclaim_instance_interval; 204 не гарантирует немедленное физическое удаление. Изменение name отдельно разрешено.

- `POST /servers` — Создание ВМ; Create Server, L284-474.
- `GET /servers/{server_id}` — Получение сведений о ВМ; Show Server Details, L715-854.
- `PUT /servers/{server_id}` — Переименование ВМ; Update Server, L855-976. Поле запроса `name`.
- `DELETE /servers/{server_id}` — Удаление ВМ; Delete Server, L977-1045.
