"""Неизменяемая загрузка требований из текста."""

from typing import Literal

from reqmap.ids import generated_requirement_id
from reqmap.models import Requirement, SourceCoordinate


def load_text(
    text: str, mode: Literal["single", "lines"], source_name: str
) -> tuple[Requirement, ...]:
    """Возвращает требования, не меняя исходный текст и порядок строк."""
    if mode == "single":
        values = [(text.removesuffix("\n"), 1)]
    elif mode == "lines":
        values = [
            (line, line_number)
            for line_number, line in enumerate(text.splitlines(), start=1)
            if line.strip()
        ]
    else:
        raise ValueError("Режим текста должен быть single или lines")

    return tuple(
        Requirement(
            requirement_id=generated_requirement_id(ordinal),
            source_id=None,
            text=value,
            ordinal=ordinal,
            coordinate=SourceCoordinate(
                source_name=source_name,
                sheet=None,
                row=source_row,
            ),
        )
        for ordinal, (value, source_row) in enumerate(values, start=1)
    )
