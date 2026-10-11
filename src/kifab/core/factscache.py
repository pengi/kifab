"""Catalogue facts of parts seen before, kept in one small SQLite file.

The detail view shows them at once on the next visit (also offline); price and
stock are only trustworthy as of ``fetched``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kifab.core.parts import PartHit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    id TEXT PRIMARY KEY,
    mpn TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    fetched REAL NOT NULL,
    hit TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class CachedHit:
    hit: PartHit
    fetched: float  # unix seconds


class FactsCache:
    """Safe to use from several threads: every call opens its own connection.

    The cache is a convenience: unreadable or corrupt storage reads as empty
    and write errors are ignored.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=5)
        con.execute(_SCHEMA)
        return con

    def get(self, part_id: str) -> CachedHit | None:
        try:
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT fetched, hit FROM parts WHERE id = ?", (part_id.upper(),)
                ).fetchone()
            finally:
                con.close()
            if row is None:
                return None
            return CachedHit(PartHit.from_json(json.loads(row[1])), float(row[0]))
        except (OSError, sqlite3.Error, ValueError):
            return None

    def put(self, hit: PartHit, fetched: float) -> None:
        text = json.dumps(hit.to_json(), sort_keys=True)
        try:
            con = self._connect()
            try:
                with con:
                    con.execute(
                        "INSERT OR REPLACE INTO parts VALUES (?, ?, ?, ?, ?)",
                        (hit.id.upper(), hit.mpn, hit.manufacturer, fetched, text),
                    )
            finally:
                con.close()
        except (OSError, sqlite3.Error):
            pass
