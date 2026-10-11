"""``kifab`` command line entry point."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from kifab import __version__
from kifab.cli.output import (
    EXIT_BAD_INPUT,
    EXIT_ERRORS,
    EXIT_OK,
    error_line,
    issues_json,
    print_issues,
    print_json,
    summary_line,
)
from kifab.core import gitignore
from kifab.core.bundle import BundleOptions, BundleResult, bundle
from kifab.core.check import run_check
from kifab.core.ci import CiError, init_ci
from kifab.core.config import ConfigError
from kifab.core.design_rules import apply_rules
from kifab.core.git import GitCli, GitError
from kifab.core.init import InitError, init_project
from kifab.core.kicad_plugin import PluginError, install_plugin, uninstall_plugin
from kifab.core.issues import Issue, Severity
from kifab.core.order import record_order
from kifab.core.orderability import check_availability, schematic_bom
from kifab.core.project import KicadOpen, Project
from kifab.core.library import (
    VendorResult,
    schematic_part_numbers,
    sync_parts,
    vendor_part,
)
from kifab.core.lockfile import Lockfile, LockError, load_lockfile
from kifab.core.parts import CatalogError, LibraryType, PartHit
from kifab.fabs.base import Fab, FetchError, UnknownFab, available_fabs, get_fab
from kifab.kicad.project import InputError


def _cmd_init(args: argparse.Namespace) -> int:
    directory = args.dir if args.dir is not None else Path(args.name)
    result = init_project(
        directory,
        args.name,
        args.fab,
        layers=args.layers,
        git=None if args.no_git else GitCli(),
    )
    project = result.project
    print(f"created {project.name} ({project.config.fab if project.config else ''})")
    print(f"  in {directory}")
    for path in result.files:
        print(f"  {project.rel(path)}")
    if result.git_initialised:
        print("  git repository initialised")
    if args.ci:
        ci = init_ci(project, GitCli(), kifab_spec=args.kifab_spec)
        print(f"  {ci.path}")
    print(
        "Next: open the project in KiCad; KiCad's DRC now uses the fab's limits.\n"
        "Use symbols from the 'kifab' library for power and ground."
    )
    return EXIT_OK


def _cmd_rules_apply(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    result = apply_rules(project, force=args.force)
    profile = project.design_rules()
    assert profile is not None  # apply_rules needs a config
    if not result.changed:
        print(f"rules already match the {profile.describe()}")
        return EXIT_OK
    for path in result.changed:
        print(f"updated {project.rel(path)}")
    if result.backup:
        print(f"backup in {project.rel(result.backup)}/")
    return EXIT_OK


def _exit_code(issues: list[Issue], strict: bool) -> int:
    failing = {Severity.ERROR, Severity.WARNING} if strict else {Severity.ERROR}
    return EXIT_ERRORS if any(i.severity in failing for i in issues) else EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    if args.fix_gitignore:
        path = project.directory / ".gitignore"
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        new = gitignore.update(old)
        if new != old:
            path.write_text(new, encoding="utf-8")
            if not args.json:
                print("updated .gitignore")
    result = run_check(project, git=None if args.no_git else GitCli())
    code = _exit_code(result.issues, args.strict)
    if args.json:
        print_json(
            {
                "project": project.name,
                "directory": str(project.directory),
                "checks": result.checks,
                "issues": issues_json(result.issues),
                "ok": code == EXIT_OK,
            }
        )
        return code
    print_issues(result.issues)
    verdict = "ok" if code == EXIT_OK else "FAILED"
    print(f"{project.name}: {summary_line(result.issues)} -> {verdict}")
    return code


class TextReporter:
    """Prints steps as they finish (issues are printed at the end)."""

    def step(self, name: str, detail: str) -> None:
        print(f"  {name:<10} {detail}")

    def issue(self, issue: Issue) -> None:
        pass


def _bundle_json(result: BundleResult) -> dict[str, object]:
    return {
        "project": result.project,
        "version": result.version,
        "ok": result.ok,
        "out_dir": str(result.out_dir),
        "files": [p.name for p in result.files],
        "artifacts": [str(a.path) for a in result.artifacts],
        "bom_lines": len(result.bom.lines) if result.bom else None,
        "bom_parts": result.bom.part_count if result.bom else None,
        "placed": len(result.placement.parts) if result.placement else None,
        "left_out": result.left_out,
        "dnp": result.dnp,
        "issues": issues_json(result.issues),
    }


def _cmd_bundle(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    options = BundleOptions(
        out_dir=args.out,
        skip_check=args.skip_check,
        skip_drc=args.skip_drc,
        force=args.force,
        assemble_through_hole=True if args.assemble_through_hole else None,
        artifacts_dir=args.artifacts_dir,
        stock=args.stock,
        boards=args.boards,
    )
    if not args.json:
        print(f"{project.name}  ({project.directory})")
    result = bundle(
        project,
        options,
        git=GitCli(),
        reporter=None if args.json else TextReporter(),
    )
    code = _exit_code(result.issues, args.strict)
    if args.json:
        print_json(_bundle_json(result))
        return code
    print_issues(result.issues)
    if result.files:
        print(f"  -> {result.out_dir}/  ({len(result.files)} files)")
    for artifact in result.artifacts:
        print(f"  release: {artifact.path}")
    if code == EXIT_OK:
        print(
            "  Check polarized and rotated parts (modules, ICs, LEDs) in the fab's "
            "placement preview."
        )
    else:
        print(f"  FAILED: {summary_line(result.issues)}")
    return code


def _cmd_ci_init(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    result = init_ci(
        project,
        GitCli(),
        kifab_spec=args.kifab_spec,
        image=args.image,
        force=args.force,
    )
    print(f"wrote {result.path}")
    print("Push a tag (git tag v1.0 && git push --tags) to get a release.")
    return EXIT_OK


def _cmd_order_record(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    when = None
    if args.date:
        try:
            when = date.fromisoformat(args.date)
        except ValueError:
            error_line(f"--date must be YYYY-MM-DD, got {args.date!r}")
            return EXIT_BAD_INPUT
    result = record_order(
        project, GitCli(), build_dir=args.build, when=when, stage=not args.no_stage
    )
    if result.issues:
        print_issues(result.issues, sys.stderr)
        return EXIT_ERRORS
    assert result.directory is not None
    print(f"recorded {project.rel(result.directory)}/")
    for path in result.files:
        print(f"  {path.name}")
    if result.staged:
        print("staged in git; commit it together with the tag you ordered")
    return EXIT_OK


def _fab_for(args: argparse.Namespace) -> Fab:
    """``--fab`` if given, else the fab of the project in ``--project``/cwd."""
    if getattr(args, "fab", None):
        return get_fab(args.fab)
    try:
        project = Project.open(args.project)
    except InputError:
        return get_fab("jlcpcb")
    return project.fab() or get_fab("jlcpcb")


_TYPE_LABEL = {
    LibraryType.BASIC: "Basic",
    LibraryType.PREFERRED: "Pref",
    LibraryType.EXTENDED: "Ext",
    LibraryType.UNKNOWN: "?",
}


def format_hit(n: int, hit: PartHit) -> str:
    price = hit.unit_price(1)
    price_text = f"${price:.4f}" if price is not None else "-"
    desc = (
        hit.description if len(hit.description) <= 60 else hit.description[:57] + "..."
    )
    return (
        f"{n:>3} {hit.id:<10} {_TYPE_LABEL[hit.library_type]:<5} {hit.stock:>9} "
        f"{price_text:>9}  {hit.package:<12} {hit.mpn}\n      {desc}"
    )


def _cmd_component_search(args: argparse.Namespace) -> int:
    fab = _fab_for(args)
    query = " ".join(args.query)
    try:
        page = fab.catalog().search(
            query, page=args.page, page_size=args.limit, basic_only=args.basic
        )
    except CatalogError as exc:
        error_line(str(exc))
        return EXIT_ERRORS
    if args.json:
        print_json(
            {
                "query": query,
                "fab": fab.name,
                "page": page.page,
                "pages": page.pages,
                "total": page.total,
                "hits": [h.to_json() for h in page.hits],
            }
        )
        return EXIT_OK
    if not page.hits:
        print(f"no {fab.title} parts found for {query!r}")
        return EXIT_OK
    print(f"  # {'part':<10} {'type':<5} {'stock':>9} {'1 pc':>9}  {'package':<12} mpn")
    first = (page.page - 1) * page.page_size
    for i, hit in enumerate(page.hits, start=first + 1):
        print(format_hit(i, hit))
    print(f"page {page.page} of {page.pages} ({page.total} parts)")
    if not args.add:
        return EXIT_OK
    try:
        answer = input("Add which one? (number or part id, Enter to skip): ").strip()
    except EOFError:
        answer = ""
    if not answer:
        return EXIT_OK
    chosen = answer.upper()
    if answer.isdigit() and first < int(answer) <= first + len(page.hits):
        chosen = page.hits[int(answer) - first - 1].id
    project = Project.open(args.project)
    try:
        _print_vendored(vendor_part(project, chosen))
    except FetchError as exc:
        error_line(str(exc))
        return EXIT_ERRORS
    print(_RELOAD_NOTE)
    return EXIT_OK


def _print_vendored(result: VendorResult) -> None:
    what = ", ".join(result.symbols + result.footprints) or "nothing"
    print(f"  {result.part_id:<10} {what}")
    for path in result.written:
        print(f"      wrote {path}")
    for issue in result.issues:
        print(f"      {issue.severity.value}: {issue.message}")


_RELOAD_NOTE = (
    "If KiCad is open, new symbols/footprints may only appear after reopening the "
    "project."
)


def _cmd_component_add(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    code = EXIT_OK
    for i, part_id in enumerate(args.ids):
        if i and args.delay:
            time.sleep(args.delay)
        try:
            result = vendor_part(project, part_id, refresh=args.refresh)
        except FetchError as exc:
            error_line(str(exc))
            code = EXIT_ERRORS
            continue
        _print_vendored(result)
    if code == EXIT_OK:
        print(_RELOAD_NOTE)
    return code


def _cmd_component_sync(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    only = [x for part in args.only for x in part.split(",")] if args.only else []
    result = sync_parts(
        project,
        refresh=args.refresh,
        only=only,
        delay=args.delay,
        reporter=TextReporter(),
    )
    if args.json:
        print_json(
            {
                "vendored": [r.part_id for r in result.vendored],
                "skipped": result.skipped,
                "failed": result.failed,
                "issues": issues_json(result.issues),
            }
        )
    else:
        print_issues(result.issues)
        print(
            f"{len(result.vendored)} vendored, {len(result.skipped)} already "
            f"vendored, {len(result.failed)} failed"
        )
    return EXIT_ERRORS if result.failed else EXIT_OK


def _cmd_component_list(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    lock = load_lockfile(project.lock_path) or Lockfile()
    numbers, _ = schematic_part_numbers(project)
    parts = sorted(lock.parts, key=lambda p: (p.fab, p.id))
    missing = sorted(set(numbers) - {p.id for p in lock.parts})
    if args.json:
        print_json(
            {
                "parts": [
                    {
                        "id": p.id,
                        "fab": p.fab,
                        "source": p.source,
                        "symbols": list(p.symbols),
                        "files": list(p.files),
                        "used_by": numbers.get(p.id, []),
                    }
                    for p in parts
                ],
                "not_vendored": missing,
            }
        )
        return EXIT_OK
    for part in parts:
        used = ",".join(numbers.get(part.id, [])) or "unused"
        print(f"{part.id:<10} {', '.join(part.symbols)}  ({used})")
    if missing:
        print("used in the schematic but not vendored: " + ", ".join(missing))
        print("run `kifab component sync`")
    return EXIT_OK


def _cmd_component_check(args: argparse.Namespace) -> int:
    project = Project.open(args.project)
    fab = project.fab() or get_fab("jlcpcb")
    config = project.config
    bom = schematic_bom(
        project,
        assemble_through_hole=args.assemble_through_hole
        or bool(config and config.bundle.assemble_through_hole),
    )
    issues = fab.check_parts(bom)
    report = check_availability(bom, fab.catalog(), boards=args.boards)
    issues += report.issues
    code = _exit_code(issues, args.strict)
    if args.json:
        print_json(
            {
                "boards": report.boards,
                "parts": {pn: entry for pn, entry in sorted(report.snapshot().items())},
                "extended_types": report.extended_types,
                "parts_cost": round(report.parts_cost, 4),
                "issues": issues_json(issues),
                "ok": code == EXIT_OK,
            }
        )
        return code
    for status in report.lines:
        hit = status.hit
        refs = ",".join(status.line.references)
        if hit is None:
            print(f"  {status.line.part_number:<10} NOT FOUND             {refs}")
            continue
        price = status.unit_price
        print(
            f"  {hit.id:<10} {_TYPE_LABEL[hit.library_type]:<5} "
            f"{hit.stock:>9} in stock, need {status.needed:<5} "
            f"{'$%.4f' % price if price is not None else '-':>9}  {refs}"
        )
    print_issues(issues)
    print(
        f"{len(report.lines)} parts for {report.boards} board(s): "
        f"{len(report.extended_types)} Extended type(s), parts ~${report.parts_cost:.2f}"
        f" -> {'ok' if code == EXIT_OK else 'FAILED'}"
    )
    return code


def _cmd_plugin_install(args: argparse.Namespace) -> int:
    try:
        result = install_plugin(args.dest, force=args.force)
    except PluginError as exc:
        error_line(str(exc))
        return EXIT_ERRORS
    print(f"installed the kifab KiCad plugin in {result.directory}")
    print(f"  its buttons start {result.kifab_gui}")
    print(
        "In KiCad: Preferences > Plugins > enable the IPC API server, then restart "
        "KiCad.\nThe plugin only opens kifab; everything works without it."
    )
    return EXIT_OK


def _cmd_plugin_uninstall(args: argparse.Namespace) -> int:
    try:
        removed = uninstall_plugin(args.dest)
    except PluginError as exc:
        error_line(str(exc))
        return EXIT_ERRORS
    print(f"removed {removed}" if removed else "the kifab plugin is not installed")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kifab",
        description="Manage a KiCad project together with its fabrication service.",
    )
    parser.add_argument("--version", action="version", version=f"kifab {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("init", help="create a new project for a fab")
    p.add_argument("name", help="project name (also the KiCad file names)")
    p.add_argument("fab", help="fabrication service: " + ", ".join(available_fabs()))
    p.add_argument("--dir", type=Path, help="where to create it (default: ./<name>)")
    p.add_argument("--layers", type=int, default=2, help="copper layers (default 2)")
    p.add_argument("--no-git", action="store_true", help="do not run git init")
    p.add_argument(
        "--ci", action="store_true", help="also write the GitHub release workflow"
    )
    p.add_argument("--kifab-spec", help="pip requirement the workflow installs")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser(
        "check",
        help="self-containment, rules drift and git hygiene (offline)",
        description="Exit code 0: no errors; 1: errors (or warnings with "
        "--strict); 2: bad input.",
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="warnings fail too")
    p.add_argument("--no-git", action="store_true", help="skip the git checks")
    p.add_argument(
        "--fix-gitignore",
        action="store_true",
        help="write kifab's managed block into .gitignore first",
    )
    p.set_defaults(func=_cmd_check)

    p = sub.add_parser(
        "bundle",
        help="validate and produce all manufacturing files into build/",
        description="Exit code 0: bundle made; 1: errors (or warnings with "
        "--strict); 2: bad input.",
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("-o", "--out", type=Path, help="output directory (default build/)")
    p.add_argument("--skip-check", action="store_true", help="do not run kifab check")
    p.add_argument("--skip-drc", action="store_true", help="do not run KiCad's DRC")
    p.add_argument(
        "--force", action="store_true", help="run even if KiCad seems to be open"
    )
    p.add_argument(
        "--assemble-through-hole",
        action="store_true",
        help="keep through-hole parts in BOM and placement",
    )
    p.add_argument(
        "--artifacts-dir",
        type=Path,
        help="also copy the upload set there as <name>-<version>-* (for releases)",
    )
    p.add_argument(
        "--stock",
        action="store_true",
        help="also check stock and part types online (recorded in the manifest)",
    )
    p.add_argument("--boards", type=int, default=1, help="quantity for --stock")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="warnings fail too")
    p.set_defaults(func=_cmd_bundle)

    ci = sub.add_parser("ci", help="continuous integration")
    ci_sub = ci.add_subparsers(dest="ci_command", metavar="ACTION")
    p = ci_sub.add_parser(
        "init", help="write .github/workflows/release.yml (release on tag v*)"
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("--kifab-spec", help="pip requirement to install kifab with")
    p.add_argument("--image", default="kicad/kicad:10.0", help="KiCad docker image")
    p.add_argument("--force", action="store_true", help="replace an existing file")
    p.set_defaults(func=_cmd_ci_init)

    order = sub.add_parser("order", help="order history")
    order_sub = order.add_subparsers(dest="order_command", metavar="ACTION")
    p = order_sub.add_parser(
        "record", help="archive the current bundle into orders/ and stage it"
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("--build", type=Path, help="bundle directory (default build/)")
    p.add_argument("--date", help="order date, YYYY-MM-DD (default today)")
    p.add_argument("--no-stage", action="store_true", help="do not git add it")
    p.set_defaults(func=_cmd_order_record)

    component = sub.add_parser("component", help="parts from the fab's library")
    comp_sub = component.add_subparsers(dest="component_command", metavar="ACTION")
    p = comp_sub.add_parser("search", help="search the fab's parts catalogue")
    p.add_argument("query", nargs="+", help="keywords or a part number")
    p.add_argument("--basic", action="store_true", help="Basic parts only")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--limit", type=int, default=10, help="results per page")
    p.add_argument("--fab", help="fab to search (default: the project's, or jlcpcb)")
    p.add_argument("--project", type=Path, default=Path("."), help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument(
        "--add",
        action="store_true",
        help="then ask which result to add to the project",
    )
    p.set_defaults(func=_cmd_component_search)

    p = comp_sub.add_parser(
        "add", help="vendor parts' symbol, footprint and 3D model into lib/<fab>/"
    )
    p.add_argument("ids", nargs="+", metavar="ID", help="part numbers, e.g. C123456")
    p.add_argument("--project", type=Path, default=Path("."))
    p.add_argument(
        "--refresh", action="store_true", help="replace already vendored files"
    )
    p.add_argument("--delay", type=float, default=0.5, help=argparse.SUPPRESS)
    p.set_defaults(func=_cmd_component_add)

    p = comp_sub.add_parser(
        "sync", help="vendor every part the schematic references (by LCSC field)"
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("--only", action="append", help="only these part numbers")
    p.add_argument(
        "--refresh", action="store_true", help="re-fetch parts already vendored"
    )
    p.add_argument("--delay", type=float, default=0.5, help="seconds between fetches")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_component_sync)

    p = comp_sub.add_parser(
        "check",
        help="orderability: stock, Basic/Extended, price (online)",
        description="Exit code 0: orderable; 1: missing or out-of-stock parts "
        "(or warnings with --strict); 2: bad input.",
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("--boards", type=int, default=1, help="how many boards")
    p.add_argument(
        "--assemble-through-hole",
        action="store_true",
        help="include through-hole parts",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--strict", action="store_true", help="warnings fail too")
    p.set_defaults(func=_cmd_component_check)

    p = comp_sub.add_parser("list", help="vendored parts (from kifab.lock)")
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_component_list)

    plugin = sub.add_parser(
        "kicad-plugin", help="optional KiCad toolbar buttons that open kifab"
    )
    plugin_sub = plugin.add_subparsers(dest="plugin_command", metavar="ACTION")
    p = plugin_sub.add_parser("install", help="install the plugin for KiCad 10")
    p.add_argument("--dest", type=Path, help="plugin directory (default: KiCad's)")
    p.add_argument("--force", action="store_true", help="replace an installed one")
    p.set_defaults(func=_cmd_plugin_install)
    p = plugin_sub.add_parser("uninstall", help="remove the plugin")
    p.add_argument("--dest", type=Path, help="plugin directory (default: KiCad's)")
    p.set_defaults(func=_cmd_plugin_uninstall)

    rules = sub.add_parser("rules", help="the fab's design rules in the project")
    rules_sub = rules.add_subparsers(dest="rules_command", metavar="ACTION")
    p = rules_sub.add_parser(
        "apply", help="write the fab's rules into .kicad_dru and .kicad_pro"
    )
    p.add_argument("project", nargs="?", type=Path, default=Path("."))
    p.add_argument(
        "--force", action="store_true", help="write even if KiCad seems to be open"
    )
    p.set_defaults(func=_cmd_rules_apply)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help(sys.stdout)
        return EXIT_OK if args.command is None else EXIT_BAD_INPUT
    try:
        code: int = args.func(args)
        return code
    except (InputError, ConfigError, InitError, UnknownFab, CiError) as exc:
        error_line(str(exc))
        return EXIT_BAD_INPUT
    except (KicadOpen, GitError, LockError) as exc:
        error_line(str(exc))
        return EXIT_ERRORS
