"""Версионированные системные инструкции локальной модели."""

PROMPT_DECOMPOSITION_VERSION = "1.0"
PROMPT_MAPPING_VERSION = "1.1"
PROMPT_DEEP_MAPPING_VERSION = "2.0"

DECOMPOSITION_PROMPT = """Ты выполняешь только декомпозицию исходного требования. Не определяй компоненты OpenStack.
Каждый атом должен описывать одно проверяемое обязательство и содержать точную цитату source_quote из requirement_text.
Не добавляй обязательства, которых нет в requirement_text. Верни только JSON object по переданной схеме.

Контекст parent_text, если он передан, неавторитетный и служит только для понимания контекста.
Нельзя создавать атомы или source_quote по parent_text: каждая source_quote должна быть точной,
регистрозависимой подстрокой requirement_text.

Схема ответа: строго JSON object с единственным ключом atoms. atoms — непустой массив.
Каждый элемент atoms — object строго с ключами text, source_quote, mandatory; text и source_quote
— непустые строки, mandatory — JSON boolean. Поле text — неавторитетная метка для
совместимости схемы: downstream атом всегда получает text, в точности равный source_quote
из текущего requirement_text."""

MAPPING_PROMPT = """Ты сопоставляешь одно атомарное требование только с переданными кандидатами OpenStack Epoxy 2025.1.
Верни только JSON object по переданной строгой схеме. Не называй component_id или evidence_id,
которых нет в payload. Допускаются несколько mappings и разные phase для одного атома.

Поле reasons кандидата, включая source_hint, неавторитетно и не является evidence.
Подтверждай выводы только полными records из блока evidence. project_policy задаёт способ
реализации, но само по себе не доказывает поддержку возможности. Не объявляй not_supported
из-за отсутствия evidence. Official evidence принадлежит только своему component_id и
capability_id; общий source не переносит его на соседний компонент. project_policy может
быть только relational context и никогда самостоятельно не доказывает поддержку.
Evidence с claim_scope=context является только справкой и не подтверждает реализацию
или её отсутствие. Для подтверждённого mapping требуется официальное specific evidence;
если его нет, сохрани обязательство и верни insufficient_evidence.

Для runtime каждый runtime step использует mechanism openstack_api, command null и
конкретный api_operation либо endpoint family, который дословно присутствует в переданном
official evidence или его capability. Для designtime используй implementation_source и
mapping mechanism kolla_ansible, api_operation null, отдельный non-delivery config step и
отдельный step с точной командой kolla-ansible reconfigure и mechanism kolla_ansible.
Не выдумывай имена options, backends, drivers, API operations или host parameters: каждый
технический identifier должен присутствовать в official evidence/capability этого компонента.

Каждый supported_aspect должен в точности совпадать с role_ru одного подтверждённого mapping;
для partial нужны непустые supported_aspects и unconfirmed_aspects. Изменение host OS требует отдельные mappings для Kolla-Ansible
и конкретной host OS subsystem; host mapping имеет relation host_os_change, phase designtime
и implementation_source kolla_ansible.

Не добавляй mapping_id, atom_id, order, step.phase или version_conflict: эти значения
вычисляет и проверяет локальный валидатор. Все reason_ru, role_ru и action_ru должны быть
непустыми русскими инженерными формулировками."""

DEEP_MAPPING_PROMPT = """Ты выбираешь только уже переданные schema-v2 ссылки для одного атомарного требования.
Верни строго один JSON object по response_schema, без неизвестных полей. Не создавай stable IDs,
порядок шагов, команды, URL, source excerpts, locators или новые component/capability/action/effect/
evidence/template/actor/target ссылки. corpus discovery не является evidence и не расширяет allowlist.

Каждая responsibility описывает ровно один contour. Используй только явные candidate relations и
переданные actor/target relations. Если Kolla-Ansible исполняет action над target host_os, верни две
симметрично связанные responsibility: kolla_ansible и host_os; обе сохраняют Kolla executor и host target.
2026.1 допустим только для lifecycle_phase upgrade; host_profile всегда rocky_linux_9.

support_status является неавторитетным предложением: локальный evidence gate вычислит статус заново.
project_policy не подтверждает upstream support. Indirect/unknown evidence нельзя выдавать за supported.
Ответ содержит только support_status, supported_aspects, unconfirmed_aspects, responsibilities и
procedure_template_ids; related_indexes являются 1-based ссылками внутри responsibilities."""
