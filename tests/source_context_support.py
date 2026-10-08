"""Explicit synthetic source documents; no implicit approval of real inputs."""
from io import BytesIO

from openpyxl import Workbook

from reqmap.export_json import canonical_json_bytes
from reqmap.ids import generated_requirement_id
from reqmap.models import Requirement, SourceCoordinate


API_TEXT = "Nova должна создавать ВМ через API"
DETACHED_TEXT = "Nova должна создавать ВМ"


def text_document(texts=(API_TEXT,), source_name="synthetic-texts"):
    rows = tuple(Requirement(generated_requirement_id(i), None, text, i,
                            SourceCoordinate(source_name, None, i))
                 for i, text in enumerate(texts, 1))
    return dict(content=canonical_json_bytes(list(texts)), source_kind="texts",
                source_name=source_name, requirements=rows, input_profile=None)


def workbook_bytes():
    book = Workbook()
    for i, name in enumerate(("Первый", "Второй")):
        sheet = book.active if i == 0 else book.create_sheet()
        sheet.title = name
        sheet.append(["ID", "Требование", "Условие", "Подсказка"])
        sheet.append(["same-id", "😀GUI и GUI", "при отказе узла", "контекст"])
        sheet.append(["same-id", DETACHED_TEXT, "через GUI", "Nova"])
    stream = BytesIO()
    book.save(stream)
    book.close()
    return stream.getvalue()
