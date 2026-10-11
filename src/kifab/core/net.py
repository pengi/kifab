"""HTTP behind a small interface, so everything network-facing runs offline in tests.

``$KIFAB_OFFLINE`` set to a fixtures directory (e.g. ``tests/fixtures``) makes kifab
answer from fixture files instead of the network: handy for tests, demos
and GUI work on a machine without access to the fab.
"""

from __future__ import annotations

import gzip
import json
import os
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Protocol

ENV_OFFLINE = "KIFAB_OFFLINE"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class NetError(Exception):
    pass


class HttpClient(Protocol):
    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> Any:
        """POST ``payload`` as JSON; return the decoded JSON answer."""


class UrllibClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def post_json(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> Any:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
                "Content-Type": "application/json",
                **headers,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body: bytes = resp.read()
                encoding = resp.headers.get("Content-Encoding", "")
        except (urllib.error.URLError, OSError) as exc:
            raise NetError(f"{url}: {exc}") from exc
        if encoding == "gzip":
            body = gzip.decompress(body)
        elif encoding == "deflate":
            body = zlib.decompress(body)
        try:
            return json.loads(body)
        except ValueError as exc:
            raise NetError(f"{url}: answer is not JSON") from exc


def offline_root() -> Path | None:
    value = os.environ.get(ENV_OFFLINE)
    return Path(value) if value else None
