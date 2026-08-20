from reqmap.cli import main


def test_version_is_printed_in_russian_contract(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out == "reqmap 0.1.0\n"
