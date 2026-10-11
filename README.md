# kifab

Manage KiCad projects in a git-friendly way, with the fabrication service
(initially JLCPCB) as a first-class part of the project.

KiCad stays the editor; kifab manages everything around it: the fab's
libraries (vendored into the project), the fab's design rules (written into the
project so KiCad's own DRC enforces them), git hygiene, and the export for
manufacturing.

## Install

    uv tool install kifab        # or: pip install kifab

Development:

    uv sync
    uv run kifab --version

## Commands

    kifab init <name> <fab> [--layers N] [--no-git]   # new project (fab: jlcpcb)
    kifab rules apply [PROJECT]                       # write the fab's design rules
    kifab check [PROJECT] [--json] [--strict]         # self-contained? rules? git?
    kifab bundle [PROJECT] [--json] [--skip-drc]      # all manufacturing files -> build/
    kifab ci init [PROJECT]                           # GitHub release workflow (tag v*)
    kifab order record [PROJECT]                      # archive what you ordered
    kifab component search TEXT [--basic] [--add]     # find parts (JLCPCB catalogue)
    kifab component add ID...                         # vendor symbol+footprint+3D into lib/
    kifab component sync [PROJECT]                    # vendor every part the schematic uses
    kifab component list [PROJECT]                    # what is vendored (kifab.lock)
    kifab component check [PROJECT] [--boards N]      # stock, Basic/Extended, price
    kifab kicad-plugin install                        # optional KiCad toolbar buttons
    kifab-gui [PROJECT] [--tab parts]                               # the same, graphical (kifab[gui])

`kifab init demo jlcpcb` creates `demo/` with the KiCad project, `kifab.toml`,
`kifab.lock`, project-local library tables pointing at `lib/` (a `kifab` library
with power/ground symbols and an empty `jlcpcb` library), the fab's design rules
in `demo.kicad_dru` and the board-setup constraints in `demo.kicad_pro`, a
`.gitignore` with a kifab-managed block, and runs `git init` (unless the
directory is already inside a repository).

`kifab rules apply` (KiCad must be closed) rewrites the kifab block of the
`.kicad_dru` (your own rules after it are kept) and raises board-setup
constraints that are below the fab's minimum; changed files are backed up to
`kifab-backups/` first.

`kifab check` runs offline on any KiCad project and reports (exit code 1 on
errors, 2 on bad input): symbols, footprints and 3D models that do not come
from a project library (stock KiCad libraries, absolute or
`${KICAD*_3DMODEL_DIR}` paths), library-table entries outside the project,
schematic vs board footprint differences, vendored files that do not match
`kifab.lock`, design rules that drifted from the fab's profile, and git hygiene
(generated files that are tracked, sources that are not).

`kifab bundle` (KiCad must be closed, unless `--force`) runs `kifab check`,
KiCad's DRC (zones refilled in memory; the board file is not modified), plots
gerbers for all copper layers, drill files, and writes into `build/`:
`<name>_gerbers.zip` (upload as "PCB"), `<name>_bom_jlcpcb.csv`,
`<name>_cpl_jlcpcb.csv`, `<name>_report.md` and `<name>_manifest.json`.
Through-hole parts are left out of BOM and placement (hand-soldered) unless
`--assemble-through-hole` or `[bundle] assemble_through_hole = true`; DNP parts
are left out; BOM lines without an LCSC number are warned about. Time stamps in
the files are set to the commit time (or `$SOURCE_DATE_EPOCH`) and the zip is
deterministic, so the same commit gives the same bytes. Exit codes: 0 ok,
1 errors (or warnings with `--strict`), 2 bad input.

`kifab ci init` (or `kifab init --ci`) writes `.github/workflows/release.yml` at
the repository root: on a `v*` tag it runs `kifab bundle` in the `kicad/kicad`
docker image and attaches `<name>-<version>-gerbers.zip`, BOM, CPL and report to
a GitHub release. `kifab order record` copies the current (fresh, error-free)
bundle into `orders/<date>-<version>/` with an `order.toml` (commit, tags,
versions, input and file hashes) and stages it.

`kifab component add C123456` runs easyeda2kicad (a dependency) into a scratch
directory and vendors the result: the symbol is merged (sorted) into
`lib/jlcpcb/jlcpcb.kicad_sym`, the footprint goes to `lib/jlcpcb/jlcpcb.pretty/`
(through_hole/smd attribute bug fixed, 3D path rewritten to
`${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/...`), the 3D models to `.3dshapes/`, and
`kifab.lock` records every file's hash. A footprint shared by several parts is
kept as first vendored (`--refresh` replaces it). `component sync` does this for
every LCSC number in the schematic (it never edits the schematic or board).

Offline mode: `KIFAB_OFFLINE=tests/fixtures` makes search, lookups and
`component add` answer from the synthetic test fixtures instead of the network.

`kicad-cli` is found on `PATH`, or set `KIFAB_KICAD_CLI` to the command to run.

`kifab-gui` (install with `pip install 'kifab[gui]'`, PySide6) opens or creates
a project, runs check and bundle on a worker thread with live progress, and
shows the findings in a sortable, filterable table with details; a Parts tab
searches the fab's catalogue as you type (Basic/Extended badge, stock, price)
and adds parts or syncs the schematic's parts into the project; it can also
apply the fab's rules, check stock during bundle, record an order, tag a
release, open the project in KiCad and open the build folder.

`kifab kicad-plugin install` adds two toolbar buttons to KiCad 10 (pcbnew,
eeschema, project manager): "kifab" opens `kifab-gui` on the open project and
"Add part (kifab)" opens it on the Parts tab. Enable the IPC API server in
KiCad's Preferences > Plugins first. The plugin only launches kifab; kifab
manages KiCad, never the other way round.

See `plan.md` for the state of each part and what is still unverified.

## Development checks

    scripts/check.sh          # black --check, mypy --strict, pytest

The tests need neither KiCad nor network: `kicad-cli` and easyeda2kicad are
replaced by stand-ins in `tests/stubs/`, and the fab's catalogue answers from
`tests/fixtures/`, which is synthetic data written by
`tests/fixtures/generate.py` (the same offline mode by hand:
`KIFAB_OFFLINE=tests/fixtures uv run kifab-gui`).

Tests against a real KiCad: `uv run pytest tests/test_real_kicad.py` uses
`kicad-cli` from PATH, or `KIFAB_REAL_KICAD_CLI=scripts/kicad-cli-docker` runs
it from the `kicad/kicad:10.0` docker image (no KiCad install needed).

On a machine with network access to JLCPCB/EasyEDA,

    uv run python scripts/local_verify.py --part C123456

checks the live search, a part-number lookup, a real easyeda2kicad fetch and
the real-KiCad tests, and prints a pass/fail report. Captured answers stay in a
scratch directory: they are third-party data and are not committed; what they
teach goes into the synthetic fixtures.
