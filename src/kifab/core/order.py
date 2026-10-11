"""``kifab order record``: archive (opt-in) what was actually ordered.

Copies the upload set of the current bundle into ``orders/<date>-<version>/``
with ``order.toml`` (commit, tags, kifab version, fab, input and file hashes)
and stages it in git. Refuses a bundle that is stale (inputs changed since it
was built) or failed.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from kifab import __version__
from kifab.core import toml
from kifab.core.bundle import BUILD_DIR, input_hashes, sha256_bytes
from kifab.core.git import Git
from kifab.core.issues import Code, Issue, error
from kifab.core.project import Project
from kifab.kicad import sexpr
from kifab.kicad.schematic import read_schematic

ORDERS_DIR = "orders"
ORDER_FILE = "order.toml"


@dataclass(frozen=True)
class StockEntry:
    stock: int
    type: str
    needed: int
    unit_price: float


@dataclass(frozen=True)
class BundleManifest:
    """The parts of ``build/<name>_manifest.json`` an order record needs."""

    fab: str
    version: str
    commit: str | None
    ok: bool
    inputs: dict[str, str]
    files: dict[str, str]
    kicad: str
    stock: dict[str, StockEntry]
    boards: int

    @classmethod
    def parse(cls, text: str) -> BundleManifest:
        data: Any = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("manifest is not a JSON object")

        def strmap(key: str) -> dict[str, str]:
            value = data.get(key, {})
            if not isinstance(value, dict):
                raise ValueError(f"manifest {key} is not an object")
            return {str(k): str(v) for k, v in value.items()}

        stock: dict[str, StockEntry] = {}
        raw_stock = data.get("stock") or {}
        if isinstance(raw_stock, dict):
            for pn, entry in raw_stock.items():
                if isinstance(entry, dict):
                    stock[str(pn)] = StockEntry(
                        stock=int(entry.get("stock", 0)),
                        type=str(entry.get("type", "")),
                        needed=int(entry.get("needed", 0)),
                        unit_price=float(entry.get("unit_price", 0.0)),
                    )
        commit = data.get("commit")
        return cls(
            fab=str(data.get("fab", "")),
            version=str(data.get("version", "unversioned")),
            commit=str(commit) if commit else None,
            ok=bool(data.get("ok", False)),
            inputs=strmap("inputs"),
            files=strmap("files"),
            kicad=str(data.get("kicad", "")),
            stock=stock,
            boards=int(data.get("boards", 0) or 0),
        )


@dataclass
class OrderResult:
    directory: Path | None = None
    files: list[Path] = field(default_factory=list)
    staged: bool = False
    issues: list[Issue] = field(default_factory=list)


def record_order(
    project: Project,
    git: Git | None,
    *,
    build_dir: Path | None = None,
    when: date | None = None,
    stage: bool = True,
) -> OrderResult:
    result = OrderResult()
    build = build_dir or project.directory / BUILD_DIR
    manifest_path = build / f"{project.name}_manifest.json"
    if not manifest_path.is_file():
        result.issues.append(
            error(
                Code.BUNDLE_MISSING,
                f"no bundle in {project.rel(build)}/",
                hint="run `kifab bundle` first",
            )
        )
        return result
    try:
        manifest = BundleManifest.parse(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        result.issues.append(error(Code.PARSE_ERROR, f"{manifest_path.name}: {exc}"))
        return result
    if not manifest.ok:
        result.issues.append(
            error(Code.BUNDLE_STALE, "the bundle has errors; fix them and re-bundle")
        )
        return result
    try:
        schematic = read_schematic(project.kicad.sch, project.name)
    except (OSError, sexpr.SexprError):
        schematic = None
    current = input_hashes(project, schematic)
    changed = sorted(
        k
        for k in set(current) | set(manifest.inputs)
        if current.get(k) != manifest.inputs.get(k)
    )
    if changed:
        result.issues.append(
            error(
                Code.BUNDLE_STALE,
                "inputs changed since the bundle was built: " + ", ".join(changed),
                hint="run `kifab bundle` again",
            )
        )
    files: list[Path] = []
    for name, digest in sorted(manifest.files.items()):
        path = build / name
        if not path.is_file() or sha256_bytes(path.read_bytes()) != digest:
            result.issues.append(
                error(Code.BUNDLE_STALE, f"{name} in {build.name}/ does not match")
            )
        files.append(path)
    if result.issues:
        return result

    day = (when or date.today()).isoformat()
    dest = project.directory / ORDERS_DIR / f"{day}-{manifest.version}"
    if dest.exists():
        result.issues.append(
            error(Code.ORDER_EXISTS, f"{project.rel(dest)} already exists")
        )
        return result
    dest.mkdir(parents=True)
    for path in files + [manifest_path]:
        target = dest / path.name
        shutil.copy2(path, target)
        result.files.append(target)
    tags = git.tags_at_head(project.directory) if git else []
    order = [
        "# kifab order record: what was sent to the fab. Written by kifab.\n",
        toml.table(
            None,
            {
                "date": day,
                "project": project.name,
                "fab": manifest.fab,
                "version": manifest.version,
                "commit": manifest.commit or "",
                "tags": tags,
                "kifab": __version__,
                "kicad": manifest.kicad,
                "stock_snapshot": (
                    f"at bundle time, for {manifest.boards} board(s)"
                    if manifest.stock
                    else "not recorded (bundle without --stock)"
                ),
            },
        ),
        toml.table("inputs", dict(sorted(manifest.inputs.items()))),
        toml.table("files", dict(sorted(manifest.files.items()))),
    ]
    for pn, entry in sorted(manifest.stock.items()):
        order.append(
            toml.table(
                f"stock.{pn}",
                {
                    "stock": entry.stock,
                    "type": entry.type,
                    "needed": entry.needed,
                    "unit_price": entry.unit_price,
                },
            )
        )
    order_file = dest / ORDER_FILE
    order_file.write_text("\n".join(order), encoding="utf-8")
    result.files.append(order_file)
    result.directory = dest
    if stage and git is not None and git.toplevel(project.directory) is not None:
        git.add(project.directory, [dest])
        result.staged = True
    return result
