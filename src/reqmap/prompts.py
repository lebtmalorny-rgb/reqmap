"""Версионированные системные инструкции локальной модели."""

PROMPT_DECOMPOSITION_VERSION = "1.0"

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
