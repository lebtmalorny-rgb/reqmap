from reqmap.input_text import load_text


def test_lines_mode_preserves_duplicate_rows_and_order() -> None:
    """Склейка одинаковых строк уничтожила бы отдельные исходные требования."""
    result = load_text("Одинаковое требование\nОдинаковое требование\n", "lines", "stdin")

    assert [item.text for item in result] == ["Одинаковое требование", "Одинаковое требование"]
    assert [item.requirement_id for item in result] == ["REQ-0001", "REQ-0002"]
    assert [item.coordinate.row for item in result] == [1, 2]


def test_single_mode_removes_only_one_final_newline() -> None:
    """Удаление всех завершающих переводов строки изменило бы исходный текст."""
    result = load_text("Первая строка\n\n", "single", "stdin")

    assert len(result) == 1
    assert result[0].text == "Первая строка\n"
    assert result[0].coordinate.sheet is None
    assert result[0].coordinate.source_name == "stdin"


def test_lines_mode_preserves_spaces_and_original_line_numbers() -> None:
    """Нормализация пробелов или нумерация только непустых строк исказили бы источник."""
    result = load_text("\n  Текст  с  пробелами  \n\nВторой", "lines", "stdin")

    assert [item.text for item in result] == ["  Текст  с  пробелами  ", "Второй"]
    assert [item.coordinate.row for item in result] == [2, 4]
