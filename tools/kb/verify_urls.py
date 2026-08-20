"""Отдельная сетевая проверка official provenance для maintenance-среды."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from reqmap.knowledge import SourceRecord, load_knowledge


ALLOWED_HOSTS = {"docs.openstack.org", "releases.openstack.org", "opendev.org"}


@dataclass(frozen=True)
class UrlCheck:
    source_id: str
    status: str
    message_ru: str


def verify_url(source: SourceRecord, timeout: float) -> UrlCheck:
    if source.provenance != "official" or source.source_url is None:
        return UrlCheck(source.source_id, "skipped", "Локальное проектное правило")
    parsed = urlparse(source.source_url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        return UrlCheck(
            source.source_id,
            "failed",
            "URL не принадлежит разрешённому официальному OpenStack host",
        )
    try:
        with urlopen(Request(source.source_url, method="HEAD"), timeout=timeout) as response:
            return UrlCheck(source.source_id, "passed", f"HTTP {response.status}")
    except HTTPError as exc:
        if exc.code not in {403, 405}:
            return UrlCheck(source.source_id, "failed", f"HTTP {exc.code}")
    except URLError as exc:
        return UrlCheck(source.source_id, "failed", str(exc))
    try:
        request = Request(source.source_url, headers={"Range": "bytes=0-0"}, method="GET")
        with urlopen(request, timeout=timeout) as response:
            return UrlCheck(source.source_id, "passed", f"HTTP {response.status}")
    except (HTTPError, URLError) as exc:
        return UrlCheck(source.source_id, "failed", str(exc))


def verify_urls(root: Path, timeout: float) -> tuple[UrlCheck, ...]:
    kb = load_knowledge(root)
    return tuple(verify_url(source, timeout) for source in kb.sources.values())


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) not in {1, 2}:
        print("Использование: verify_urls.py КАТАЛОГ_SNAPSHOT [TIMEOUT]", file=sys.stderr)
        return 2
    try:
        timeout = float(arguments[1]) if len(arguments) == 2 else 10.0
    except ValueError:
        print("TIMEOUT должен быть числом", file=sys.stderr)
        return 2
    checks = verify_urls(Path(arguments[0]), timeout)
    for check in checks:
        print(f"{check.source_id}\t{check.status}\t{check.message_ru}")
    return 1 if any(check.status == "failed" for check in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
