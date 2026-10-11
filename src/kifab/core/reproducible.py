"""Byte-for-byte reproducible outputs: fixed timestamps and deterministic zips.

KiCad writes the plot time into every gerber, drill and job file. kifab replaces
it with one stamp (``$SOURCE_DATE_EPOCH``, else the commit time), so the same
commit gives the same bytes locally and in CI.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from collections.abc import Iterable
from datetime import datetime, timezone

_PATTERNS = (
    # gerber X2 attribute and comment
    (re.compile(r"^(%TF\.CreationDate,)[^*]*(\*%)$", re.M), "{iso}"),
    (re.compile(r"^(G04 Created by KiCad \(.*\) date )\S+ \S+?(\*)$", re.M), "{sp}"),
    # excellon drill comments
    (re.compile(r"^(; DRILL file .* date )\S+()$", re.M), "{t}"),
    (re.compile(r"^(; #@! TF\.CreationDate,)\S+()$", re.M), "{iso}"),
    # gerber job file (JSON)
    (re.compile(r'^(\s*"CreationDate":\s*")[^"]*(")', re.M), "{iso}"),
    # ODB++ misc/info and the first line of steps/<step>/eda/data
    (re.compile(r"^((?:CREATION|SAVE)_DATE=)\d{8}\.\d{6}()$", re.M), "{odb}"),
    (re.compile(r"\A(# )\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d()$", re.M), "{t}"),
)
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def source_date(fallback: datetime | None) -> datetime | None:
    """``$SOURCE_DATE_EPOCH`` if set, else ``fallback`` (e.g. the commit time)."""
    env = os.environ.get("SOURCE_DATE_EPOCH")
    if env and env.strip().isdigit():
        return datetime.fromtimestamp(int(env), tz=timezone.utc)
    return fallback


def normalize_timestamps(text: str, stamp: datetime) -> str:
    utc = stamp.astimezone(timezone.utc).replace(microsecond=0)
    values = {
        "iso": utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "sp": utc.strftime("%Y-%m-%d %H:%M:%S"),
        "t": utc.strftime("%Y-%m-%dT%H:%M:%S"),
        "odb": utc.strftime("%Y%m%d.%H%M%S"),
    }
    for pattern, key in _PATTERNS:
        value = values[key.strip("{}")]
        text = pattern.sub(lambda m: m.group(1) + value + m.group(2), text)
    return text


def deterministic_zip(
    members: Iterable[tuple[str, bytes]], stamp: datetime | None
) -> bytes:
    """A zip of ``(name, data)`` sorted by name, fixed times, modes and order.

    A name ending in ``/`` is a directory entry (kept even when empty).
    """
    if stamp is not None and stamp.year >= 1980:
        utc = stamp.astimezone(timezone.utc)
        date_time = (utc.year, utc.month, utc.day, utc.hour, utc.minute, utc.second)
    else:
        date_time = ZIP_EPOCH
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in sorted(members):
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            if name.endswith("/"):
                info.external_attr = (0o40755 << 16) | 0x10
                z.writestr(info, b"")
                continue
            info.external_attr = 0o100644 << 16
            z.writestr(info, data, compresslevel=9)
    return buf.getvalue()
