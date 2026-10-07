# NEUTRON-PORT: API capability notes

- Release: Epoxy 2025.1; neutron-lib 3.18.2.
- Commit: `bf21a6dcd48bdd15c28086f256319ac035b7fef0`.
- URL: https://opendev.org/openstack/neutron-lib/raw/commit/bf21a6dcd48bdd15c28086f256319ac035b7fef0/api-ref/source/v2/ports.inc
- Retrieved: 2026-10-07; upstream file SHA-256: `aeeae4c9402ea9c916e12985ecb5df017eecceedd878c8e8cd2cc3278c65bc4c`.
- Ниже — проверенный пересказ указанных разделов, не дословная цитата.

Наличие операции API не означает успех вызова на произвольном стенде: действуют аутентификация, policy, состояние ресурсов и параметры запроса.

Создание требует network_id, возвращает 201. PUT принимает name. DELETE освобождает связанные IP в пулы подсетей и возвращает 204. Эти операции не доказывают host LACP, AD/LDAPS или функции внешнего портала.

- `POST /v2.0/ports` — Создание сетевого порта; Create port, L602-717.
- `GET /v2.0/ports/{port_id}` — Получение сведений о сетевом порте; Show port details, L250-327.
- `PUT /v2.0/ports/{port_id}` — Переименование сетевого порта; Update port, L328-466. Поле запроса `name`.
- `DELETE /v2.0/ports/{port_id}` — Удаление сетевого порта; Delete port, L467-492.
