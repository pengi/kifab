# pcbfab: notes for a PCB manufacturing tool

Hand-over notes for designing a tool that manages libraries against manufacturers, downloads
footprints, and exports order files (gerbers, BOM, placement). Written at the end of a long session
in which two small Python tools were built and a real board was prepared for a JLCPCB order. It
records what exists, what was learned, what the owner wants, and what is still open, so a new
conversation can start from here without redoing the groundwork.

Nothing in this file has been committed. The code described is in the working tree.

---

## 1. Context

- **Repo:** `/home/pengi/src/wrink/wrink-hw` (git root). One board so far, `rpi-nrf52840-hat/`; more
  boards are expected, so everything must be generic across boards in the repo.
- **The board:** a Raspberry Pi 4 HAT with an nRF52840 module (Ebyte E73-2G4M08S1C). KiCad 10.0.6
  (`kicad-cli` 10.0.6, Ubuntu 24.04). To be ordered **with assembly from JLCPCB**; the through-hole
  parts (J1, J2, J3 headers and TP1) are ordered and hand-soldered by the owner, outside the
  assembly order.
- **Owner's goals for the tool** (said in passing, not yet specified): "manage my libraries against
  manufacturers to start with", then downloading and exporting gerber files and such. They want to
  fix the recurring pain of footprints/origins/rotations not matching what the manufacturer assembles
  from, and of orderability checks (stock, part type, "difficult to process").
- **Owner's working style** (matters for how to collaborate):
  - Keeps KiCad open while working, saves often. Wants live help where possible, but file-based
    work is fine when KiCad is closed or when working on a copy.
  - Prefers a proper Python package over "creative bash magic".
  - Wants JLCPCB-compatible footprints kept **unmodified** and in sync with JLCPCB's; no hand edits.
  - Cares about reducing the number of distinct part types (one cap, one resistor value family).
  - Their global instruction: never add `Co-Authored-By:` or other Claude attribution to commit
    messages.

---

## 2. What exists today

All in `populate_jlc_library/` (repo root; untracked or partly staged at the time of writing):

```
populate_jlc_library/
  pyproject.toml        name populate-jlc-library 0.2.0; setuptools; python >= 3.9;
                        dependency easyeda2kicad>=0.8.0; two console scripts (below)
  README.md             user documentation for both tools
  src/populate_jlc_library/
    __init__.py         version
    __main__.py         python -m populate_jlc_library -> populate-jlc-library
    sexpr.py            tiny S-expression parser for KiCad files (parse/head/children/first/properties)
    schematic.py        collect_parts(root, fields) -> Collection(parts, without_lcsc, sheets)
    project.py          resolve(path) -> Target(project_dir, root_schematic, project_file);
                        register_library(project_dir, name, pretty_dir, descr); InputError
    downloader.py       Downloader: wrapper around easyeda2kicad (see quirks, section 5)
    library.py          populate(parts, downloader, ...), merged lcsc_footprints.csv map,
                        fix_attributes() post-processing
    cli.py              populate-jlc-library
    fab.py              the order-file steps (Job, generate, convert_bom, convert_cpl, ...)
    fab_cli.py          jlcpcb-fab
  tests/
    test_populate.py, test_fab.py     30 tests in total, all passing
    stub_easyeda2kicad.py, stub_kicad_cli.py   stand-ins selected by environment variables
```

Run the tests: `PYTHONPATH=src python3 -m unittest discover -s tests` from `populate_jlc_library/`.
They need neither network nor KiCad. The stand-ins only prove the tool's own logic; the formats they
emit were copied from real `kicad-cli` 10.0.6 output.

Run the tools (uv; **use `--with-editable`**, because plain `--with ./path` caches a built wheel and
silently runs old code after edits; this happened in the session):

```sh
uv run --with-editable ./populate_jlc_library populate-jlc-library <project> --register
uv run --with-editable ./populate_jlc_library jlcpcb-fab [<project> ...] [--skip-drc] [--force] [-o DIR]
```

### 2.1 `populate-jlc-library`

`schematic (.kicad_sch / .kicad_pro) -> LCSC numbers -> easyeda2kicad -> <project>/jlcpcb-lib/`

- Walks the schematic and its sub-sheets (cycle-safe), collecting every symbol that has an LCSC
  number in any of the fields `LCSC`, `LCSC Part #`, `LCSC Part`, `LCSC#`, `JLCPCB Part #`,
  `JLCPCB`, `JLC` (case-insensitive; `--field` adds more). Power symbols and `#` references are
  ignored. Parts without a number are reported.
- Output: `jlcpcb.pretty/` (one `.kicad_mod` per EasyEDA footprint, shared by parts with the same
  package), `jlcpcb.3dshapes/` (`.step`, `.wrl`, referenced as `${KIPRJMOD}/...`),
  `lcsc_footprints.csv` (columns `lcsc, refs, value, status, footprint_files`; merged across runs),
  `download.log`.
- `--register` adds the library to the project's `fp-lib-table`. Options: `--dry-run`, `--only`,
  `--skip-dnp`, `--full` (symbols too), `--overwrite`, `--out`, `--lib-name`, `--delay`, `--timeout`.
  Exit codes 0 ok, 1 some downloads failed, 2 bad input.
- **Post-processing:** easyeda2kicad writes `(attr through_hole)` for some all-SMD footprints
  (seen on C0805 and the 3.2x1.5 crystal). That makes KiCad's `--smd-only` position export drop the
  part, and makes KiCad warn "Footprint ... does not match copy in library". `library.fix_attributes`
  changes it to `(attr smd)` when no pad is drilled. Real through-hole footprints are left alone.
- The tool **never edits schematics or boards.** Remapping an existing board onto the new library is
  not part of it (see section 7 and the appendix).

### 2.2 `jlcpcb-fab`

Produces everything for an order into `<project dir>/gerber/` (git-ignored; `git add -f` to record an
order):

- `<name>-<layer>.g*`, `<name>-PTH.drl`, `<name>-NPTH.drl` (+ PDF maps), `<name>-job.gbrjob`
- `<name>_gerbers.zip` (gerbers, drill files and the job file; maps excluded): upload as "PCB"
- `<name>_bom_jlcpcb.csv`, `<name>_cpl_jlcpcb.csv` in JLCPCB's column names

Steps (`fab.generate`): refuse if KiCad has the board open (`~<pcb>.lck`; `--force` overrides), run
DRC (`kicad-cli pcb drc --refill-zones --save-board --schematic-parity --severity-error
--severity-warning --exit-code-violations`; any violation stops the run; user exclusions are
respected; **this rewrites the .kicad_pcb**), plot gerbers (`--check-zones --subtract-soldermask`),
export drill (Excellon, mm, decimal zeros, separate PTH/NPTH, route ovals, absolute origin, PDF
map), zip, BOM, CPL.

Design points worth keeping:

- Everything is built in a temporary staging directory; the output directory is only changed at the
  end, and only files starting with the board name are replaced. A failed run leaves old results.
- Layer names: KiCad 10 uses `F.Silkscreen`/`B.Silkscreen`; older versions `F.SilkS`/`B.SilkS`.
  The tool tries the new names and falls back to the old ones, then checks that all required files
  (`.gtl .gbl .gts .gbs .gto .gbo .gm1`) exist.
- BOM: from `kicad-cli sch export bom --exclude-dnp --fields Value,Reference,Footprint,LCSC --labels
  Comment,Designator,Footprint,"LCSC Part #" --group-by Value,Footprint,LCSC --ref-delimiter ','
  --ref-range-delimiter ''`, then through-hole designators are removed (python). Lines without LCSC
  are warned about.
- CPL: `kicad-cli pcb export pos --format csv --units mm --side both --exclude-dnp` (all parts) and
  again with `--smd-only`; through-hole = in the first but not the second. CPL columns `Designator,
  Mid X, Mid Y, Layer, Rotation` with `mm`-suffixed coordinates (4 decimals), `Top`/`Bottom`, rotation
  `% 360`. **No per-part corrections**: see "origin and rotation" below.
- Project discovery with no argument: every `.kicad_pro` in the enclosing git repository, skipping
  hidden directories and `*-backups`.
- Exit codes 0 ok, 1 a step failed (any board), 2 bad input. Verified: with the real `kicad-cli` on a
  copy of the project the BOM/CPL were byte-identical to the earlier bash script's.

The old `rpi-nrf52840-hat/generate_gerber.sh` (bash plus an embedded Python block) still exists and
can be deleted once the owner is happy with `jlcpcb-fab`.

---

## 3. Manufacturer rules that bit us (JLCPCB)

- **Assembly uses EasyEDA's footprint for each LCSC part.** The placement file's origin and rotation
  are interpreted in *that* footprint's frame. If the KiCad footprint differs (origin, orientation),
  the JLCPCB placement preview shows the part offset or rotated. Symptoms seen: the E73 module
  offset (JLCPCB's origin was exactly 2.0 mm from the body centre of the Nordic-library footprint)
  and the SOIC-8 rotated 270 degrees. Per-footprint offset tables in the export script worked but
  were a patchwork. **The robust fix is to use JLCPCB's own footprint** (`populate-jlc-library`);
  then KiCad's origin and rotation equal JLCPCB's and the CPL needs no corrections. The preview
  still has to be checked by eye for polarized or rotated parts (modules, ICs, LEDs).
- A custom footprint is fine if its pads coincide with JLCPCB's. D1 (RGB LED, 1.6x1.5 mm, pads
  0.5x0.8 at (+-0.6, +-0.5), numbering 1=R, 2=A, 3=G, 4=B for a common-anode part) was verified that
  way.
- **Part types:** Basic parts have no extra setup fee; Extended parts do. The BOM check can flag a
  part as "difficult to process, reviewed after ordering, additional fee possible": the 3.2x1.5 mm
  crystal first chosen got that flag; the replacement C7462182 did not. A tool that warns before
  ordering would help. LCSC stock numbers can differ between the API/database and the website; the
  owner checked the website manually for one part.
- **Capabilities used for DRC** (in `rpi-nrf52840-hat/rpi-nrf52840-hat.kicad_dru`, comments cite
  https://jlcpcb.com/capabilities/pcb-capabilities): outer track 0.127 mm width/spacing, inner 0.09,
  silkscreen text >= 1.0 mm high and 0.15 mm stroke, pad-to-silkscreen 0.15, board edge to track 0.3,
  hole 0.2 to 6.3 mm, NPTH >= 0.5, hole-to-hole 0.5 (different nets) / 0.254 (same), track to hole
  0.254 (NPTH) / 0.33 (PTH), via annular ring. JLCPCB enlarges the annular ring of PTH to 0.15 mm
  itself; the board-setup minimum annular width is 0.25 mm here.
- **Annular ring rule:** do not use a custom rule for it. A custom rule matching all plated items
  overrides the board-setup minimum and hid a real setting (the global change "had no effect").
- JLCPCB clips silkscreen off exposed pads when processing the gerbers (not independently
  verified for this order).
- Through-hole parts and test points are left out of BOM/CPL when the owner solders them.

---

## 4. KiCad facts that bit us

- **Library tables are read when the project is opened.** Adding a `jlcpcb` row to `fp-lib-table`
  while KiCad runs gives "The current configuration does not include the footprint library 'jlcpcb'"
  until the project is reopened (or the row is added in Preferences). Opening the library dialog and
  pressing OK without the row can write KiCad's in-memory table back and drop the new entry.
- KiCad overwrites project files on save. Do not edit `.kicad_pro`, `.kicad_pcb` or the schematic
  while the editors are open (the `.lck` files show it). A scripted edit is safe only when closed;
  analysis is safe on a **copy** (`kicad-cli pcb drc` without `--save-board` does not modify the
  board, but `--refill-zones --save-board` does).
- **DRC codes met:** `footprint_filters_mismatch` (symbol `ki_fp_filters` copied to footprints; the
  owner set it to ignore), `lib_footprint_mismatch` ("does not match copy in library": here caused by
  the through_hole/smd attribute, see 2.1), `silk_overlap` ("Silkscreen clearance", from the custom
  pad-to-silkscreen rule), `silk_over_copper` ("Silkscreen clipped by solder mask": severity only,
  cannot be scoped by a custom rule), `starved_thermal` ("Thermal relief connection to zone
  incomplete ... min spoke count 2; actual 1"; the minimum is `min_resolved_spokes` under Board Setup >
  Design Rules > Constraints; a pad with 0 spokes still counts as starved when the minimum is 1).
- **Custom rule scoped to a library:** `A.memberOfFootprint('jlcpcb:*')` works in DRC conditions
  (verified with a negative test). It was used to relax pad-to-silkscreen clearance for JLCPCB's
  footprints only:

  ```
  (rule "Pad to Silkscreen (JLCPCB library footprints)"
      (layer outer)
      (condition "A.Type == 'pad' && B.Layer == '?.Silkscreen' && A.memberOfFootprint('jlcpcb:*')")
      (constraint silk_clearance (min 0mm)))
  ```
  EasyEDA footprints draw silkscreen 0.01-0.14 mm from pads, so 92 warnings appeared otherwise.
  Rules later in the file win.
- **Net class vs zone:** a net class track width (e.g. 0.4 mm for the power class) applies to tracks
  only. Thermal spoke width, thermal gap and zone minimum width are zone properties (with a per-pad
  override in pad properties). Spoke *width* does not affect the starved-thermal *count*.
- Pad angles in `.kicad_pcb` files are absolute (not relative to the footprint); flipped footprints
  store mirrored coordinates. KiCad coordinates are y-down, rotation positive counter-clockwise on
  screen. The position export negates y (`PosY = -y`).
- **pcbnew's Python module** (`/usr/bin/python3`, `import pcbnew`) is the safe way to script board
  edits (footprint replacement, pad overrides) on a closed board. Gotchas: `SetField(name, text)`;
  `new.SetFPID(pcbnew.LIB_ID("jlcpcb", name))`; footprint attributes are bit flags (`FP_THROUGH_HOLE
  = 1`, `FP_SMD = 2`); a harmless `PROPERTY_ENUM ... No enum choices defined` assertion line is
  printed on import.
- `kicad-cli`: `pcb export gerbers -o <dir>/` needs the trailing slash; `sch export bom` and
  `pcb export pos` take `-o <file>`; DRC JSON (`--format json --severity-all`) lists `violations`,
  `unconnected_items`, `schematic_parity`.
- **kicad-live MCP** (in the owner's Claude setup): has `ipc` (live KiCad) and `swig` (file-based,
  can be stale) backends. `sync_schematic_to_board` via swig wrote junk nets and duplicated parts
  even though it said auto-save was refused; avoid it and use F8 ("Update PCB from Schematic") in
  KiCad. Live DRC and the GUI driver (AT-SPI) work. The JLCPCB parts database for that MCP is
  already stored by the owner under `~/.local` (about 1.5 GB); do not keep another copy.

---

## 5. easyeda2kicad quirks (v1.0.1 used)

- Invocation used: `python -m easyeda2kicad --lcsc_id=C123 --footprint --3d --output
  <dir>/<lib> [--overwrite] [--project-relative]` (or `--full` for symbols too).
- `--project-relative` computes the 3D path relative to the **current working directory**, so the
  working directory must be an ancestor of the output; the Downloader sets `cwd` to the project dir
  and omits the flag if the library is outside the project.
- A footprint that already exists makes the tool exit 1 with an "already exists" message unless
  `--overwrite` is given. The Downloader maps that to status `exists`. Several LCSC numbers can map
  to one footprint (all 0603 resistors -> `R0603`); only the first creates a file.
- The easyeda.com API returned 403 for a plain `curl` from the CLI, but the Python tool worked.
- Footprint names come from EasyEDA, e.g. `C0603`, `C0805`, `R0603`,
  `CRYSTAL-SMD_L3.2-W1.5-1`, `WIRELM-SMD_E73-2G4M08S1C`, `SOP-8_L4.9-W3.9-P1.27-LS6.0-BL`,
  `LED-SMD_4P-L1.6-W1.5_P4-1615G2B2R4TS2-06T-001-24`, `HDR-TH_40P-P2.54-V-F-R2-C20-S2.54`,
  `HDR-TH_8P-P2.54-V-M-R2-C4-S2.54`.
- The generated E73 footprint has its origin 2.0 mm off the body centre and no antenna keepout;
  the owner accepted removing the keepout from the board ("I know it anyway") and keeping the
  library untouched.
- Re-downloading with `--overwrite` regenerates files; any hand edit of a library file is lost.
  That is why the attribute fix lives in the tool.

---

## 6. State of the real board (as of the end of the session)

- 22 footprints on the board were swapped to `jlcpcb:` ones (C1-C3, C5, R1-R15, U1, U2, X1); the
  schematic's Footprint fields were rewritten to match. D1 uses the project's own footprint
  `rpi-nrf52840-hat:LED_RGB_1.6x1.5mm_CA_P4-1615`.
- Parts: 330 ohm for R1-R8, R12, R13, R15; 4.7k for R9-R11, R14 (R9, R10, R14 do-not-populate);
  4.7 uF/0805 for C1 and C5 (C20416422); 18 pF/0603 for C2 and C3 (C1647); crystal C7462182
  (32.768 kHz, 12.5 pF load; the 18 pF caps were chosen from the nRF52840 equation
  C = 2*CL - C_pin - C_pcb with C_pin = 4 pF, cross-checked from several sources); U1 C356849
  (E73-2G4M08S1C), U2 C18723546 (24C32), D1 C7496855, J2 C19725278, J1/J3 C41413841.
- DRC on a copy of the saved board was clean apart from the choices described in section 4. The
  owner then decided to set the minimum spoke count to 1 and widen the GND spokes (U1 pad 21 sits
  between two VDD pads and can only ever have one spoke). `lib_footprint_mismatch` was cured by the
  attribute fix.
- `rpi-nrf52840-hat/jlcpcb-lib/` was regenerated by the owner a few times with the tool; the files
  currently match the tool's output.

---

## 7. Ideas for the bigger tool (not designed yet)

The owner wants a tool that handles "libraries against manufacturers", "downloading and exporting
gerber files and such". Candidate pieces, roughly in the order they would pay off:

1. **Footprint remap / adopt.** Productise the swap done with a throwaway script (appendix): replace
   board footprints with library ones keeping position, side, nets, DNP/attributes, fields and
   Reference/Value text, pick the rotation (0/90/180/270) that makes pads coincide, optionally anchor
   on a body offset (the E73 needed (0, 2.0) mm from the pad-fit origin), and rewrite the schematic
   Footprint property to match. Must run with the editors closed and take a backup first.
2. **Library consistency checks.** Library vs board vs schematic: attribute (smd/through_hole),
   pad numbering vs symbol pins, missing 3D models, footprint name collisions, silkscreen too close to
   pads (report only, do not edit).
3. **Orderability checks before export.** Per BOM line: LCSC exists, in stock, Basic vs Extended,
   "difficult to process" flag if obtainable, price; suggest alternates (the same-value-different
   -footprint search that found the replacement crystal); minimise the number of distinct types.
   Use a local, incrementally updatable parts database; do not re-download 1.5 GB.
4. **Placement preview check without uploading.** Compute where JLCPCB will put each part from its
   footprint (pad centroid / origin / rotation) and compare with the CPL, instead of eyeballing the
   JLCPCB preview. The 2.0 mm offset and the 270-degree rotation were found by trial and error.
5. **Design-rule profiles per manufacturer.** Generate the `.kicad_dru` and the board-setup minimums
   from a manufacturer capability table (JLCPCB today; PCBWay and others later), including the
   scoped silkscreen rule above.
6. **Order package.** Today `jlcpcb-fab` writes the files. Next: version/tag the order (the
   `gerber/` directory is ignored on purpose), hash the inputs, keep what was ordered, maybe drive the
   order upload.
7. **Multi-board repo support.** Already partly there (project discovery); a shared library for
   several boards and a per-repo config file would be next.

Open design questions to settle in the next chat:

- One package with subcommands (`pcbfab populate|fab|remap|check`) or separate tools? The current
  package name `populate-jlc-library` no longer describes it; renaming breaks the owner's uv
  command, so rename together with the first real feature.
- Where manufacturer data comes from (EasyEDA API via easyeda2kicad, JLCPCB parts database in
  `~/.local`, LCSC pages) and how to cache it.
- Whether to keep depending on `kicad-cli`/`pcbnew` (yes, both are stable interfaces; `pcbnew` only
  for edits) or parse more files directly (`sexpr.py` exists for reading; writing is riskier).
- Whether the tool should ever write the schematic/board itself, or only report and produce scripts.
  Everything done so far wrote files only with KiCad closed and with a backup.

---

## 8. Conventions and gotchas for whoever continues

- Keep tests offline: stub the external commands through the existing environment variables
  (`POPULATE_JLC_EASYEDA2KICAD`, `POPULATE_JLC_KICAD_CLI`).
- Never run `kicad-cli ... --save-board` or a scripted edit on files KiCad has open; work on a copy
  in a scratch directory when analysing.
- `.gitignore` (repo root) ignores locks, `.history/`, `*-backups/`, `fp-info-cache`, `*.kicad_prl`,
  `*-bak`, `_autosave-*`, DRC/render output, `download.log`, `gerber/`, and Python build artefacts. The
  library (`jlcpcb-lib/`), `fp-lib-table`, the `.kicad_*` sources and `generate_gerber.sh` /
  the package are meant to be committed.
- Commit messages: no `Co-Authored-By` or other Claude attribution (owner's standing instruction).
- Files relevant to the real board: `rpi-nrf52840-hat/rpi-nrf52840-hat.kicad_{pro,sch,pcb,dru}`,
  `fp-lib-table`, `rpi-nrf52840-hat.pretty/` (D1), `jlcpcb-lib/`.

---

## Appendix: the footprint swap script (throwaway, worth productising)

This ran once with `/usr/bin/python3 remap_footprints.py <project_dir>` on a closed project, after a
backup. It is reproduced here because it lived only in a scratch directory.

```python
"""Swap board footprints for the JLCPCB (EasyEDA) ones from jlcpcb-lib, keeping pads where they are."""
import math, re, sys
from pathlib import Path
import pcbnew

PROJ = Path(sys.argv[1])
PCB = PROJ / "rpi-nrf52840-hat.kicad_pcb"
SCH = PROJ / "rpi-nrf52840-hat.kicad_sch"
LIBDIR = PROJ / "jlcpcb-lib" / "jlcpcb.pretty"
NICK = "jlcpcb"

MAPPING = {}                                   # reference -> footprint name in the library
for r in ("C2", "C3"): MAPPING[r] = "C0603"
for r in ("C1", "C5"): MAPPING[r] = "C0805"
for i in range(1, 16): MAPPING[f"R{i}"] = "R0603"
MAPPING["U1"] = "WIRELM-SMD_E73-2G4M08S1C"
MAPPING["U2"] = "SOP-8_L4.9-W3.9-P1.27-LS6.0-BL"
MAPPING["X1"] = "CRYSTAL-SMD_L3.2-W1.5-1"
# origin must be located from the module BODY, not pad centres:
# ref -> (body centre in the NEW footprint's frame, body centre in the OLD footprint's frame), mm
BODY = {"U1": ((0.0, 2.0), (0.0, 0.0))}

mm, fromMM = pcbnew.ToMM, pcbnew.FromMM

def rot(x, y, deg):                            # KiCad: y down, positive angle = counter-clockwise on screen
    t = math.radians(deg)
    return x * math.cos(t) + y * math.sin(t), -x * math.sin(t) + y * math.cos(t)

board = pcbnew.LoadBoard(str(PCB))
for ref, name in MAPPING.items():
    old = board.FindFootprintByReference(ref)
    assert not old.IsFlipped(), f"{ref} is on the back side"
    new = pcbnew.FootprintLoad(str(LIBDIR), name)
    new.SetFPID(pcbnew.LIB_ID(NICK, name))

    oldp = {p.GetNumber(): (mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in old.Pads()}
    newp = {p.GetNumber(): (mm(p.GetPosition().x), mm(p.GetPosition().y)) for p in new.Pads()}
    common = sorted(set(oldp) & set(newp))
    assert len(common) == len(newp) == len(oldp), (ref, "pad numbers differ")
    best = None
    for th in (0, 90, 180, 270):               # rotation that makes the pad centres coincide
        rp = {n: rot(*newp[n], th) for n in common}
        tx = sum(oldp[n][0] - rp[n][0] for n in common) / len(common)
        ty = sum(oldp[n][1] - rp[n][1] for n in common) / len(common)
        err = max(math.hypot(oldp[n][0] - rp[n][0] - tx, oldp[n][1] - rp[n][1] - ty) for n in common)
        if best is None or err < best[0]:
            best = (err, th, tx, ty)
    err, th, tx, ty = best
    ox, oy = mm(old.GetPosition().x), mm(old.GetPosition().y)
    if ref in BODY:
        (nbx, nby), (obx, oby) = BODY[ref]
        rx, ry = rot(nbx, nby, th)
        ex, ey = rot(obx, oby, old.GetOrientationDegrees())
        tx, ty = ox + ex - rx, oy + ey - ry
    else:
        tx, ty = ox, oy                        # same origin convention: keep the old origin exactly
    new.SetOrientationDegrees(th)
    new.SetPosition(pcbnew.VECTOR2I(fromMM(tx), fromMM(ty)))
    new.SetLayer(old.GetLayer())
    new.SetAttributes(old.GetAttributes())
    new.SetDNP(old.IsDNP())
    new.SetPath(old.GetPath()); new.SetSheetname(old.GetSheetname()); new.SetSheetfile(old.GetSheetfile())
    new.SetReference(old.GetReference()); new.SetValue(old.GetValue())
    for fld in old.GetFields():                # LCSC, MPN, Description, ... plus text appearance
        n = fld.GetName()
        if n in ("Reference", "Value"):
            tgt = new.Reference() if n == "Reference" else new.Value()
        elif new.HasField(n):
            tgt = new.GetField(n)
        else:
            new.SetField(n, fld.GetText()); tgt = new.GetField(n)
        tgt.SetText(fld.GetText()); tgt.SetPosition(fld.GetPosition()); tgt.SetTextAngle(fld.GetTextAngle())
        tgt.SetLayer(fld.GetLayer()); tgt.SetTextSize(fld.GetTextSize())
        tgt.SetTextThickness(fld.GetTextThickness()); tgt.SetVisible(fld.IsVisible())
        tgt.SetHorizJustify(fld.GetHorizJustify()); tgt.SetVertJustify(fld.GetVertJustify())
    for p in new.Pads():                       # nets and pin function/type
        for q in old.Pads():
            if q.GetNumber() == p.GetNumber():
                p.SetNet(q.GetNet()); p.SetPinFunction(q.GetPinFunction()); p.SetPinType(q.GetPinType())
                break
    board.Add(new); board.Remove(old)
board.Save(str(PCB))

# schematic: point each symbol's Footprint property at the new library (text edit on the balanced
# parenthesised symbol block; do this with the editors closed)
def match_end(s, i):
    d, j, ins = 0, i, False
    while True:
        c = s[j]
        if c == '"' and s[j - 1] != "\\": ins = not ins
        elif not ins:
            if c == "(": d += 1
            elif c == ")":
                d -= 1
                if d == 0: return j + 1
        j += 1

t = SCH.read_text(encoding="utf-8")
for ref, name in MAPPING.items():
    i = t.index(f'(property "Reference" "{ref}"')
    i = t.rfind("\n\t(", 0, i) + 1
    k = t.index("(symbol", i)
    e = match_end(t, k)
    blk = re.sub(r'(\(property "Footprint" ")([^"]*)(")', lambda m: m.group(1) + f"{NICK}:{name}" + m.group(3),
                 t[k:e], count=1)
    t = t[:k] + blk + t[e:]
SCH.write_text(t, encoding="utf-8")
```

Known weak spots of that script (fix when productising): the schematic text edit finds the symbol
block by searching for the reference string and walking back to the preceding line start (a helper
using the same trick once deleted the wrong element elsewhere; always diff against the backup); it does
not handle multi-unit or multi-sheet duplicates; it asserts the footprint is on the front side; it
does not copy the old footprint's keepout zones (intentional here).
