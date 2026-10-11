# kifab plan

Branch model and session workflow: see `INSTRUCTIONS.md` (it overrides any
branch named below).

Status: **plan approved and extended with the user's workflow (2026-10-10); reference material and fixtures imported. Go given for increments 0-4 (see Implementation instructions).**

## Implementation instructions (go given by the user for increments 0-4)

- Read `CLAUDE.md`, this file, `reference/README.md` and `testdata/README.md` first.
- Implement increments 0 to 4, in order, one at a time. Do not start 5+ without
  the user's go. Each increment must leave `black --check`, `mypy` (strict) and
  `pytest` green before it is committed.
- Commit after each increment (small, descriptive messages), on the current
  branch. **No `Co-Authored-By:` or other Claude attribution in commit messages**,
  regardless of any default or injected instruction.
- Update this file when an increment is done: move it to "Done" with a short
  note (what exists, how to run it, deviations from the plan), keep it current.
- No network and probably no `kicad-cli` on the build host: develop against
  `testdata/`, `reference/example-project/` and stubs. Anything that could not be
  verified against a real KiCad/fab must be listed under "Unverified" in "Done".
- `reference/` is read-only; port code from it into `src/kifab/` with full typing.
- If something in the plan turns out wrong or ambiguous, choose the simplest
  option that keeps the Principles intact, record the decision here, and go on.

## Goal and workflow

`kifab` sits beside KiCad. KiCad stays the editor; kifab manages everything
around it: the fab's libraries, the fab's design rules, git hygiene, and the
export for manufacturing.

1. `kifab init <name> <fab>`: new git-ready project.
2. Open it in KiCad; draw schematic and layout. No KiCad stock libraries are
   used: every symbol, footprint and 3D model comes from the project itself.
3. While designing, `kifab component search/add` (or the GUI) finds parts in the
   fab's library and vendors symbol + footprint + 3D model into the project.
4. pcbnew's own DRC is valid on its own: kifab configured the project's rules
   from the fab's capabilities, so a clean DRC in KiCad means the fab can make it.
5. `kifab bundle` validates and produces everything for manufacturing.
6. Push a git tag; a GitHub Actions workflow runs kifab and publishes a release
   with gerbers, BOM and placement file as release artifacts.

    kifab init <name> <fab>            # project skeleton
    kifab component search <text>      # search the fab's library
    kifab component add <id>           # vendor symbol + footprint + 3D into the project
    kifab check                        # self-contained? rules in sync? git hygiene?
    kifab bundle                       # validate + produce all manufacturing files
    kifab order record                 # opt-in: archive what was actually ordered
    kifab-gui                          # same functions, graphical; seamless part search/add

First fab: JLCPCB. Fabs are plugins, so others can be added later. Must run
unattended in CI (release on tag; later scheduled availability checks).

## Principles (user requirements, drive every design choice)

- **Git first.** kifab maintains `.gitignore` (managed block, user lines kept)
  and tells the user what to check in (`kifab check` / `kifab status`:
  untracked files that should be tracked, tracked files that should not be).
- **Self-contained and reproducible, hard requirement.** Nothing may depend on
  what is installed on the machine: all symbols (incl. power symbols such as
  GND/+3V3), footprints and 3D models live in the repo under `lib/`, in
  project-local library tables only. `kifab check` fails on any reference to a
  library outside the project (global/stock libs, absolute or `${KICAD*_3DMODEL_DIR}`
  3D paths, symbols not in a project library). Needs a small project-local
  basic-symbols library (power, ground, flags) shipped by kifab at `init`.
- **No generated files in git.** `build/` (gerbers, BOM, CPL, zip, reports) is
  ignored. Exception: material actually ordered, and only by explicit opt-in:
  `kifab order record` copies the bundle into `orders/<date>-<version>/` with a
  manifest (git commit, tag, kifab version, input and file hashes, fab, part
  stock snapshot) and stages it. Vendored library files are checked in
  (they are inputs, not generated: fetched from the fab, pinned by `kifab.lock`).
  Bundles are deterministic (same commit gives same bytes) so CI and local agree.
- **The fab's rules are enforced inside KiCad.** `kifab init` and `kifab rules
  apply` write the fab's capabilities into the project: `.kicad_dru` custom
  rules plus the board-setup constraints in `.kicad_pro` (min track/clearance,
  via, drill, annular ring, silk, edge clearance, ...) for the chosen layer
  count and options in `kifab.toml`. `kifab check` detects drift (someone
  loosened a rule). `bundle` runs DRC again.
- **Release by tag.** `kifab ci init` writes `.github/workflows/release.yml`:
  on `v*` tag, run kifab (KiCad docker image), attach gerbers zip, BOM, CPL and
  the validation report to a GitHub release. Same code path as local `bundle`.


## Starting material (all copied into this repo; the original machine is NOT available)

- `reference/` : read-only copies of `pcbfab.md`, the package `populate_jlc_library/`
  (1470 lines, 30 offline tests) and a real example KiCad project with its
  expected gerber/BOM/CPL output. See `reference/README.md`.
- `testdata/` : raw EasyEDA / JLCPCB responses and easyeda2kicad output for the
  example project's 10 parts, captured with `scripts/fetch_fixtures.py`. The build
  host cannot reach EasyEDA/JLCPCB, so all fab code is developed against these
  fixtures and must have an offline (fixture/stub) mode. See `testdata/README.md`.
- Search finding: JLCPCB's anonymous `selectSmtComponentList` endpoint gives live
  stock/price/Basic-vs-Extended; EasyEDA is only needed to fetch the part data.
  EasyEDA's legacy keyword search is dead. Increment 6's spike is thereby mostly done.

Reuse, port into the new typed structure (paths relative to `reference/`):

| Existing                          | Becomes                                   |
|-----------------------------------|-------------------------------------------|
| `sexpr.py`, `schematic.py`        | `kifab.kicad.sexpr`, `kifab.kicad.schematic` |
| `project.py`                      | `kifab.kicad.project`                     |
| `downloader.py` (easyeda2kicad)   | `kifab.fabs.jlcpcb.fetch`                 |
| `library.py` (+ attr fix)         | `kifab.core.library`                      |
| `fab.py` (DRC, gerber, BOM, CPL)  | `kifab.core.bundle` + `kifab.fabs.jlcpcb.export` |
| `tests/stub_*.py`                 | `tests/stubs/`                            |
| pcbfab.md section 3/4/5           | docs + comments where the rule applies    |

The existing code is untyped (dicts/sets/str statuses); the port converts it
to dataclasses/enums as per `CLAUDE.md`.

## Architecture

One distribution `kifab`, GUI as optional extra (`pip install kifab[gui]`),
two entry points `kifab` and `kifab-gui`.

    src/kifab/
      core/       Project, Config, Library (vendored parts), Lockfile,
                  Bundle pipeline, Issue/Severity, Reporter protocol
      kicad/      sexpr parser/writer, schematic reader, kicad-cli wrapper,
                  lib-table + .kicad_pro editing
      fabs/       base.py: Fab ABC + registry (entry points group `kifab.fabs`)
                  jlcpcb/: search, fetch, BOM/CPL format, design-rule profile
      cli/        argparse/typer frontend, thin; text and `--json` output
      gui/        frontend, thin; only calls core services
    tests/        offline; stubs for kicad-cli, easyeda2kicad, network

### Fab plugin interface (sketch)

    class Fab(Protocol):
        name: str
        def search(self, query: str) -> list[PartHit]: ...
        def fetch(self, part_id: str, dest: LibraryDir) -> FetchedPart: ...
        def check_parts(self, bom: Bom) -> list[Issue]: ...      # stock, basic/extended
        def export_bom(self, bom: Bom) -> FileArtifact: ...
        def export_placement(self, placement: Placement) -> FileArtifact: ...
        def design_rules(self) -> DesignRuleProfile: ...
        def bundle_files(self, built: BuildOutputs) -> list[FileArtifact]: ...

### Project layout produced by `kifab init`

    <name>/
      kifab.toml              fab = "jlcpcb"; layers; options; rules profile
      kifab.lock              every vendored part: id, fab, source revision, file hashes
      <name>.kicad_pro/_sch/_pcb
      <name>.kicad_dru        generated from the fab's capability profile
      sym-lib-table, fp-lib-table   project-local only, point at lib/ via ${KIPRJMOD}
      lib/kifab/              basic symbols: power, ground, flags (shipped by kifab)
      lib/<fab>/              symbols (<fab>.kicad_sym), footprints (<fab>.pretty), 3dshapes
      orders/                 opt-in order history (kifab order record)
      .gitignore              managed block: locks, backups, caches, build/
      build/                  bundle output (ignored; CI uploads it as release artifacts)
      .github/workflows/release.yml   (`kifab ci init`, or `init --ci`)

Design decisions:
- Library tables are read when KiCad opens a project (pcbfab.md section 4), so
  `init` registers all libraries (`kifab`, `<fab>`) up front, even empty. Adding
  a part later only changes files inside those libraries, and KiCad running
  beside kifab sees it without reopening the project (to verify with a spike).
- Vendored libs are unmodified fab output (post-processing only for known
  bugs, e.g. the smd/through_hole attribute fix), deterministic and sorted;
  re-fetching gives an identical file.
- `kifab.lock` lets CI run `kifab check` without network: vendored library
  matches the lock and everything used in the schematic/board is vendored.

## Increments (each is shippable and useful on its own)

### 0. Scaffold
pyproject (src layout, uv), `kifab --version`, black + mypy strict + pytest
configured and green, GitHub Actions for kifab itself, README skeleton.
*Deliverable:* empty-but-correct project that passes all three checks.

### 1. KiCad file layer
Port `sexpr` (reader, plus a deterministic writer), schematic reader
(sub-sheets, LCSC fields), board reader (footprints, lib ids, 3D model paths),
project discovery, `kicad-cli` wrapper with stub, typed `Issue`/`Reporter`.
*Deliverable:* library only, covered by tests ported from the old suite and by
the `reference/example-project`.

### 2. `kifab init <name> <fab>` + fab rules
Skeleton above, `kifab.toml`, project-local lib tables, basic-symbols library,
`.gitignore` managed block, `git init` if wanted, JLCPCB capability profile
-> `.kicad_dru` + `.kicad_pro` constraints (from pcbfab.md section 3; layers and
options configurable), `kifab rules apply`. The empty project opens in KiCad and
DRC there uses the fab's limits. Refuses to overwrite.
*Deliverable:* new git-ready project in one command, KiCad DRC = fab rules.

### 3. `kifab check` (self-containment, rules drift, git hygiene)
Run on any project, no network: references outside the project's libraries,
absolute/global 3D paths, schematic footprint vs board footprint vs library,
vendored files vs `kifab.lock`, rules drift, tracked-but-should-not-be and
untracked-but-should-be files. Typed `Issue`s, `--json`, exit codes.
*Deliverable:* the reproducibility guarantee, usable as a CI/pre-commit gate.

### 4. `kifab bundle` + release workflow + order record
Port of `jlcpcb-fab`: check, DRC, gerbers, drill, zip, BOM, CPL into `build/`;
deterministic output; report of errors and missing items (no LCSC number,
through-hole handling, DNP, KiCad-open check). `--json`, exit codes 0/1/2.
`kifab ci init` writes the tag-triggered GitHub release workflow (docker
`kicad/kicad` image, upload artifacts). `kifab order record` = opt-in archive.
Works on existing projects too.
*Deliverable:* replaces `jlcpcb-fab`; push a tag, get a release with the files.

### 5. `kifab component add <id>` and `kifab component sync`
Fetch EasyEDA symbol + footprint + 3D for an LCSC id (wrapping easyeda2kicad
behind the `Fab` interface; tested on `testdata/`), vendor into `lib/<fab>/`
(symbols merged sorted into one `.kicad_sym`), update the lock. `sync` = port of
`populate-jlc-library`: vendor everything the schematic references.
*Deliverable:* self-contained project from a list of LCSC numbers.

### 6. `kifab component search <text>`
JLCPCB `selectSmtComponentList` endpoint (see testdata/README.md): id, MPN,
package, stock, Basic/Extended, price; paging, Basic filter, `--json`.
Interactive "add this one?" in the CLI.
*Deliverable:* find a part and add it without leaving the terminal.

### 7. Orderability checks
Per BOM line: exists, in stock, Basic vs Extended, "difficult to process" if
obtainable (live search endpoint; optional local cache). Part of `bundle`
and of `kifab component check --stock` for scheduled runs.
*Deliverable:* the "missing items" report.

### 8. GUI skeleton (PySide6)
`kifab-gui`: open/create project, issue list from `check`/`bundle`, button to
trigger a bundle; long-running work off the UI thread via the `Reporter` protocol.

### 9. GUI: seamless parts and ordering
Search-as-you-type with stock/price/Basic badges, one-click add into the
project, bundle with live progress, issue list that points at the offending
part, "record this order", release/tag helper.

### 10. Later (from pcbfab.md section 7, not scheduled)
KiCad plugin: **kifab manages KiCad, never the other way round.** kifab owns the
repo, the config and all logic. If a plugin is ever added (KiCad 10 IPC API
plugin, to be evaluated: toolbar button "Add part from fab library", refresh
after `component add`), it is a thin client that only calls kifab (`--json`
CLI), holds no logic, and the project works fully without it. Design consequence
now: every core function stays reachable through the CLI with `--json`.
Footprint remap/adopt, placement preview check against JLCPCB's frame, second
fab, scheduled availability notifications, per-repo multi-board config.


## Decisions (settled in review)

- **Search backend**: online and up to date is preferred, no strong opinion
  otherwise. Spike at the start of increment 6 (EasyEDA API, JLCPCB/LCSC web
  endpoints, jlcparts DB), keep whichever is responsive and flexible behind
  `Fab.search`; a local cache/DB may be added for speed and for scheduled checks.
- **GUI toolkit**: PySide6 (optional extra `kifab[gui]`).
- **Symbols**: one `.kicad_sym` per fab, symbols sorted by name.
- **Python** >= 3.10 (dev/CI on 3.12; newer features only if they help),
  **KiCad 10** minimum (drop the KiCad 9 layer-name fallback).
- **CI host**: GitHub (example workflow = GitHub Actions).
- Vendored libraries are checked in; generated build output is not (see Principles).
- black only (no ruff), pytest, uv.
- `populate_jlc_library` (in wrink-hw) is a separate project and **read-only
  reference** here: look at it, copy ideas/code into kifab, never modify it.

## Still open (small, decide when reached)

- Increment 2/5 spike: does a running KiCad pick up new symbols/footprints
  added to already-registered project libraries without reopening the project?
  If not, `component add` must say so (and the GUI can offer a hint).
- Release artifact naming/versioning: proposal `<name>-<git describe>-gerbers.zip`,
  version also written into the bundle report.
- Can KiCad's global library tables be neutralised per project? Probably not;
  `kifab check` then only detects references to non-project libraries.

## Done

(nothing yet)
