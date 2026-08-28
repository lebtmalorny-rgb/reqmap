from reqmap.cli import main


def test_version_is_printed_in_russian_contract(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "reqmap 0.1.0\n"


def test_top_level_usage_is_localized_and_version_contract_is_unchanged(capsys):
    assert main(["--help"]) == 0
    help_text = capsys.readouterr().out.lower()
    assert "использование:" in help_text
    assert "параметры" in help_text
    assert "show this help" not in help_text
