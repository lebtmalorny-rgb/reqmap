"""Lossless long-cell transport within the existing XLSX run sheet.

Chunk strings are deliberately short enough for both Unicode code point and
UTF-16 Excel limits. Expected rows include every fragment and its original
UTF-8 hash, so value/count comparisons also reject orphaned or altered chunks.
"""
import hashlib
import json


def encode_long_cells(rows_by_sheet):
    encoded, chunks = {}, []
    for sheet_index, (sheet, rows) in enumerate(rows_by_sheet.items(), 1):
        converted = []
        for row_index, row in enumerate(rows, 2):
            cells = []
            for column, value in enumerate(row, 1):
                if isinstance(value, str) and len(value.encode('utf-16-le')) > 32767 * 2:
                    key = f'xlsx-text:{sheet_index}:{row_index}:{column}'
                    fragments = [value[i:i+16000] for i in range(0, len(value), 16000)]
                    chunks.extend((f'{key}:{i}', part) for i, part in enumerate(fragments, 1))
                    value = json.dumps(dict(xlsx_text_key=key, parts=len(fragments),
                                            sha256=hashlib.sha256(value.encode('utf-8')).hexdigest()),
                                       ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                cells.append(value)
            converted.append(tuple(cells))
        encoded[sheet] = tuple(converted)
    encoded['Запуск'] += tuple(chunks)
    return encoded
