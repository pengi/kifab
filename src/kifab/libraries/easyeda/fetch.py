"""Fetch a part's symbol, footprint and 3D model: easyeda2kicad behind an interface.

easyeda2kicad (https://github.com/uPesy/easyeda2kicad.py) downloads the EasyEDA
data JLCPCB assembles from and converts it. kifab runs it into a scratch
directory with ``--full --overwrite`` and does the vendoring itself, so none of
the tool's quirks (``--project-relative`` depending on the working directory,
"already exists" exits) reach the project.

``$KIFAB_EASYEDA2KICAD`` replaces the command (tests use a stand-in);
``$KIFAB_OFFLINE=<dir>`` copies captured output from
``<dir>/easyeda2kicad_output/<id>/`` instead of running anything.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kifab.libraries.base import FetchedPart, FetchError, PartSource

ENV_COMMAND = "KIFAB_EASYEDA2KICAD"
OUTPUT_NAME = "jlcpcb"  # easyeda2kicad's library name: footprints become jlcpcb:<name>
_VERSION = re.compile(r"easyeda2kicad(?:\.py)?\s+v?(\d+(?:\.\d+)+)", re.IGNORECASE)
_LCSC = re.compile(r"^C\d+$")


def _collect(part_id: str, out: Path, log: str) -> FetchedPart:
    pretty = out.with_suffix(".pretty")
    shapes = out.with_suffix(".3dshapes")
    sym = out.with_suffix(".kicad_sym")
    footprints = tuple(sorted(pretty.glob("*.kicad_mod"))) if pretty.is_dir() else ()
    models = (
        tuple(sorted(p for p in shapes.iterdir() if p.is_file()))
        if shapes.is_dir()
        else ()
    )
    if not footprints and not sym.is_file():
        tail = log.strip().splitlines()[-1:] or ["no output"]
        raise FetchError(f"{part_id}: easyeda2kicad produced nothing ({tail[0]})")
    m = _VERSION.search(log)
    return FetchedPart(
        part_id=part_id,
        symbol_library=sym if sym.is_file() else None,
        footprints=footprints,
        models=models,
        log=log,
        source=f"easyeda2kicad {m.group(1)}" if m else "easyeda2kicad",
    )


_LOG_LINE = re.compile(r"^\[(ERROR|WARNING|DEBUG)\]\s*(.*)$")
_NO_DATA = re.compile(r"['\"]success['\"]:\s*False", re.IGNORECASE)
_NETWORK = re.compile(
    r"urlopen error|timed out|Name or service not known|nodename nor servname|"
    r"Temporary failure in name resolution|Connection refused|Tunnel connection|"
    r"Network is unreachable",
    re.IGNORECASE,
)
_TLS = re.compile(r"CERTIFICATE_VERIFY_FAILED|SSL", re.IGNORECASE)
_THROTTLED = re.compile(r"HTTP Error (429|5\d\d)|Too Many Requests", re.IGNORECASE)
#: failures a retry cannot fix: no DNS, blocked by a proxy, untrusted certificate
_PERMANENT = re.compile(
    r"Name or service not known|nodename nor servname|Tunnel connection|"
    r"CERTIFICATE_VERIFY_FAILED",
    re.IGNORECASE,
)
_CANNOT_FETCH = "Failed to fetch data from EasyEDA API"


def explain_failure(part_id: str, log: str, returncode: int) -> str:
    """A one-paragraph reason from easyeda2kicad's log, with what to do about it.

    easyeda2kicad ends every failed download with "Failed to fetch data from
    EasyEDA API"; the cause is on the lines before (an [ERROR] for network and
    TLS problems, the API's answer at --debug level when it has no data).
    """
    lines = [m.groups() for m in map(_LOG_LINE.match, log.splitlines()) if m]
    errors = [text for level, text in lines if level in ("ERROR", "WARNING")]
    cause = [e for e in errors if _CANNOT_FETCH not in e]
    answer = next((t for lvl, t in lines if lvl == "DEBUG" and _NO_DATA.search(t)), "")
    tail = log.strip().splitlines()[-1:] or [f"exit {returncode}"]
    message = f"{part_id}: easyeda2kicad failed: " + "; ".join(cause or errors or tail)
    text = "\n".join(cause)
    if _THROTTLED.search(text):
        hint = "EasyEDA is limiting requests; wait a moment and try again"
    elif _TLS.search(text):
        hint = (
            "the TLS connection to easyeda.com could not be verified; install "
            "certifi (pip install certifi) or fix the system certificates"
        )
    elif _NETWORK.search(text):
        hint = "easyeda.com could not be reached (network, proxy or firewall)"
    elif answer:
        hint = (
            f"EasyEDA has no symbol/footprint for {part_id} (it answered: "
            f"{answer[:200]}); check the number, or pick another part"
        )
    else:
        hint = (
            f"see the full log with: python -m easyeda2kicad --lcsc_id={part_id} "
            "--full --debug --output /tmp/e2k/x"
        )
    return f"{message} ({hint})"


def retryable(log: str) -> bool:
    """Whether a failed run may succeed when repeated (throttling, hiccups)."""
    return not _PERMANENT.search(log)


def _check_id(part_id: str) -> str:
    wanted = part_id.strip().upper()
    if not _LCSC.match(wanted):
        raise FetchError(f"{part_id!r} is not an LCSC part number (C123456)")
    return wanted


class _Failed(Exception):
    """One failed easyeda2kicad run (``fetch`` decides whether to retry)."""

    def __init__(self, message: str, log: str) -> None:
        super().__init__(message)
        self.log = log


@dataclass
class Easyeda2kicad:
    """:class:`kifab.libraries.base.PartSource` running the easyeda2kicad tool."""

    command: tuple[str, ...] = ()
    timeout: float = 120.0
    #: pauses before repeating a failed fetch: EasyEDA throttles bursts (one
    #: part is several requests), and a fetch a few seconds later usually works
    retry_delays: tuple[float, ...] = (3.0, 8.0)
    sleep: Callable[[float], None] = time.sleep

    def argv(self) -> list[str]:
        if self.command:
            return list(self.command)
        env = os.environ.get(ENV_COMMAND)
        return shlex.split(env) if env else [sys.executable, "-m", "easyeda2kicad"]

    def fetch(self, part_id: str, workdir: Path) -> FetchedPart:
        part_id = _check_id(part_id)
        delays = list(self.retry_delays)
        while True:
            try:
                return self._fetch_once(part_id, workdir)
            except _Failed as failed:
                if not delays or not retryable(failed.log):
                    raise FetchError(str(failed)) from None
                self.sleep(delays.pop(0))

    def _fetch_once(self, part_id: str, workdir: Path) -> FetchedPart:
        out = Path(workdir) / OUTPUT_NAME
        out.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            *self.argv(),
            f"--lcsc_id={part_id}",
            "--full",
            "--overwrite",
            "--debug",  # the API's answer is only logged at debug level
            "--output",
            str(out),
        ]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=workdir,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"{part_id}: easyeda2kicad timed out") from exc
        except OSError as exc:
            raise FetchError(
                f"cannot run easyeda2kicad ({exc}); install it: pip install easyeda2kicad"
            ) from exc
        log = (proc.stdout or "") + (proc.stderr or "")
        if "No module named easyeda2kicad" in log:
            raise FetchError(
                "easyeda2kicad is not installed: pip install easyeda2kicad"
            )
        if proc.returncode != 0:
            raise _Failed(explain_failure(part_id, log, proc.returncode), log)
        return _collect(part_id, out, log)


LOG_NAME = "easyeda2kicad.log"
COMPLETE = ".complete"


def _read_captured(part_id: str, captured: Path, dest: Path) -> FetchedPart:
    """Copy an easyeda2kicad output directory and collect it as a fetch."""
    shutil.copytree(captured, dest)
    log_file = dest / LOG_NAME
    log = log_file.read_text(encoding="utf-8") if log_file.is_file() else ""
    return _collect(part_id, dest / OUTPUT_NAME, log)


@dataclass
class FixturePartSource:
    """Offline :class:`PartSource`: captured easyeda2kicad output per part."""

    root: Path

    def fetch(self, part_id: str, workdir: Path) -> FetchedPart:
        part_id = _check_id(part_id)
        captured = Path(self.root) / "easyeda2kicad_output" / part_id
        if not captured.is_dir():
            raise FetchError(f"{part_id}: no captured easyeda2kicad output (offline)")
        return _read_captured(part_id, captured, Path(workdir) / "captured")


@dataclass
class CachedPartSource:
    """Keeps what ``inner`` fetched per part, so a part is downloaded once.

    One fetch is several EasyEDA requests (the part, its 3D model in two
    formats) and EasyEDA throttles bursts; previews, adding, syncing and
    re-adding then reuse the copy. Entries older than ``max_age`` seconds are
    fetched again; :meth:`forget` drops one (refresh).
    """

    inner: PartSource
    root: Path  # <cache>/easyeda2kicad
    max_age: float = 30 * 24 * 3600.0
    now: Callable[[], float] = time.time

    def _dir(self, part_id: str) -> Path:
        return self.root / _check_id(part_id)

    def forget(self, part_id: str) -> None:
        shutil.rmtree(self._dir(part_id), ignore_errors=True)

    def _fresh(self, entry: Path) -> bool:
        stamp = entry / COMPLETE
        return stamp.is_file() and self.now() - stamp.stat().st_mtime < self.max_age

    def fetch(self, part_id: str, workdir: Path) -> FetchedPart:
        part_id = _check_id(part_id)
        entry = self._dir(part_id)
        if not self._fresh(entry):
            fetched = self.inner.fetch(part_id, Path(workdir) / "fetch")
            self._store(entry, fetched)
        return _read_captured(part_id, entry, Path(workdir) / "cached")

    def _store(self, entry: Path, fetched: FetchedPart) -> None:
        tmp = entry.with_name(entry.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            (tmp / f"{OUTPUT_NAME}.pretty").mkdir(parents=True)
            (tmp / f"{OUTPUT_NAME}.3dshapes").mkdir()
            if fetched.symbol_library is not None:
                shutil.copyfile(
                    fetched.symbol_library, tmp / f"{OUTPUT_NAME}.kicad_sym"
                )
            for fp in fetched.footprints:
                shutil.copyfile(fp, tmp / f"{OUTPUT_NAME}.pretty" / fp.name)
            for model in fetched.models:
                shutil.copyfile(model, tmp / f"{OUTPUT_NAME}.3dshapes" / model.name)
            (tmp / LOG_NAME).write_text(fetched.log, encoding="utf-8")
            (tmp / COMPLETE).write_text(fetched.source + "\n", encoding="utf-8")
            shutil.rmtree(entry, ignore_errors=True)
            tmp.replace(entry)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)  # no cache, still a result
            raise
