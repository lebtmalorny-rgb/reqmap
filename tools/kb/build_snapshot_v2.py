"""Build a canonical schema-v2 snapshot manifest for maintenance."""

from __future__ import annotations

import argparse
from pathlib import Path

from reqmap.errors import ReqmapError
from reqmap.snapshot_trust import write_snapshot_manifest


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage()
        self.exit(2, f"{self.prog}: Некорректные аргументы.\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path, metavar="SNAPSHOT")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        write_snapshot_manifest(arguments.path)
    except ReqmapError as exc:
        print(f"{exc.code}: {exc.message_ru}")
        return 1
    print(arguments.path / "snapshot-manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
