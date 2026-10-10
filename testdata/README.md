# testdata/

Raw online responses captured 2026-10-10 by `scripts/fetch_fixtures.py`, for
developing and testing the JLCPCB fab backend **offline** (the build host cannot
reach EasyEDA/JLCPCB). Parts: the 10 LCSC numbers of `reference/example-project`.

| Path                                  | Source / meaning |
|---------------------------------------|------------------|
| `easyeda/components/<LCSC>.json`      | `GET easyeda.com/api/products/<LCSC>/components`: the EasyEDA symbol + footprint data easyeda2kicad converts (`result.dataStr`, `packageDetail`, 3D model uuid, ...) |
| `easyeda/svgs/<LCSC>.json`            | `GET .../svgs`: pre-rendered symbol/footprint SVGs |
| `easyeda/searchByNumbers.json`        | `POST easyeda.com/api/components/searchByNumbers` (form `numbers=[...]`): LCSC number -> component uuids |
| `easyeda/search/*.json`               | EasyEDA's legacy keyword search: returns **empty lists for everything** now (kept as proof; do not use) |
| `jlcpcb_search/by_id/<LCSC>.json`     | `POST jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList` with `{"keyword": "<LCSC>", "currentPage": 1, "pageSize": 10}`. Works anonymously. Has stock, prices, Basic (`base`) vs Extended (`expand`), package, datasheet, attributes: `data.componentPageInfo.list[]` |
| `jlcpcb_search/keyword/*.json`        | same endpoint with text queries; `_base` = with `componentLibraryType: "base"` (Basic only). `STM32F103_base`, `xyzzy_no_such_part` show the empty case (`list` is `null`) |
| `easyeda2kicad_output/<LCSC>/`        | What easyeda2kicad 1.0.1 writes with `--full --overwrite`: `jlcpcb.kicad_sym`, `jlcpcb.pretty/*.kicad_mod`, `jlcpcb.3dshapes/*.wrl` (`.step` only kept for C1647, for size), plus the console log |
| `jlcparts_db_sample.json`             | Rows from a local sqlite parts DB (community jlcparts style, 110k parts): the table schema, the BOM parts it contains (6 of 10), an FTS query and a sample. Alternative offline search/stock backend |

Search backend findings (for plan increment 5): the **JLCPCB search endpoint is
the best online option**: anonymous, keyword or LCSC-number query, paging
(`currentPage`, `pageSize`), Basic filter, returns stock and price live. EasyEDA
is needed only to fetch symbol/footprint/3D for a chosen LCSC number
(`/api/products/<LCSC>/components` and the 3D endpoints). The endpoints are
unofficial and may change; keep the parsers tolerant and covered by these fixtures.

Regenerate (needs network): `uv run --with easyeda2kicad python scripts/fetch_fixtures.py`.
