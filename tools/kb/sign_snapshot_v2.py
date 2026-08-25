"""Sign a canonical approved schema-v2 snapshot manifest for maintenance."""

from __future__ import annotations

import argparse
from pathlib import Path

from reqmap.errors import ReqmapError
from reqmap.snapshot_trust import sign_snapshot_manifest


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage()
        self.exit(2, f"{self.prog}: Некорректные аргументы.\n")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True, type=Path, metavar="SNAPSHOT")
    parser.add_argument("--private-key", required=True, type=Path, metavar="KEY")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        signature = sign_snapshot_manifest(arguments.path, arguments.private_key)
    except ReqmapError as exc:
        print(f"{exc.code}: {exc.message_ru}")
        return 1
    print(signature)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
