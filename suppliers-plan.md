# Plan: split component supplier from fab

Status: proposal, nothing implemented. Survey done 2026-10-11 from search results
only (this host cannot reach the vendors' sites), so figures marked (unverified)
need a check before they become code or fixtures.

## 1. Survey

### 1.1 Who sources what

| Fab | Assembly parts come from | Part identity in the BOM |
|---|---|---|
| JLCPCB | its own LCSC-backed parts library (Basic / Preferred / Extended) | LCSC number `C123456` |
| PCBWay | Mouser, DigiKey and others, bought by PCBWay (turnkey) or consigned | MPN + manufacturer (unverified: exact column names) |
| Aisler | Würth, TI, Mouser, DigiKey, Farnell, Arrow, LCSC; others by hand | MPN, assigned in their web BOM tool |

So for PCBWay and Aisler the **MPN** is the part identity, and the distributor is
only where price and stock come from. Neither fab has a public parts API.

### 1.2 Distributor (supplier) APIs: price and stock

| Supplier | API | Access | Limits | Footprints via API? |
|---|---|---|---|---|
| LCSC / JLCPCB parts | what kifab uses today (JLCPCB's parts search) | none | unofficial | **yes**: EasyEDA library via `easyeda2kicad` |
| Mouser | Search API: keyword and part number (Mouser no. or MPN, up to 10 per call) | free API key, approval may be pending | 30/min, 1000/day | no (SamacSys models only on web pages) |
| DigiKey | Product Information v4: keyword, part details, pricing by quantity | free OAuth2 client credentials (developer.digikey.com) | per app quota | no (Ultra Librarian on the web, about 15 downloads per month) |
| Farnell / Newark / element14 | Product Search API (REST), one API for all regions | free 24-char key | about 2/s, 1000/day (unverified) | no |
| Arrow | Pricing & Availability API | key through sales contact or websupport@arrow.com | 50 req/s | no |
| Nexar (Octopart) | GraphQL: offers from every distributor in one call | free "Welcome 1K" tier, 1000 calls per month (unverified) | small | only links to the CAD marketplace (UL, SnapMagic) |

### 1.3 Footprint / symbol / 3D sources

| Source | Coverage | Programmatic? | Licence / note |
|---|---|---|---|
| EasyEDA (via `easyeda2kicad`) | everything LCSC lists; LCSC lists a large share of common MPNs | yes, by LCSC number | already used; quality varies |
| KiCad's own libraries | generic packages (0402–1206, SOT-23, SOIC, QFN, …) and many standard symbols | files on disk, ships with KiCad | CC-BY-SA 4.0 with exception |
| Würth Elektronik KiCad library | Würth parts only | yes: GitHub repo WurthElektronik/KiCad-Library, KiCad 7+ | vendor-maintained |
| DigiKey KiCad library | about 1000s of parts | GitHub, but **unmaintained** and pre-KiCad 6 | CC-BY-SA 4.0 |
| SamacSys / Component Search Engine (Mouser, Farnell, Arrow, RS) | very large | **no public API**; web download or "Library Loader" | free with account |
| Ultra Librarian (DigiKey, TI) | very large | **no public API**; web export to KiCad | free with account, quotas |
| SnapMagic (SnapEDA) | very large | API by application only (backlog), no public pricing | account |

**The key finding:** only LCSC/EasyEDA supplies libraries by API. The large
model libraries (SamacSys, Ultra Librarian, SnapMagic) sit behind web logins
with no usable API. The distributors' APIs give price and stock but no
footprints.

So "the supplier contributes the footprint library" holds for LCSC only. Two
things make it workable anyway:

1. **EasyEDA can serve parts bought elsewhere.** Look up the MPN on LCSC to get
   a C-number, then fetch with `easyeda2kicad`. The footprint is the same
   whether the reel comes from LCSC or Mouser.
2. **Manual import covers the rest.** The user downloads a KiCad zip from
   SamacSys, Ultra Librarian or SnapMagic, and `kifab component import <zip> --mpn X`
   vendors and locks it like a fetched part. That is the officially supported
   path for those sites anyway.

Generic passives, which are most of a BOM, can use KiCad's own footprints.

## 2. Target model: three roles, not two

```
Fab            design rules, order files, which suppliers it can buy from,
               what identifies a part in its BOM
Supplier       catalogue: search, lookup (by own SKU and by MPN), stock, price
LibrarySource  symbol + footprint + 3D for a part (by MPN or source-specific id)
```

* **JLCPCB fab** = rules and exports, plus supplier `jlcpcb-parts` (today's
  `JlcpcbCatalog`) and library source `easyeda`. Its BOM key stays the LCSC
  number. Basic/Extended stays a JLCPCB-specific attribute of that supplier's
  hits.
* **PCBWay fab** = rules and exports. It accepts suppliers `mouser` and
  `digikey` (others allowed). Library sources are `easyeda` (by MPN through
  LCSC), `kicad` (generic) and `import`. Its BOM key is MPN + manufacturer.
* **Aisler fab** = same as PCBWay with a wider supplier list: `mouser`,
  `digikey`, `farnell`, `arrow`, `lcsc`. Tried by hand: Aisler's BOM upload
  guesses parts well and needs only manual verification, so a good BOM export
  (MPN, manufacturer, supplier SKUs, value, footprint) is all kifab has to
  provide.

The library source is a separate role, not part of the supplier. A supplier
does not give footprints except for LCSC, and the same footprint serves every
supplier of an MPN. A supplier may still *recommend* a library source
(`jlcpcb-parts` → `easyeda`), which covers the "JLCPCB is both" case without
coupling.

### 2.1 Interfaces (sketch)

```python
# kifab/suppliers/base.py   (entry-point group "kifab.suppliers")
@dataclass(frozen=True)
class PartKey:                 # what identifies a physical part
    mpn: str
    manufacturer: str = ""

@dataclass(frozen=True)
class Offer:                   # was PartHit; id becomes the supplier's SKU
    supplier: str              # "mouser"
    sku: str                   # supplier part no. ("C123456", "595-…")
    key: PartKey
    package: str; description: str; category: str
    stock: int; prices: tuple[PriceBreak, ...]; min_order: int
    datasheet: str; url: str; images: tuple[str, ...]
    attributes: tuple[tuple[str, str], ...]
    fab_class: LibraryType = LibraryType.UNKNOWN   # JLCPCB Basic/Extended

class Supplier(ABC):
    name: str; title: str
    sku_fields: tuple[str, ...]          # schematic fields, e.g. LCSC_FIELDS, ("Mouser",)
    needs_credentials: bool
    def catalog(self, client: HttpClient | None, creds: Credentials | None) -> Catalog: ...
    def preferred_library(self) -> str | None: ...   # "easyeda" for jlcpcb-parts

class Catalog(Protocol):     # today's Catalog, plus lookup by MPN
    def search(...) -> SearchPage: ...
    def lookup(self, sku: str) -> Offer | None: ...
    def lookup_mpn(self, key: PartKey) -> list[Offer]: ...
    def image(self, url: str) -> bytes: ...

# kifab/libraries/base.py   (entry-point group "kifab.libraries")
class LibrarySource(ABC):
    name: str
    def resolve(self, ref: PartRef) -> str | None: ...   # source id or None
    def fetch(self, source_id: str, workdir: Path) -> FetchedPart: ...  # moved from fabs.base

# kifab/fabs/base.py
class Fab(ABC):
    ...rules, exports, bundle_files (unchanged)...
    supported_suppliers: tuple[str, ...]      # () = any
    default_suppliers: tuple[str, ...]        # JLCPCB: ("jlcpcb-parts",)
    default_libraries: tuple[str, ...]        # JLCPCB: ("easyeda",)
    bom_key: BomKey                           # SKU_OF(supplier) | MPN
    # catalog() and part_source() go away; the project assembles them
```

`PartRef` is what a schematic symbol says about its part. It replaces the bare
`part_number: str`:

```python
@dataclass(frozen=True)
class PartRef:
    key: PartKey | None                         # MPN + Manufacturer fields
    skus: tuple[tuple[str, str], ...]           # (supplier, sku) from LCSC/Mouser/... fields
```

`BomLine.part_number` becomes `BomLine.part: PartRef`. Each fab's export picks
what it needs: JLCPCB the LCSC SKU, PCBWay and Aisler the MPN + manufacturer.

### 2.2 Project configuration

```toml
fab = "pcbway"
project = "demo"

[parts]                       # optional; defaults come from the fab
suppliers = ["mouser", "digikey"]   # order = preference for price/stock
libraries = ["easyeda", "kicad", "import"]
library = "parts"             # nickname of lib/<name>/; existing projects keep "jlcpcb"
```

Credentials never go in the project. They come from the environment
(`KIFAB_MOUSER_KEY`, `KIFAB_DIGIKEY_CLIENT_ID`/`_SECRET`, …) or from
`~/.config/kifab/credentials.toml`. CI gets them as secrets, and
`kifab ci init` writes the `env:` lines.

### 2.3 The part model: what ties the three together

Fab, supplier and library source never talk to each other directly. They meet
in one record per part, and a resolver fills in the missing pieces from
whatever the schematic already says:

```python
@dataclass(frozen=True)
class LibraryRef:
    source: str                 # "easyeda", "import", "kicad"
    id: str                     # "C123456", an import hash, "Resistor_SMD:R_0603_1608Metric"

@dataclass(frozen=True)
class Part:                     # one physical part, as kifab knows it
    key: PartKey                # MPN + manufacturer: the identity
    skus: tuple[tuple[str, str], ...]   # ("lcsc", "C123456"), ("mouser", "…")
    library: LibraryRef | None  # where its symbol/footprint/3D came from
```

The resolver (`kifab.core.partdb`) works from what is known:

* SKU → MPN: ask that supplier (`lookup(sku)`).
* MPN → SKUs: ask each configured supplier (`lookup_mpn`).
* MPN or SKU → library: each library source says what it needs. `easyeda`
  needs an LCSC SKU, so it asks the `lcsc`/`jlcpcb-parts` catalogue for one
  when the schematic only has an MPN.

Resolved parts are written to `kifab.lock` (key, SKUs, library ref, file
hashes). `check` and `bundle` in CI then need no network and no API keys. Only
`component check` (price and stock) goes online.

Example: Aisler as fab, LCSC as supplier, EasyEDA for libraries:

```toml
fab = "aisler"
[parts]
suppliers = ["lcsc"]
libraries = ["easyeda"]
```

`component search` queries LCSC. `component add C123456` vendors the EasyEDA
symbol and footprint and writes `LCSC`, `MPN` and `Manufacturer` fields onto
the symbol. Aisler's BOM gets MPN, manufacturer and the LCSC number. Changing
the supplier later to `["mouser"]` changes only where price and stock come
from; the vendored library and the schematic stay as they are.

Naming: "model" already means 3D model in kifab (`lib/<name>/…3dshapes`), so
the role is called *library source* in code.

### 2.4 Lock file

`LockedPart.id`/`fab` become `source` (library source name) + `source_id`, plus
`key` (the MPN). Bump `LOCK_VERSION` to 2. Version 1 reads as
`source="easyeda", source_id=<C-number>`, and the next write upgrades it, so
old projects keep working.

## 3. Feasibility and risks

| Topic | Assessment |
|---|---|
| Refactor of the core | Feasible. Catalogue and part source already sit behind interfaces. About 15 call sites use `fab.catalog()`/`fab.part_source()`/`part_number`. |
| Price and stock for PCBWay/Aisler | Feasible with Mouser (simplest key) and DigiKey (OAuth client credentials). The user must register for keys. Daily quotas mean `component check` must cache and batch (Mouser takes 10 MPNs per call). |
| Footprints for non-LCSC parts | **Partly automatic.** EasyEDA via an LCSC MPN cross-reference covers many parts. The rest go through manual `import`. No way around this without a SnapMagic/SamacSys agreement. |
| CPL rotation | JLCPCB relies on EasyEDA footprints matching its own data. PCBWay and Aisler check placement by hand. Use KiCad's export unchanged and add a per-fab rotation hook only if needed. |
| PCBWay BOM columns | Unconfirmed. Needs PCBWay's sample BOM (you can download it, I cannot) turned into a synthetic fixture. |
| Aisler | Accepts native `.kicad_pcb`, so the bundle could be just the board file. Rules depend on surface finish, which needs `RuleOptions.finish`. |
| Offline tests | Same pattern as today: synthetic JSON fixtures per supplier API under `tests/fixtures/suppliers/<name>/`, served by `KIFAB_OFFLINE`. The response shapes come from public API docs, never from captured answers. |
| Terms of service | Distributor APIs allow this use with the user's own key. The unofficial JLCPCB search and EasyEDA access are unchanged from today. Scraping SamacSys or UL pages is ruled out. |

## 4. Steps (one commit each, each green on `scripts/check.sh`)

1. **Move, don't change.** Create `kifab.suppliers` (move `fabs/jlcpcb/search.py`
   → `suppliers/jlcpcb_parts/`) and `kifab.libraries` (move `fabs/jlcpcb/fetch.py`
   → `libraries/easyeda/`, with `FetchedPart`/`PartSource`). Add both registries
   with entry points. `JlcpcbFab.catalog()` and `.part_source()` delegate to
   them. No behaviour change.
2. **The project assembles roles.** Add `Project.suppliers()` and
   `Project.library_sources()` from `kifab.toml` `[parts]` plus fab defaults.
   Switch the CLI, GUI, `bundle`, `orderability` and `library` to them. Drop
   `catalog()`/`part_source()` from `Fab`. Add `--supplier` to
   `component search`.
3. **Part model.** Add `PartKey`/`PartRef`/`Part`/`LibraryRef` and the
   resolver (2.3). Read `MPN`/`Manufacturer` fields and each supplier's SKU
   fields from the schematic; `component add` writes all three. `BomLine`
   carries `PartRef`. `Fab.check_parts` reports against the fab's `bom_key`.
   Rename `PartHit` → `Offer` (with `supplier`, `sku`). Lock file v2 (key,
   SKUs, library ref) with v1 upgrade.
4. **Availability across suppliers.** `check_availability` takes a list of
   catalogues, looks lines up by SKU or MPN, batches, and reports the best
   offer per line, with a per-supplier result in JSON. Add a credentials loader
   and an issue code for a missing key.
5. **Aisler fab, with LCSC + EasyEDA.** This needs no new supplier API: the
   `lcsc` supplier starts as today's catalogue under a second name (same
   C-numbers, MPNs and EasyEDA libraries; stock is JLCPCB's, flagged as an
   approximation until an LCSC-shop catalogue exists). HASL/ENIG rule
   profiles (`RuleOptions.finish`), BOM/CPL in MPN form with supplier SKU
   columns (their BOM upload matches parts from these), and a bundle with the
   Gerber zip (and optionally the `.kicad_pcb`). First end-to-end result.
6. **Library resolution by MPN.** The `easyeda` source resolves an MPN to a
   C-number through the LCSC catalogue. The new `import` source handles
   `kifab component import <zip|dir> --mpn X [--manufacturer Y]` for SamacSys,
   Ultra Librarian and SnapMagic KiCad exports. Add a `kicad` source that binds
   a generic KiCad footprint to a part, referenced not vendored, and checked
   by `kifab check`.
7. **Mouser supplier.** Keyword and part-number search, synthetic fixtures,
   offline mode.
8. **PCBWay fab.** Standard/advanced rule profiles and BOM in PCBWay's template
   columns, once the template is confirmed.
9. **DigiKey supplier** (OAuth client credentials, token cached in the user
   cache dir).
10. **GUI.** Supplier picker in the Parts tab, offers from several suppliers per
    part, and an import dialog.
11. **Later / optional.** A real LCSC-shop catalogue (own stock and prices),
    Farnell and Arrow suppliers, a Nexar aggregator supplier, the Würth
    library source (git checkout into the user cache), and Aisler "Push for
    CLI" upload.

Steps 1–4 are pure restructuring and keep JLCPCB working throughout. Step 5 is
the first visible result (Aisler + LCSC + EasyEDA). Each later supplier or
library source is a small, independent plugin.

## 5. Needs from you

* Mouser API key (and DigiKey client id/secret later) for a manual end-to-end
  test on your machine. Tests stay offline.
* PCBWay's sample BOM / centroid template and Aisler's current design-rules
  page values, to turn into synthetic fixtures and rule profiles.
* A decision on library nickname for new projects: keep `lib/<fab>/`, or a
  neutral `lib/parts/`? Existing projects keep theirs either way.
