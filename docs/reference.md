# kifab reference

Details behind the commands in the [README](../README.md).

## New projects

`kifab init my_project jlcpcb` creates `my_project/` with the KiCad project, `kifab.toml`,
`kifab.lock`, project-local library tables pointing at `lib/` (a `kifab` library
with power/ground symbols and an empty `jlcpcb` library), the fab's design rules
in `my_project.kicad_dru` and the board-setup constraints in `my_project.kicad_pro`, a
`.gitignore` with a kifab-managed block, and runs `git init` (unless the
directory is already inside a repository).

## Fabs, suppliers and library sources

Parts have three independent roles, each a plugin: the **fab** (design rules,
order files), the **suppliers** (search, stock, price) and the **library
source** (symbols, footprints and 3D models). The fab brings defaults (JLCPCB:
supplier `jlcpcb`, library source `easyeda`, library `jlcpcb`); a `[parts]`
table in `kifab.toml` overrides them:

    [parts]
    suppliers = ["jlcpcb"]        # preferred first; searches go to the first
    library_source = "easyeda"    # where vendored parts come from
    library = "jlcpcb"            # nickname of lib/<name>/ (keep it once used)

`kifab component search --supplier NAME` searches another supplier once.

**jlcpcb** (profile `standard`): BOM by LCSC number.

**aisler** (profiles `hasl`, the default, and `enig` for the finer limits ENIG
allows): supplier `lcsc` by default; BOM by MPN with manufacturer and supplier
numbers, which AISLER's BOM import matches and asks you to confirm; placement as
KiCad writes it. `kifab bundle` writes `<name>_aisler.zip` to upload, laid out
like the zip AISLER's own KiCad plugin uploads: gerbers, drill files, an
IPC-D-356 netlist, `components.json` (parts with position, MPN and package) and
the board as ODB++. The BOM and placement CSVs next to it are for reading.

Suppliers: **jlcpcb** (JLCPCB's parts library: Basic/Extended, JLCPCB's stock)
and **lcsc** (the same catalogue by LCSC number, without JLCPCB's part classes;
its stock is JLCPCB's, an approximation of LCSC's).

The stock check (`component check`, `bundle --stock`) asks the suppliers in
order, by their own number when the symbol has one, else by MPN, and takes the
first with enough stock. Vendored symbols get `MPN` and `Manufacturer` fields
from the catalogue, so fabs that buy from distributors know what to order.

## Design rules: `kifab rules apply`

KiCad must be closed. It rewrites the kifab block of the `.kicad_dru` (your own
rules after it are kept) and raises board-setup constraints that are below the
fab's minimum; changed files are backed up to `kifab-backups/` first.

## Checking: `kifab check`

Runs offline on any KiCad project and reports (exit code 1 on errors, 2 on bad
input): symbols, footprints and 3D models that do not come from a project
library (stock KiCad libraries, absolute or `${KICAD*_3DMODEL_DIR}` paths),
library-table entries outside the project, schematic vs board footprint
differences, vendored files that do not match `kifab.lock`, design rules that
drifted from the fab's profile, and git hygiene (generated files that are
tracked, sources that are not).

The design settings are checked against the fab's limits before any DRC: the
board-setup minimums, net classes (clearance, track width, via size and drill,
annular ring, diff pairs), the pre-defined track, via and diff-pair sizes
(errors), your own `.kicad_dru` rules after kifab's block that allow less than
the fab (warnings: later rules override kifab's), and the board's copper layer
count against `kifab.toml`. Run it from the GUI's Check button or `kifab check`
whenever you change Board Setup.

## Bundling: `kifab bundle`

KiCad must be closed, unless `--force`. It runs `kifab check`, KiCad's DRC
(zones refilled in memory; the board file is not modified), plots gerbers for
all copper layers and drill files, and writes into `build/`:
`<name>_gerbers.zip` (upload as "PCB"), `<name>_bom_jlcpcb.csv`,
`<name>_cpl_jlcpcb.csv`, `<name>_report.md` and `<name>_manifest.json`.

Through-hole parts are left out of BOM and placement (hand-soldered) unless
`--assemble-through-hole` or `[bundle] assemble_through_hole = true`; DNP parts
are left out; BOM lines without an LCSC number are warned about. Time stamps in
the files are set to the commit time (or `$SOURCE_DATE_EPOCH`) and the zip is
deterministic, so the same commit gives the same bytes. Exit codes: 0 ok,
1 errors (or warnings with `--strict`), 2 bad input.

## Releases and orders

`kifab ci init` (or `kifab init --ci`) writes `.github/workflows/release.yml` at
the repository root: on a `v*` tag it runs `kifab bundle` in the `kicad/kicad`
docker image and attaches `<name>-<version>-gerbers.zip`, BOM, CPL and report to
a GitHub release.

`kifab order record` copies the current (fresh, error-free) bundle into
`orders/<date>-<version>/` with an `order.toml` (commit, tags, versions, input
and file hashes) and stages it.

## Vendored parts

`kifab component add C123456` runs easyeda2kicad (a dependency) into a scratch
directory and vendors the result: the symbol is merged (sorted) into
`lib/jlcpcb/jlcpcb.kicad_sym`, the footprint goes to `lib/jlcpcb/jlcpcb.pretty/`
(through_hole/smd attribute bug fixed, 3D path rewritten to
`${KIPRJMOD}/lib/jlcpcb/jlcpcb.3dshapes/...`), the 3D models to `.3dshapes/`, and
`kifab.lock` records every file's hash. A footprint shared by several parts is
kept as first vendored (`--refresh` replaces it).

A vendored symbol without a description gets the fab catalogue's description of
the part, so KiCad's symbol chooser and the placed symbol's Description field
show it, as do `component list` and the GUI's project library; adding an
already vendored part again fills it in. `component sync` does this for every
LCSC number in the schematic (it never edits the schematic or board).

`kifab component add` also takes an MPN (`kifab component add KF-R330-0603`),
and `component sync` vendors symbols that only carry an `MPN` field: both look
the MPN up at a supplier selling by the library source's numbers (LCSC numbers
for EasyEDA).

Pin names that only repeat the pin number (EasyEDA names a resistor's pins "1"
and "2") are dropped from vendored symbols; when no real names are left, pin
names are hidden, and for two-pin parts the pin numbers too, like KiCad's own
resistor. Real names (GND, P0.00/XL1, ...) stay.

## Cache

Fetched parts are kept in kifab's cache (`~/.cache/kifab` on Linux,
`KIFAB_CACHE` overrides; safe to delete) for 30 days, so a part is downloaded
from EasyEDA once; `--refresh` and the GUI's "Refresh from fab" fetch afresh. A
fetch that fails (EasyEDA throttles bursts) is repeated after 3 and 8 s.

## The GUI

`kifab-gui` (`pip install 'kifab[gui]'`, PySide6) starts on a page with the
recent projects (File > Open recent, File > Close project). The Parts tab, shown
first, lists the project library (each part's symbol, footprint and how many
schematic/board references use it; parts the schematic uses but that are not
vendored) with:

- **Search at fab**: looks the selected part up in the catalogue below.
- **Refresh from fab**: fetches it again; fills in a missing description and
  offers to replace files that differ from the fab's current ones.
- **Sync** and **Remove**.

The catalogue search runs as you type (Basic/Extended badge, stock, price).
Typing R, C or L and a value (`R 4k7`, `R4.7k`, `R 4700`, `R 1R5`, `C 18p`,
`L 10u`), optionally followed by more words (`R 4k7 0603 1%`), lists only parts
of that kind and value, Basic first; "±10% alternatives" adds the neighbouring
E24 values. Both tables sort by clicking a column title; when a search holds
more results than were loaded, clicking Stock (most first) or 1 pc (cheapest
first) asks the fab for that order, and "Load more" fetches the next page.

Selecting a part shows its product photo (when the fab has one), its footprint
and symbol drawn by kifab (fetched into kifab-gui's cache on request for parts
not in the project), a table of facts and price breaks, and links to the
datasheet and product page.

Check and Bundle run on a worker thread with live progress; their findings go to
the "Check & bundle results" tab. The GUI can also apply the fab's rules, check
stock during bundle, record an order, tag a release, open the project in KiCad
and open the build folder.

kifab-gui keeps its own settings (recent projects) and cache in the usual
per-user places (`~/.config/kifab-gui`, `~/.cache/kifab-gui` on Linux;
`KIFAB_GUI_HOME` overrides both). The `kifab` command never reads them, and
nothing of it is stored in a project.

## KiCad plugin

`kifab kicad-plugin install` adds a "kifab" toolbar button to KiCad 10 (pcbnew,
eeschema, project manager). It runs `kifab-gui <project directory>` for the
project open in KiCad. Enable the IPC API server in KiCad's Preferences >
Plugins first. The plugin only launches kifab; kifab
manages KiCad, never the other way round.
