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


def map_for(document, *, disposition="independent", target=0, links=(), reviewed=True):
    """Approve only the explicitly selected synthetic row, never all inputs."""
    import hashlib
    from reqmap.models import to_dict
    row = document.requirements[target]
    ref = dict(requirement_id=row.requirement_id, coordinate=to_dict(row.coordinate),
               source_sha256=hashlib.sha256(row.text.encode()).hexdigest())
    entry = dict(target=ref, disposition=disposition,
                 review_state="reviewed" if reviewed else "draft",
                 reviewed_by="synthetic-maintainer" if reviewed else None,
                 reviewed_at="2026-10-08T00:00:00Z" if reviewed else None,
                 reason_ru="Проверка синтетического примера целиком",
                 context_complete=reviewed and disposition != "unresolved", links=list(links))
    return dict(schema_version="1.0", map_id="synthetic-map",
                source_kind=document.source_kind, source_name=document.source_name,
                input_sha256=document.input_sha256, input_profile_sha256=document.input_profile_sha256,
                requirements_sha256=document.requirements_sha256, rows=[entry])


def link_for(document, origin=1, *, field="interface", value="gui", link_id="link-1", start=0, end=None):
    text = document.requirements[origin].text
    end = len(text) if end is None else end
    return dict(link_id=link_id, field=field, value=value,
                origin=dict(row=map_for(document, target=origin)["rows"][0]["target"],
                            span=dict(start=start, end=end, quote=text[start:end])))
