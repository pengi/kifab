# tests/fixtures/

Synthetic test data, **all invented** for kifab's tests: part numbers
(`C9000xx`), manufacturer ("Example Components"), MPNs (`KF-...`), footprints,
3D model placeholders, the `demo-board` KiCad project and every catalogue answer.
Nothing is copied from a fab, a parts catalogue, KiCad's libraries or a real
board. Only the *formats* follow the real ones (KiCad 10 files, kicad-cli 10.0.6
output, JLCPCB's parts-search answer, easyeda2kicad 1.0.1 output).

Everything here is written by `generate.py`; regenerate with

    uv run python -m tests.fixtures.generate

`tests/test_fixtures.py` fails when the committed files differ from it.

| Path | What |
|------|------|
| `demo-board/` | KiCad 10 project: sub-sheet, DNP part, not-in-BOM part, through-hole header, bottom-side part, a stock-library footprint and symbol, missing 3D models, a pre-kifab `.kicad_dru` |
| `expected/` | the JLCPCB BOM and CPL the bundle must produce for `demo-board` (written by hand) |
| `kicad_cli/` | kicad-cli output for `demo-board` in kicad-cli 10.0.6's exact layout (DRC JSON, BOM and position CSV, gerber file names of a 4-layer board) |
| `jlcpcb_search/` | answers of JLCPCB's parts search for the fake parts: `by_id/<id>.json`, `keyword/<query>[_base].json` |
| `easyeda2kicad_output/<id>/` | what easyeda2kicad writes for a part: symbol library (20211014 syntax), legacy `module` footprint with an absolute 3D path, placeholder model, log |

`tests/test_real_kicad.py` checks with a real kicad-cli 10 that the demo board
loads and that kicad-cli's exports equal the samples in `kicad_cli/`.
`KIFAB_OFFLINE=tests/fixtures` makes kifab answer catalogue searches and part
fetches from this directory (for trying the CLI/GUI by hand).
