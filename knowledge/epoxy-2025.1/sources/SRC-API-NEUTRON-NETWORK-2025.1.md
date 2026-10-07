# NEUTRON-NETWORK: API capability notes

- Release: Epoxy 2025.1; neutron-lib 3.18.2.
- Commit: `bf21a6dcd48bdd15c28086f256319ac035b7fef0`.
- URL: https://opendev.org/openstack/neutron-lib/raw/commit/bf21a6dcd48bdd15c28086f256319ac035b7fef0/api-ref/source/v2/networks.inc
- Retrieved: 2026-10-07; upstream file SHA-256: `08b8edd4804ef3e4d8676a80d5e94d19c199a63294d1b78a8267742d3179430f`.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не означает успех вызова на произвольном стенде: действуют аутентификация, policy, состояние ресурсов и параметры запроса.

Создание возвращает 201. PUT включает name; изменение provider-атрибутов и MTU имеет отдельные ограничения и здесь не подтверждается. DELETE возвращает 204, возможны конфликты 409/412; произвольные связанные ресурсы не обещано удалять.

- `POST /v2.0/networks` — Создание сети; Create network, L493-605.
- `GET /v2.0/networks/{network_id}` — Получение сведений о сети; Show network details, L191-265.
- `PUT /v2.0/networks/{network_id}` — Переименование сети; Update network, L266-362. Поле запроса `name`.
- `DELETE /v2.0/networks/{network_id}` — Удаление сети; Delete network, L363-385.
