"""Версионированные системные инструкции локальной модели."""

PROMPT_DECOMPOSITION_VERSION = "2.0"
PROMPT_MAPPING_VERSION = "1.3"
PROMPT_DEEP_MAPPING_VERSION = "2.1"

DECOMPOSITION_PROMPT = """Выбери canonical atoms из полной source_binding без изменения семантики.
Верни только JSON object с proposal_schema_version=2 и atoms по canonical_proposal.
source_quote и source_span должны точно соответствовать обязательству backend. Смещения измеряются
в Unicode code points, конец исключён. mandatory всегда true. Нельзя опускать условия и отрицание,
делить обязательство или переносить source_quote из parent_text. parent_text неавторитетен и служит
только контекстом. text является неавторитетной меткой; backend сохраняет точную исходную цитату.
"""

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

role_ru описывает доказанную возможность компонента, action_ru — доказанный шаг. Эти поля
проверяются лексически по cited official specific evidence и его capability: каждое значимое
слово должно присутствовать в этом корпусе. Для role_ru и action_ru используй подходящий
claim_ru выбранного evidence дословно либо формулировку из того же корпуса. Не копируй в них
исходное требование с нормативными словами вроде «должна». source_quote сохраняет полное
требование без изменений, включая условия и отрицания; описание evidence их не заменяет.
Наличие подходящего текста роли не доказывает покрытие исходного обязательства.
MAPPING_ROLE_UNGROUNDED и MAPPING_STEP_UNGROUNDED указывают поле и непокрытые слова: исправляй
только описание в новом proposal, сохраняя source_quote; повторно оцени покрытие предикатом.

Каждый supported_aspect в proposal должен в точности совпадать с role_ru одного подтверждённого mapping;
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


_BINDING_INSTRUCTION = """
Если payload содержит source_binding, используй proposal_schema_version=2, точный obligation_id
и predicate_ids только из переданного каталога predicates. Не изменяй исходные source spans,
семантические поля и обязательность. Один predicate должен покрыть действие, объект, направление,
интерфейс и все условия целого обязательства. Нельзя объединять разные predicates в новую гарантию.
Без подходящего предиката предложи insufficient_evidence. supported_aspects в результате вычисляет
backend из исходных обязательств; свободный текст и заявленный статус не подтверждают поддержку.
"""
MAPPING_PROMPT += _BINDING_INSTRUCTION
DEEP_MAPPING_PROMPT += _BINDING_INSTRUCTION
