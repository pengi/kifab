# reference/

Read-only material copied from other local projects (snapshot 2026-10-10), so
the work can continue on a host without access to the original machine. Do not
edit; port ideas and code into `src/kifab/` (typed, per `CLAUDE.md`).

| Path                         | What                                                         |
|------------------------------|--------------------------------------------------------------|
| `pcbfab.md`                  | Hand-over notes: JLCPCB rules, KiCad/easyeda2kicad gotchas, ideas |
| `populate_jlc_library/`      | The existing untyped tools (`populate-jlc-library`, `jlcpcb-fab`) with 30 offline tests and stubs. Source: `wrink-hw/populate_jlc_library` |
| `example-project/`           | A real KiCad 10 project (`rpi-nrf52840-hat`) ordered from JLCPCB |

`example-project/rpi-nrf52840-hat/` contains the KiCad sources (`.kicad_pro/_sch/_pcb/_dru`),
`fp-lib-table`, its own footprint library, `jlcpcb-lib/` as produced by
populate-jlc-library (footprints and `lcsc_footprints.csv`; 3D models left out
for size), `generate_gerber.sh` (the old bash flow), and `gerber/` = the
**expected output** of a bundle for this board (gerbers, drill, job file, zip,
`*_bom_jlcpcb.csv`, `*_cpl_jlcpcb.csv`). Use it as the golden example for
`kifab bundle` and as a schematic/board input for parser tests.

Paths mentioned inside the copied documents (e.g. `/home/pengi/src/wrink/...`)
refer to the original machine and no longer exist here.
