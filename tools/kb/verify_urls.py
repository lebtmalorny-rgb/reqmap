"""Отдельная сетевая проверка official provenance для maintenance-среды."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from reqmap.knowledge import SourceRecord, load_knowledge


ALLOWED_HOSTS = {"docs.openstack.org", "releases.openstack.org", "opendev.org"}
REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5


@dataclass(frozen=True)
class UrlCheck:
    source_id: str
    status: str
    message_ru: str


class _UnsafeRedirect(Exception):
    pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def _is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def _open_no_redirect(request: Request, timeout: float) -> object:
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _open_with_allowed_redirects(request: Request, timeout: float) -> object:
    current = request
    for redirect_count in range(MAX_REDIRECTS + 1):
        try:
            return _open_no_redirect(current, timeout)
        except HTTPError as exc:
            if exc.code not in REDIRECT_CODES:
                raise
            location = exc.headers.get("Location") if exc.headers is not None else None
            if not location:
                raise _UnsafeRedirect("Redirect не содержит Location") from exc
            redirected_url = urljoin(current.full_url, location)
            if not _is_allowed_url(redirected_url):
                raise _UnsafeRedirect(
                    "Redirect ведёт на URL вне разрешённых официальных OpenStack hosts"
                ) from exc
            if redirect_count == MAX_REDIRECTS:
                raise _UnsafeRedirect("Превышено допустимое число redirect") from exc
            current = Request(
                redirected_url,
                headers=dict(current.header_items()),
                method=current.get_method(),
            )
    raise _UnsafeRedirect("Превышено допустимое число redirect")


def verify_url(source: SourceRecord, timeout: float) -> UrlCheck:
    if source.provenance != "official" or source.source_url is None:
        return UrlCheck(source.source_id, "skipped", "Локальное проектное правило")
    if not _is_allowed_url(source.source_url):
        return UrlCheck(
            source.source_id,
            "failed",
            "URL не принадлежит разрешённому официальному OpenStack host",
        )
    try:
        with _open_with_allowed_redirects(
            Request(source.source_url, method="HEAD"), timeout
        ) as response:
            return UrlCheck(source.source_id, "passed", f"HTTP {response.status}")
    except HTTPError as exc:
        if exc.code not in {403, 405}:
            return UrlCheck(source.source_id, "failed", f"HTTP {exc.code}")
    except URLError as exc:
        return UrlCheck(source.source_id, "failed", str(exc))
    except _UnsafeRedirect as exc:
        return UrlCheck(source.source_id, "failed", str(exc))
    try:
        request = Request(source.source_url, headers={"Range": "bytes=0-0"}, method="GET")
        with _open_with_allowed_redirects(request, timeout) as response:
            return UrlCheck(source.source_id, "passed", f"HTTP {response.status}")
    except (HTTPError, URLError) as exc:
        return UrlCheck(source.source_id, "failed", str(exc))
    except _UnsafeRedirect as exc:
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
