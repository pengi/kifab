"""Check kifab against the real online services (and a real KiCad, if present).

Run on a machine with network access to JLCPCB/EasyEDA:

    uv run python scripts/local_verify.py --part C123456 [--query "100nF 0603"]

``--part`` is any LCSC part number you use. Everything happens in a scratch
directory; a short report (pass/fail per check, no captured data) is written to
``--out`` (default: a new temporary directory) and printed. Captured answers of
the online services are third-party data: they are kept in the scratch
directory for you to look at, never written into the repository. If something
fails, describe it (or paste the relevant lines) so it can be turned into a
synthetic fixture in ``tests/fixtures``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Step:
    name: str
    ok: bool
    detail: str


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def kifab(*args: str) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "kifab", *args])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--part", required=True, help="an LCSC part number to fetch")
    parser.add_argument("--query", default="100nF 0603", help="a search query")
    parser.add_argument("--out", type=Path, help="report directory")
    args = parser.parse_args()
    out = args.out or Path(tempfile.mkdtemp(prefix="kifab-verify-report-"))
    out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="kifab-verify-"))
    steps: list[Step] = []

    def step(name: str, fn: Callable[[], str]) -> None:
        print(f"... {name}", flush=True)
        try:
            steps.append(Step(name, True, fn()))
        except Exception as exc:  # recorded; the run goes on
            steps.append(Step(name, False, f"{exc}\n{traceback.format_exc(limit=2)}"))
        print(("ok   " if steps[-1].ok else "FAIL ") + steps[-1].detail.splitlines()[0])

    def search() -> str:
        proc = kifab("component", "search", *args.query.split(), "--json")
        (scratch / "search.json").write_text(proc.stdout)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "search failed")
        hits = json.loads(proc.stdout)["hits"]
        if not hits:
            raise RuntimeError(f"no hits for {args.query!r}")
        missing = [k for k in ("id", "stock", "type", "unit_price") if k not in hits[0]]
        if missing or hits[0]["type"] == "unknown":
            raise RuntimeError(f"answer not understood: {missing or hits[0]['type']}")
        return f"{len(hits)} hits for {args.query!r}, first {hits[0]['id']}"

    def lookup() -> str:
        proc = kifab("component", "search", args.part, "--json")
        hits = json.loads(proc.stdout or "{}").get("hits", [])
        if not any(h["id"] == args.part.upper() for h in hits):
            raise RuntimeError(f"{args.part} not found by part-number search")
        return f"{args.part} found"

    def sorted_search() -> str:
        found = []
        for sort, key, reverse in (
            ("stock", "stock", True),
            ("price", "unit_price", False),
        ):
            proc = kifab(
                "component",
                "search",
                *args.query.split(),
                "--plain",
                "--sort",
                sort,
                "--limit",
                "30",
                "--json",
            )
            (scratch / f"sorted_{sort}.json").write_text(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "search failed")
            values = [
                h[key] for h in json.loads(proc.stdout)["hits"] if h[key] is not None
            ]
            if values != sorted(values, reverse=reverse):
                raise RuntimeError(
                    f"--sort {sort}: JLCPCB did not return results in that order; "
                    "the sort fields in kifab/suppliers/jlcpcb/search.py (SORT_FIELDS) "
                    "need adjusting (answers saved in the scratch directory)"
                )
            found.append(f"{sort}: {len(values)} hits in order")
        return "; ".join(found)

    def quick() -> str:
        found = []
        for words in (["R", "10k", "0603"], ["C", "100n", "0603"]):
            proc = kifab("component", "search", *words, "--basic", "--json")
            name = "_".join(words)
            (scratch / f"quick_{name}.json").write_text(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "search failed")
            hits = json.loads(proc.stdout)["hits"]
            if not hits:
                plain = kifab("component", "search", "--plain", *words[1:], "--json")
                (scratch / f"plain_{name}.json").write_text(plain.stdout)
                raise RuntimeError(
                    f"quick search {' '.join(words)!r} found nothing; the keyword "
                    "format or attribute names differ (answers saved in the scratch "
                    "directory)"
                )
            found.append(f"{' '.join(words)}: {hits[0]['id']}")
        return "; ".join(found)

    def image() -> str:
        from kifab.fabs.base import get_fab

        catalog = get_fab("jlcpcb").catalog()
        hit = catalog.lookup(args.part)
        if hit is None:
            raise RuntimeError(f"{args.part} not found")
        if not hit.images:
            raise RuntimeError(
                "no image URL understood in the answer (look at the image fields "
                f"of {args.part} in {scratch / 'search.json'} or the website)"
            )
        data = catalog.image(hit.images[0])
        (scratch / "image.bin").write_bytes(data)
        kind = (
            "PNG"
            if data[:4] == b"\x89PNG"
            else "JPEG" if data[:2] == b"\xff\xd8" else "?"
        )
        if kind == "?":
            raise RuntimeError(f"downloaded {len(data)} bytes, not an image")
        return f"{len(hit.images)} image URL(s); first is a {kind} of {len(data)} bytes"

    def fetch() -> str:
        project = scratch / "p"
        proc = kifab("init", "p", "jlcpcb", "--dir", str(project), "--no-git")
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr)
        proc = kifab("component", "add", args.part, "--project", str(project))
        (scratch / "component_add.txt").write_text(proc.stdout + proc.stderr)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip())
        check = kifab("check", str(project), "--no-git", "--json")
        issues = json.loads(check.stdout)["issues"]
        if issues:
            raise RuntimeError(f"kifab check after add: {issues}")
        return f"{args.part} vendored, kifab check clean"

    def real_kicad() -> str:
        proc = run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_real_kicad.py"], ROOT
        )
        last = (proc.stdout.strip().splitlines() or ["no output"])[-1]
        if proc.returncode != 0 or "skipped" in last and "passed" not in last:
            raise RuntimeError(last)
        return last

    step("live JLCPCB search", search)
    step("live part-number lookup", lookup)
    step("search sorted by the fab", sorted_search)
    step("quick search for passives", quick)
    step("product image", image)
    step("easyeda2kicad fetch + vendoring", fetch)
    step("tests against the real kicad-cli", real_kicad)

    lines = ["# kifab verification report", ""]
    lines += [
        f"- {'OK  ' if s.ok else 'FAIL'} {s.name}: {s.detail.splitlines()[0]}"
        for s in steps
    ]
    lines += ["", f"Scratch directory (captured data, do not commit): {scratch}"]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if all(s.ok for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
