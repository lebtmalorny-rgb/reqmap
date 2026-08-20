import argparse
import sys

from reqmap import __version__


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--version"]:
        print(f"reqmap {__version__}")
        return 0
    parser = argparse.ArgumentParser(
        prog="reqmap",
        description="Сопоставление требований с OpenStack Epoxy 2025.1",
    )
    parser.parse_args(arguments)
    return 0
