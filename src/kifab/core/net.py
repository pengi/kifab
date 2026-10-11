"""HTTP behind a small interface, so everything network-facing runs offline in tests.

``$KIFAB_OFFLINE`` set to a fixtures directory (e.g. ``tests/fixtures``) makes kifab
answer from fixture files instead of the network: handy for tests, demos
and GUI work on a machine without access to the fab.
"""

from __future__ import annotations

import gzip
import json
import os
import ssl
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

    def get_bytes(self, url: str, headers: dict[str, str]) -> bytes:
        """GET ``url``; return the (decompressed) body."""


#: upper bound for downloads (product images are a few hundred kB)
MAX_DOWNLOAD = 16 << 20


def _decode(body: bytes, encoding: str) -> bytes:
    if encoding == "gzip":
        return gzip.decompress(body)
    if encoding == "deflate":
        return zlib.decompress(body)
    return body


def ssl_context() -> ssl.SSLContext:
    """Verify with certifi's CA bundle when installed (some Pythons, e.g. on
    macOS, have no usable system certificates), else the system's."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class UrllibClient:
    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.context = ssl_context()

    def get_bytes(self, url: str, headers: dict[str, str]) -> bytes:
        if not url.startswith(("https://", "http://")):
            raise NetError(f"{url}: not an http(s) URL")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **headers},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self.context
            ) as resp:
                body: bytes = resp.read(MAX_DOWNLOAD + 1)
                encoding = resp.headers.get("Content-Encoding", "")
        except (urllib.error.URLError, OSError) as exc:
            raise NetError(f"{url}: {exc}") from exc
        if len(body) > MAX_DOWNLOAD:
            raise NetError(f"{url}: answer too large")
        return _decode(body, encoding)

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
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self.context
            ) as resp:
                body: bytes = resp.read()
                encoding = resp.headers.get("Content-Encoding", "")
        except (urllib.error.URLError, OSError) as exc:
            raise NetError(f"{url}: {exc}") from exc
        try:
            return json.loads(_decode(body, encoding))
        except ValueError as exc:
            raise NetError(f"{url}: answer is not JSON") from exc


def offline_root() -> Path | None:
    value = os.environ.get(ENV_OFFLINE)
    return Path(value) if value else None
