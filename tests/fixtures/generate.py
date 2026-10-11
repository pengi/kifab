"""Generate the synthetic test fixtures in ``tests/fixtures/`` (all data invented).

    uv run python -m tests.fixtures.generate

Nothing here is captured from a third party: part numbers (``C9000xx``),
manufacturers, MPNs, footprints, the demo board and the catalogue answers are
made up. Only the *formats* follow the real ones (KiCad 10 files, kicad-cli
10 output, JLCPCB's search answer, easyeda2kicad 1.0.1 output), as checked
against the real tools on 2026-10-10; ``tests/test_real_kicad.py`` re-checks
the KiCad side whenever a real kicad-cli is available.

``tests/test_fixtures.py`` fails if the committed files differ from what this
script generates.
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kifab.kicad import sexpr

HERE = Path(__file__).resolve().parent
PROJECT = "demo-board"
NS = uuid.UUID("6f1c2b1e-0000-4000-8000-00000000f1a5")
S = sexpr.Sym


def uid(name: str) -> str:
    return str(uuid.uuid5(NS, name))


# ---------------------------------------------------------------- the parts


@dataclass(frozen=True)
class FakePart:
    id: str
    mpn: str
    value: str
    package: str
    footprint: str  # footprint name in the fab library
    kind: str  # base / expand
    stock: int
    price: float
    category: str
    preferred: bool = False
    through_hole: bool = False
    attr_bug: bool = False  # easyeda2kicad writes (attr through_hole) on an SMD part
    fp_descr: str = ""
    rated: str = ""  # the value as the catalogue's Resistance/Capacitance/... shows it


PARTS = (
    FakePart("C900001", "KF-R330-0603", "330R", "0603", "R0603", "base", 500000, 0.0021, "Chip Resistor", fp_descr="330R 1%", rated="330Ω"),
    FakePart("C900002", "KF-R4K7-0603", "4k7", "0603", "R0603", "base", 800000, 0.0023, "Chip Resistor", fp_descr="4k7 1%", rated="4.7kΩ"),
    FakePart("C900003", "KF-C100N-0603", "100nF", "0603", "C0603", "base", 1000000, 0.0031, "Capacitor", rated="100nF"),
    FakePart("C900004", "KF-C4U7-0805", "4.7uF", "0805", "C0805", "expand", 50000, 0.0102, "Capacitor", attr_bug=True, rated="4.7uF"),
    FakePart("C900005", "KF-MOD-1", "KF-MOD-1", "SMD,18x13mm", "MOD-SMD_KF1", "expand", 1200, 6.5, "Radio Module"),
    FakePart("C900006", "KF-HDR-2X4", "hdr", "2x4 2.54mm", "HDR-TH_8P", "expand", 100, 0.12, "Pin Header", through_hole=True),
    FakePart("C900007", "KF-C100N-0603-X", "100nF", "0603", "C0603", "expand", 0, 0.0055, "Capacitor", rated="100nF"),
    FakePart("C900008", "KF-LED-0603", "LED", "0603", "LED0603", "expand", 70000, 0.011, "LED", preferred=True),
    # passives for the quick search (R 4k7, C 18p, L 10u)
    FakePart("C900009", "KF-R5K1-0603", "5k1", "0603", "R0603", "base", 300000, 0.0022, "Chip Resistor", rated="5.1kΩ"),
    FakePart("C900010", "KF-R4K3-0603", "4k3", "0603", "R0603", "expand", 20000, 0.0035, "Chip Resistor", rated="4.3kΩ"),
    FakePart("C900011", "KF-R4K7-0603-B", "4k7", "0603", "R0603", "expand", 0, 0.004, "Chip Resistor", rated="4.7kΩ"),
    FakePart("C900012", "KF-C18P-0402", "18pF", "0402", "C0402", "base", 400000, 0.0018, "Capacitor", rated="18pF"),
    FakePart("C900013", "KF-L10U-0805", "10uH", "0805", "L0805", "expand", 9000, 0.03, "Inductor", rated="10uH"),
    FakePart("C900014", "KF-NTC-4K7-0603", "NTC 4k7", "0603", "R0603", "expand", 15000, 0.02, "NTC Thermistor", rated="4.7kΩ"),
)  # fmt: skip
BY_ID = {p.id: p for p in PARTS}
#: parts whose easyeda2kicad output is captured (C900007/8 are catalogue-only)
FETCHABLE = ("C900001", "C900002", "C900003", "C900004", "C900005", "C900006")
#: parts the catalogue shows a product image for (served from jlcpcb_search/images)
WITH_IMAGE = FETCHABLE + ("C900008",)
IMAGE_URL = "https://example.invalid/images/{id}.png"


# ---------------------------------------------------------------- catalogue


RATED_ATTRIBUTE = {
    "Chip Resistor": "Resistance",
    "NTC Thermistor": "Resistance",
    "Capacitor": "Capacitance",
    "Inductor": "Inductance",
}


def catalog_item(p: FakePart) -> dict[str, object]:
    attributes = [
        {"attribute_name_en": "Value", "attribute_value_name": p.value},
        {"attribute_name_en": "Package", "attribute_value_name": p.package},
    ]
    if p.rated:
        attributes.append(
            {
                "attribute_name_en": RATED_ATTRIBUTE[p.category],
                "attribute_value_name": p.rated,
            }
        )
    return {
        "attributes": attributes,
        "componentBrandEn": "Example Components",
        "componentCode": p.id,
        "componentLibraryType": p.kind,
        "componentModelEn": p.mpn,
        "componentPrices": [
            {"endNumber": 999, "productPrice": p.price, "startNumber": 1},
            {"endNumber": 9999, "productPrice": round(p.price * 0.8, 6), "startNumber": 1000},
            {"endNumber": -1, "productPrice": round(p.price * 0.6, 6), "startNumber": 10000},
        ],
        "componentSpecificationEn": p.package,
        "componentTypeEn": p.category,
        "dataManualUrl": f"https://example.invalid/datasheet/{p.id}.pdf",
        "describe": f"{p.value} {p.package} {p.category} (synthetic test part)",
        "firstSortName": p.category,
        "lcscGoodsUrl": f"https://example.invalid/part/{p.id}",
        "minPurchaseNum": 1,
        "preferredComponentFlag": p.preferred,
        "stockCount": p.stock,
    } | (
        {"imageList": [{"productBigImage": IMAGE_URL.format(id=p.id)}]}
        if p.id in WITH_IMAGE
        else {}
    )  # fmt: skip


# ---------------------------------------------------------------- product images

RGB = tuple[int, int, int]
WHITE: RGB = (250, 250, 250)
METAL: RGB = (190, 190, 196)
BODY: dict[str, RGB] = {
    "Chip Resistor": (35, 35, 38),
    "Capacitor": (176, 140, 92),
    "Radio Module": (40, 110, 60),
    "Pin Header": (30, 30, 30),
    "LED": (235, 235, 200),
}


def png(width: int, height: int, pixel: Callable[[int, int], RGB]) -> bytes:
    """A tiny deterministic RGB PNG."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    rows = b"".join(
        b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width))
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def part_image(p: FakePart) -> bytes:
    """A made-up product photo: the part's body and terminals as rectangles."""
    w, h = 120, 80
    body = BODY.get(p.category, (90, 90, 90))

    def pixel(x: int, y: int) -> RGB:
        if p.category == "Pin Header":
            if 20 <= x < 100 and 30 <= y < 50:
                return body
            if (x - 30) % 20 < 4 and 10 <= y < 70 and 26 <= x < 98:
                return (200, 170, 60)  # gold pins
            return WHITE
        if p.category == "Radio Module":
            if 15 <= x < 105 and 10 <= y < 70:
                inner = 35 <= x < 85 and 20 <= y < 60
                return METAL if inner else body
            return WHITE
        if 25 <= x < 95 and 25 <= y < 55:
            return METAL if x < 37 or x >= 83 else body
        return WHITE

    return png(w, h, pixel)


def write_images(root: Path) -> None:
    for part_id in WITH_IMAGE:
        path = root / "jlcpcb_search" / "images" / f"{part_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(part_image(BY_ID[part_id]))


def answer(parts: list[FakePart], total: int | None = None) -> dict[str, object]:
    return {
        "code": 200,
        "data": {
            "componentPageInfo": {
                "list": [catalog_item(p) for p in parts] or None,
                "pageNum": 1,
                "pageSize": 10,
                "total": len(parts) if total is None else total,
            }
        },
        "message": None,
    }


KEYWORDS = {
    # slug: (parts, total)
    "330R_0603": (["C900001", "C900002"], None),
    "330R_0603_base": (["C900001"], None),
    "100nF_0603": (["C900007", "C900003"], 1534),  # more pages exist
    "100nF_0603_p2": (["C900012", "C900004"], 1534),  # page 2 (invented filler)
    "100nF_0603_base": (["C900003"], None),
    "KF_MOD_1": (["C900005"], None),
    "KF_MOD_1_base": ([], None),
    "xyzzy_no_such_part": ([], None),
    "xyzzy_no_such_part_base": ([], None),
    # quick search (kifab.core.quick): "<value><unit> [extra terms]"
    "4_7k__0603": (["C900002", "C900011", "C900014"], None),
    "4_7k__0603_base": (["C900002"], None),
    "4_3k__0603": (["C900010"], None),
    "4_3k__0603_base": ([], None),
    "5_1k__0603": (["C900009"], None),
    "5_1k__0603_base": (["C900009"], None),
    "4_7k_": (["C900002", "C900011", "C900014"], None),
    "18pF": (["C900012"], None),
    "10uH": (["C900013"], None),
}


def write_catalog(root: Path) -> None:
    for p in PARTS:
        hits = [p]
        if p.id == "C900003":  # a part-number query can return similar parts too
            hits = [BY_ID["C900007"], p]
        dump_json(root / "jlcpcb_search" / "by_id" / f"{p.id}.json", answer(hits))
    for slug, (ids, total) in KEYWORDS.items():
        parts = [BY_ID[i] for i in ids]
        dump_json(
            root / "jlcpcb_search" / "keyword" / f"{slug}.json", answer(parts, total)
        )


# ---------------------------------------------------------------- easyeda2kicad


E2K_SYMBOL = """\
  (symbol "{mpn}"
    (in_bom yes)
    (on_board yes)
    (property
      "Reference"
      "{ref}"
      (id 0)
      (at 0 5.08 0)
      (effects (font (size 1.27 1.27) ) )
    )
    (property
      "Value"
      "{mpn}"
      (id 1)
      (at 0 -5.08 0)
      (effects (font (size 1.27 1.27) ) )
    )
    (property
      "Footprint"
      "jlcpcb:{footprint}"
      (id 2)
      (at 0 -7.62 0)
      (effects (font (size 1.27 1.27) ) hide)
    )
    (property
      "Datasheet"
      "https://example.invalid/datasheet/{id}.pdf"
      (id 3)
      (at 0 -10.16 0)
      (effects (font (size 1.27 1.27) ) hide)
    )
    (property
      "LCSC Part"
      "{id}"
      (id 5)
      (at 0 -12.7 0)
      (effects (font (size 1.27 1.27) ) hide)
    )
    (symbol "{mpn}_0_1"
      (rectangle
        (start -2.54 2.54)
        (end 2.54 -2.54)
        (stroke (width 0) (type default) (color 0 0 0 0))
        (fill (type background))
      )
      (pin unspecified line
        (at -5.08 0 0)
        (length 2.54)
        (name "{name1}" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin unspecified line
        (at 5.08 0 180)
        (length 2.54)
        (name "{name2}" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
    )
  )
"""


def e2k_symbol_lib(p: FakePart) -> str:
    ref = {"Chip Resistor": "R", "Capacitor": "C", "LED": "D", "Pin Header": "J"}.get(
        p.category, "U"
    )
    # simple parts: pins named like their numbers, as EasyEDA does
    names = ("VCC", "GND") if p.category == "Radio Module" else ("1", "2")
    body = E2K_SYMBOL.format(
        mpn=p.mpn,
        ref=ref,
        footprint=p.footprint,
        id=p.id,
        name1=names[0],
        name2=names[1],
    )
    return (
        "(kicad_symbol_lib\n  (version 20211014)\n"
        "  (generator https://github.com/uPesy/easyeda2kicad.py)\n\n" + body + ")\n"
    )


def e2k_footprint(p: FakePart, capture_dir: str) -> str:
    attr = "through_hole" if (p.through_hole or p.attr_bug) else "smd"
    lines = [f"(module easyeda2kicad:{p.footprint} (layer F.Cu) (tedit 5DC5F6A4)"]
    if p.fp_descr:
        lines.append(f'\t(descr "{p.fp_descr}")')
    lines += [
        f"\t(attr {attr})",
        "\t(fp_text reference REF** (at 0 -4) (layer F.SilkS)",
        "\t\t(effects (font (size 1 1) (thickness 0.15)))",
        "\t)",
        f"\t(fp_text value {p.footprint} (at 0 4) (layer F.Fab)",
        "\t\t(effects (font (size 1 1) (thickness 0.15)))",
        "\t)",
    ]
    lines += [f"\t{line}" for line in e2k_silk(p)]
    if p.through_hole:
        for i in range(8):
            x, y = (i // 2) * 2.54, (i % 2) * 2.54
            shape = "rect" if i == 0 else "circle"
            lines.append(
                f"\t(pad {i + 1} thru_hole {shape} (at {x:g} {y:g} 0) (size 1.7 1.7) "
                "(drill 1) (layers *.Cu *.Mask))"
            )
    else:
        for i, x in ((1, -0.8), (2, 0.8)):
            lines.append(
                f"\t(pad {i} smd rect (at {x:g} 0 0) (size 0.9 0.95) "
                "(layers F.Cu F.Paste F.Mask))"
            )
    lines += [
        f'\t(model "{capture_dir}/jlcpcb.3dshapes/{p.footprint}.wrl"',
        "\t\t(offset (xyz 0 0 0))",
        "\t\t(scale (xyz 1 1 1))",
        "\t\t(rotate (xyz 0 0 0))",
        "\t)",
        ")",
    ]
    return "\n".join(lines) + "\n"


def e2k_silk(p: FakePart) -> list[str]:
    """Silkscreen in easyeda2kicad's legacy syntax (lines, a circle, an arc)."""
    w = "(layer F.SilkS) (width 0.15)"
    if p.through_hole:  # 2x4 header: outline and a pin-1 mark
        x0, y0, x1, y1 = -1.27, -1.27, 8.89, 3.81
        return [
            f"(fp_line (start {x0:g} {y0:g}) (end {x1:g} {y0:g}) {w})",
            f"(fp_line (start {x1:g} {y0:g}) (end {x1:g} {y1:g}) {w})",
            f"(fp_line (start {x1:g} {y1:g}) (end {x0:g} {y1:g}) {w})",
            f"(fp_line (start {x0:g} {y1:g}) (end {x0:g} {y0:g}) {w})",
            f"(fp_circle (center -2 -2) (end -1.8 -2) {w})",
        ]
    if p.category == "Radio Module":  # an antenna arc over the body outline
        return [
            f"(fp_line (start -2 -1.5) (end 2 -1.5) {w})",
            f"(fp_line (start 2 -1.5) (end 2 1.5) {w})",
            f"(fp_line (start 2 1.5) (end -2 1.5) {w})",
            f"(fp_line (start -2 1.5) (end -2 -1.5) {w})",
            f"(fp_arc (start 0 -1.5) (end -1 -1.5) (angle 180) {w})",
        ]
    return [  # two-terminal chip: lines above and below the body
        f"(fp_line (start -0.3 -0.7) (end 0.3 -0.7) {w})",
        f"(fp_line (start -0.3 0.7) (end 0.3 0.7) {w})",
    ]


def write_e2k(root: Path) -> None:
    for part_id in FETCHABLE:
        p = BY_ID[part_id]
        d = root / "easyeda2kicad_output" / part_id
        capture = (
            f"/tmp/easyeda2kicad-capture/{part_id}"  # absolute, as the tool writes
        )
        write(d / "jlcpcb.kicad_sym", e2k_symbol_lib(p))
        write(
            d / "jlcpcb.pretty" / f"{p.footprint}.kicad_mod", e2k_footprint(p, capture)
        )
        write(
            d / "jlcpcb.3dshapes" / f"{p.footprint}.wrl",
            f"#VRML V2.0 utf8\n# synthetic 3D model for {p.footprint} (test data)\n",
        )
        write(
            d / "easyeda2kicad.log",
            "-- easyeda2kicad.py v1.0.1 --\n"
            f"[INFO] Created Kicad symbol for ID : {part_id}\n"
            f"       Symbol name : {p.mpn}\n"
            f"[INFO] Created Kicad footprint for ID: {part_id}\n"
            f"       Footprint name: {p.footprint}\n",
        )


# ---------------------------------------------------------------- demo project


def effects(size: str = "1.27", hide: bool = False) -> sexpr.Node:
    node: sexpr.Node = [S("effects"), [S("font"), [S("size"), S(size), S(size)]]]
    return node


def prop(name: str, value: str, at: str, hide: bool = False) -> sexpr.Node:
    x, y = at.split()
    node: sexpr.Node = [S("property"), name, value, [S("at"), S(x), S(y), S("0")]]
    if hide:
        node.append([S("hide"), S("yes")])
    node.append(effects())
    return node


def lib_symbol(lib_id: str, ref: str, power: bool = False) -> sexpr.Node:
    name = lib_id.split(":")[1]
    node: sexpr.Node = [S("symbol"), lib_id]
    if power:
        node.append([S("power"), S("global")])
    node += [
        [S("exclude_from_sim"), S("no")],
        [S("in_bom"), S("yes")],
        [S("on_board"), S("yes")],
        prop("Reference", ref, "0 2.54"),
        prop("Value", name, "0 -2.54"),
        prop("Footprint", "", "0 0", hide=True),
        prop("Datasheet", "", "0 0", hide=True),
        [
            S("symbol"),
            f"{name}_1_1",
            [
                S("pin"),
                S("passive"),
                S("line"),
                [S("at"), S("0"), S("0"), S("90")],
                [S("length"), S("1.27")],
                [S("name"), "", effects()],
                [S("number"), "1", effects()],
            ],
        ],
        [S("embedded_fonts"), S("no")],
    ]
    return node


@dataclass(frozen=True)
class Placed:
    ref: str
    lib_id: str
    value: str
    footprint: str
    lcsc: str = ""
    dnp: bool = False
    in_bom: bool = True
    sheet: str = "root"  # root or power


PLACED = (
    Placed("#PWR01", "power:GND", "GND", ""),
    Placed("R1", "Device:R", "330R", "jlcpcb:R0603", "C900001"),
    Placed("R2", "Device:R", "330R", "jlcpcb:R0603", "C900001"),
    Placed("R3", "Device:R", "4k7", "jlcpcb:R0603", "C900002", dnp=True),
    Placed("R4", "Device:R", "4k7", "jlcpcb:R0603", "C900002"),
    Placed("C1", "Device:C", "100nF", "jlcpcb:C0603", "C900003"),
    Placed("C2", "Device:C", "4.7uF", "jlcpcb:C0805", "C900004"),
    Placed("U1", "Device:U", "KF-MOD-1", "jlcpcb:MOD-SMD_KF1", "C900005"),
    Placed("D1", "Device:LED", "LED", "demo-board:LED_0603", "C900008"),
    Placed("J1", "Device:Conn", "hdr", "Connector_PinHeader_2.54mm:PinHeader_2x04", "C900006"),
    Placed("TP1", "Device:TP", "TP", "Connector_PinHeader_2.54mm:PinHeader_1x01", in_bom=False),
    Placed("#PWR02", "power:GND", "GND", "", sheet="power"),
    Placed("C3", "Device:C", "100nF", "jlcpcb:C0603", "C900003", sheet="power"),
)  # fmt: skip

ROOT_UUID = uid("sheet:root")
SUB_UUID = uid("sheet:power")


def placed_symbol(p: Placed, i: int) -> sexpr.Node:
    path = f"/{ROOT_UUID}" if p.sheet == "root" else f"/{ROOT_UUID}/{SUB_UUID}"
    x, y = 50 + 10 * (i % 8), 50 + 20 * (i // 8)
    props = [
        prop("Reference", p.ref, f"{x} {y - 2}", hide=p.ref.startswith("#")),
        prop("Value", p.value, f"{x} {y + 2}"),
        prop("Footprint", p.footprint, f"{x} {y}", hide=True),
        prop("Datasheet", "", f"{x} {y}", hide=True),
    ]
    if p.lcsc:
        props.append(prop("LCSC", p.lcsc, f"{x} {y}", hide=True))
    return [
        S("symbol"),
        [S("lib_id"), p.lib_id],
        [S("at"), S(str(x)), S(str(y)), S("0")],
        [S("unit"), S("1")],
        [S("exclude_from_sim"), S("no")],
        [S("in_bom"), S("yes" if p.in_bom else "no")],
        [S("on_board"), S("yes")],
        [S("dnp"), S("yes" if p.dnp else "no")],
        [S("uuid"), uid(f"symbol:{p.ref}")],
        *props,
        [S("pin"), "1", [S("uuid"), uid(f"pin:{p.ref}")]],
        [
            S("instances"),
            [
                S("project"),
                PROJECT,
                [S("path"), path, [S("reference"), p.ref], [S("unit"), S("1")]],
            ],
        ],
    ]


def schematic(sheet: str) -> str:
    libs: sexpr.Node = [S("lib_symbols")]
    used = sorted({p.lib_id for p in PLACED if p.sheet == sheet})
    for lib_id in used:
        ref = {"power:GND": "#PWR", "Device:R": "R", "Device:C": "C"}.get(lib_id, "U")
        libs.append(lib_symbol(lib_id, ref, power=lib_id.startswith("power:")))
    node: sexpr.Node = [
        S("kicad_sch"),
        [S("version"), S("20260306")],
        [S("generator"), "eeschema"],
        [S("generator_version"), "10.0"],
        [S("uuid"), ROOT_UUID if sheet == "root" else uid("file:power")],
        [S("paper"), "A4"],
        [S("title_block"), [S("title"), "Demo board (synthetic test data)"]],
        libs,
    ]
    for i, p in enumerate(PLACED):
        if p.sheet == sheet:
            node.append(placed_symbol(p, i))
    if sheet == "root":
        node.append(
            [
                S("sheet"),
                [S("at"), S("150"), S("50")],
                [S("size"), S("20"), S("10")],
                [S("uuid"), SUB_UUID],
                prop("Sheetname", "power", "150 49"),
                prop("Sheetfile", "power.kicad_sch", "150 61"),
                [
                    S("instances"),
                    [
                        S("project"),
                        PROJECT,
                        [S("path"), f"/{ROOT_UUID}", [S("page"), "2"]],
                    ],
                ],
            ]
        )
        node.append([S("sheet_instances"), [S("path"), "/", [S("page"), "1"]]])
    node.append([S("embedded_fonts"), S("no")])
    return sexpr.dumps(node)


@dataclass(frozen=True)
class OnBoard:
    ref: str
    lib_id: str
    x: float
    y: float
    rot: float
    attrs: str = "smd"
    side: str = "F"
    model: str | None = "jlcpcb"  # jlcpcb, stock, or None


ON_BOARD = (
    OnBoard("R1", "jlcpcb:R0603", 110, 60, 0),
    OnBoard("R2", "jlcpcb:R0603", 112.5, 60, 90),
    OnBoard("R3", "jlcpcb:R0603", 115, 60, 0, attrs="smd dnp"),
    OnBoard("R4", "jlcpcb:R0603", 117.5, 65.25, 180, side="B"),
    OnBoard("C1", "jlcpcb:C0603", 120, 60, -90),
    OnBoard("C2", "jlcpcb:C0805", 122.5, 62.75, 270),
    OnBoard("C3", "jlcpcb:C0603", 125, 60, 450),
    OnBoard("U1", "jlcpcb:MOD-SMD_KF1", 130, 70, 0),
    OnBoard("D1", "demo-board:LED_0603", 140, 60, 0, model=None),
    OnBoard("J1", "Connector_PinHeader_2.54mm:PinHeader_2x04", 105, 80, 90, attrs="through_hole", model="stock"),
    OnBoard("TP1", "Connector_PinHeader_2.54mm:PinHeader_1x01", 150, 80, 0, attrs="through_hole exclude_from_bom", model="stock"),
    OnBoard("MH1", "MountingHole:MountingHole_2.7mm", 103, 53, 0, attrs="board_only exclude_from_pos_files exclude_from_bom", model=None),
)  # fmt: skip
FOOTPRINT_MODELS = {
    "R0603": "R0603.wrl",
    "C0603": "C0603.wrl",
    "C0805": "C0805.wrl",
    "MOD-SMD_KF1": "MOD-SMD_KF1.wrl",
}


def pads(f: OnBoard) -> list[sexpr.Node]:
    layer = "B" if f.side == "B" else "F"
    if "board_only" in f.attrs:
        return [
            [
                S("pad"),
                "",
                S("np_thru_hole"),
                S("circle"),
                [S("at"), S("0"), S("0")],
                [S("size"), S("2.7"), S("2.7")],
                [S("drill"), S("2.7")],
                [S("layers"), "*.Cu", "*.Mask"],
                [S("uuid"), uid(f"pad:{f.ref}:h")],
            ]
        ]
    count = 8 if f.lib_id.endswith("2x04") else (1 if f.lib_id.endswith("1x01") else 2)
    out: list[sexpr.Node] = []
    for i in range(count):
        if "through_hole" in f.attrs:
            out.append(
                [
                    S("pad"),
                    str(i + 1),
                    S("thru_hole"),
                    S("rect" if i == 0 else "circle"),
                    [
                        S("at"),
                        sexpr.number((i // 2) * 2.54),
                        sexpr.number((i % 2) * 2.54),
                    ],
                    [S("size"), S("1.7"), S("1.7")],
                    [S("drill"), S("1")],
                    [S("layers"), "*.Cu", "*.Mask"],
                    [S("uuid"), uid(f"pad:{f.ref}:{i}")],
                ]
            )
        else:
            out.append(
                [
                    S("pad"),
                    str(i + 1),
                    S("smd"),
                    S("rect"),
                    [S("at"), S("-0.8" if i == 0 else "0.8"), S("0")],
                    [S("size"), S("0.9"), S("0.95")],
                    [S("layers"), f"{layer}.Cu", f"{layer}.Paste", f"{layer}.Mask"],
                    [S("uuid"), uid(f"pad:{f.ref}:{i}")],
                ]
            )
    return out


def board_footprint(f: OnBoard) -> sexpr.Node:
    placed = next((p for p in PLACED if p.ref == f.ref), None)
    layer = "B" if f.side == "B" else "F"
    silk = f"{layer}.SilkS"
    node: sexpr.Node = [
        S("footprint"),
        f.lib_id,
        [S("layer"), f"{layer}.Cu"],
        [S("uuid"), uid(f"fp:{f.ref}")],
        [S("at"), sexpr.number(f.x), sexpr.number(f.y), sexpr.number(f.rot)],
    ]
    node += [
        [
            S("property"),
            "Reference",
            f.ref,
            [S("at"), S("0"), S("-1.5"), S("0")],
            [S("layer"), silk],
            [S("uuid"), uid(f"fpref:{f.ref}")],
            [
                S("effects"),
                [S("font"), [S("size"), S("1"), S("1")], [S("thickness"), S("0.15")]],
            ],
        ],
        [
            S("property"),
            "Value",
            placed.value if placed else "MountingHole",
            [S("at"), S("0"), S("1.5"), S("0")],
            [S("layer"), f"{layer}.Fab"],
            [S("uuid"), uid(f"fpval:{f.ref}")],
            [
                S("effects"),
                [S("font"), [S("size"), S("1"), S("1")], [S("thickness"), S("0.15")]],
            ],
        ],
    ]
    if placed and placed.lcsc:
        node.append(
            [
                S("property"),
                "LCSC",
                placed.lcsc,
                [S("at"), S("0"), S("0"), S("0")],
                [S("layer"), f"{layer}.Fab"],
                [S("hide"), S("yes")],
                [S("uuid"), uid(f"fplcsc:{f.ref}")],
                [S("effects"), [S("font"), [S("size"), S("1"), S("1")]]],
            ]
        )
    if placed:
        sheetfile = (
            "power.kicad_sch" if placed.sheet == "power" else f"{PROJECT}.kicad_sch"
        )
        path = f"/{uid('symbol:' + f.ref)}"
        if placed.sheet == "power":
            path = f"/{SUB_UUID}{path}"
        node += [
            [S("path"), path],
            [S("sheetname"), "/power/" if placed.sheet == "power" else "/"],
            [S("sheetfile"), sheetfile],
        ]
    node.append([S("attr"), *(S(a) for a in f.attrs.split())])
    node += pads(f)
    name = f.lib_id.split(":")[1]
    if f.model == "jlcpcb":
        model = f"${{KIPRJMOD}}/jlcpcb-lib/jlcpcb.3dshapes/{FOOTPRINT_MODELS[name]}"
    elif f.model == "stock":
        model = (
            f"${{KICAD10_3DMODEL_DIR}}/Connector_PinHeader_2.54mm.3dshapes/{name}.step"
        )
    else:
        model = None
    if model:
        node.append(
            [
                S("model"),
                model,
                [S("offset"), [S("xyz"), S("0"), S("0"), S("0")]],
                [S("scale"), [S("xyz"), S("1"), S("1"), S("1")]],
                [S("rotate"), [S("xyz"), S("0"), S("0"), S("0")]],
            ]
        )
    return node


LAYERS = (
    ("0", "F.Cu", "signal", None),
    ("2", "B.Cu", "signal", None),
    ("9", "F.Adhes", "user", "F.Adhesive"),
    ("11", "B.Adhes", "user", "B.Adhesive"),
    ("13", "F.Paste", "user", None),
    ("15", "B.Paste", "user", None),
    ("5", "F.SilkS", "user", "F.Silkscreen"),
    ("7", "B.SilkS", "user", "B.Silkscreen"),
    ("1", "F.Mask", "user", None),
    ("3", "B.Mask", "user", None),
    ("25", "Edge.Cuts", "user", None),
    ("31", "F.CrtYd", "user", "F.Courtyard"),
    ("29", "B.CrtYd", "user", "B.Courtyard"),
    ("35", "F.Fab", "user", None),
    ("33", "B.Fab", "user", None),
)


def board() -> str:
    layers: sexpr.Node = [S("layers")]
    for n, name, kind, user in LAYERS:
        item: sexpr.Node = [S(n), name, S(kind)]
        if user:
            item.append(user)
        layers.append(item)
    node: sexpr.Node = [
        S("kicad_pcb"),
        [S("version"), S("20260206")],
        [S("generator"), "pcbnew"],
        [S("generator_version"), "10.0"],
        [S("general"), [S("thickness"), S("1.6")], [S("legacy_teardrops"), S("no")]],
        [S("paper"), "A4"],
        [S("title_block"), [S("title"), "Demo board (synthetic test data)"]],
        layers,
        [S("setup"), [S("pad_to_mask_clearance"), S("0")]],
        [S("net"), S("0"), ""],
    ]
    node += [board_footprint(f) for f in ON_BOARD]
    node.append(
        [
            S("gr_rect"),
            [S("start"), S("100"), S("50")],
            [S("end"), S("160"), S("90")],
            [S("stroke"), [S("width"), S("0.05")], [S("type"), S("default")]],
            [S("fill"), S("no")],
            [S("layer"), "Edge.Cuts"],
            [S("uuid"), uid("outline")],
        ]
    )
    node.append([S("embedded_fonts"), S("no")])
    return sexpr.dumps(node)


def lib_footprint(name: str, through_hole: bool = False) -> str:
    """A footprint file in KiCad 10's own format (for the project libraries)."""
    f = OnBoard(
        "REF**", f"x:{name}", 0, 0, 0, attrs="through_hole" if through_hole else "smd"
    )
    node: sexpr.Node = [
        S("footprint"),
        name,
        [S("version"), S("20260206")],
        [S("generator"), "pcbnew"],
        [S("generator_version"), "10.0"],
        [S("layer"), "F.Cu"],
        [S("descr"), f"{name} (synthetic test footprint)"],
        [S("attr"), S("smd")],
        *pads(f),
    ]
    if name in FOOTPRINT_MODELS:
        node.append(
            [
                S("model"),
                f"${{KIPRJMOD}}/jlcpcb-lib/jlcpcb.3dshapes/{FOOTPRINT_MODELS[name]}",
                [S("offset"), [S("xyz"), S("0"), S("0"), S("0")]],
                [S("scale"), [S("xyz"), S("1"), S("1"), S("1")]],
                [S("rotate"), [S("xyz"), S("0"), S("0"), S("0")]],
            ]
        )
    node.append([S("embedded_fonts"), S("no")])
    return sexpr.dumps(node)


def project_file() -> str:
    data = {
        "board": {
            "design_settings": {
                "rules": {
                    "min_clearance": 0.0,
                    "min_copper_edge_clearance": 0.5,
                    "min_hole_clearance": 0.25,
                    "min_hole_to_hole": 0.25,
                    "min_resolved_spokes": 1,
                    "min_through_hole_diameter": 0.3,
                    "min_track_width": 0.0,
                    "min_via_annular_width": 0.25,
                    "min_via_diameter": 0.5,
                }
            }
        },
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{PROJECT}.kicad_pro", "version": 3},
    }
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


DRU = """\
(version 1)
# Hand-written rules of the demo project (from before it used kifab).
# TODO demo: a rule the designer keeps.

(rule "Demo: keep-out clearance"
	(condition "A.Type == 'track'")
	(constraint edge_clearance (min 0.5mm))
)
"""

FP_LIB_TABLE = """\
(fp_lib_table
	(version 7)
	(lib (name "demo-board")(type "KiCad")(uri "${KIPRJMOD}/demo-board.pretty")(options "")(descr "Project footprints"))
	(lib (name "jlcpcb")(type "KiCad")(uri "${KIPRJMOD}/jlcpcb-lib/jlcpcb.pretty")(options "")(descr "Fab footprints"))
)
"""

#: What the bundle must produce for the demo board (written by hand from the
#: board above, not computed by kifab): DNP R3, not-in-BOM TP1, through-hole J1
#: and the board-only MH1 are left out; C1 and C3 (sub-sheet) share a line.
EXPECTED_BOM = (
    '"Comment","Designator","Footprint","LCSC Part #"\r\n'
    '"100nF","C1,C3","jlcpcb:C0603","C900003"\r\n'
    '"4.7uF","C2","jlcpcb:C0805","C900004"\r\n'
    '"LED","D1","demo-board:LED_0603","C900008"\r\n'
    '"330R","R1,R2","jlcpcb:R0603","C900001"\r\n'
    '"4k7","R4","jlcpcb:R0603","C900002"\r\n'
    '"KF-MOD-1","U1","jlcpcb:MOD-SMD_KF1","C900005"\r\n'
)
EXPECTED_CPL = (
    "Designator,Mid X,Mid Y,Layer,Rotation\r\n"
    "C1,120.0000mm,-60.0000mm,Top,270\r\n"
    "C2,122.5000mm,-62.7500mm,Top,270\r\n"
    "C3,125.0000mm,-60.0000mm,Top,90\r\n"
    "D1,140.0000mm,-60.0000mm,Top,0\r\n"
    "R1,110.0000mm,-60.0000mm,Top,0\r\n"
    "R2,112.5000mm,-60.0000mm,Top,90\r\n"
    "R4,117.5000mm,-65.2500mm,Bottom,180\r\n"
    "U1,130.0000mm,-70.0000mm,Top,0\r\n"
)


def write_project(root: Path) -> None:
    d = root / PROJECT
    write(d / f"{PROJECT}.kicad_pro", project_file())
    write(d / f"{PROJECT}.kicad_sch", schematic("root"))
    write(d / "power.kicad_sch", schematic("power"))
    write(d / f"{PROJECT}.kicad_pcb", board())
    write(d / f"{PROJECT}.kicad_dru", DRU)
    write(d / "fp-lib-table", FP_LIB_TABLE)
    write(d / f"{PROJECT}.pretty" / "LED_0603.kicad_mod", lib_footprint("LED_0603"))
    for name in FOOTPRINT_MODELS:  # models left out on purpose: check reports them
        write(
            d / "jlcpcb-lib" / "jlcpcb.pretty" / f"{name}.kicad_mod",
            lib_footprint(name),
        )
    write(root / "expected" / f"{PROJECT}_bom_jlcpcb.csv", EXPECTED_BOM)
    write(root / "expected" / f"{PROJECT}_cpl_jlcpcb.csv", EXPECTED_CPL)


# ---------------------------------------------------------------- kicad-cli samples

PosRow = tuple[str, str, str, float, float, float, str]


def write_kicad_cli_samples(root: Path) -> None:
    """kicad-cli 10 output in its real layout, with invented content."""
    d = root / "kicad_cli"

    def item(descr: str, x: float, y: float, name: str) -> dict[str, object]:
        return {"description": descr, "pos": {"x": x, "y": y}, "uuid": uid(name)}

    drc = {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "coordinate_units": "mm",
        "date": "2026-01-01T00:00:00",
        "ignored_checks": [
            {
                "description": "Silkscreen clipped by solder mask",
                "key": "silk_over_copper",
            }
        ],
        "included_severities": ["error", "warning", "exclusion"],
        "kicad_version": "10.0.6",
        "schematic_parity": [],
        "source": f"{PROJECT}.kicad_pcb",
        "unconnected_items": [],
        "violations": [
            {
                "description": "The current configuration does not include the "
                "footprint library 'Connector_PinHeader_2.54mm'",
                "items": [item("Footprint J1", 105, 80, "drc:j1")],
                "severity": "warning",
                "type": "lib_footprint_issues",
            },
            {
                "description": "The current configuration does not include the "
                "footprint library 'MountingHole'",
                "items": [item("Footprint MH1", 103, 53, "drc:mh1")],
                "severity": "warning",
                "type": "lib_footprint_issues",
            },
        ],
    }
    dump_json(d / "drc.json", drc)
    rules = dict(drc)
    rules["violations"] = [
        {
            "description": "Track width (rule 'Track width, outer layer (1oz copper)' "
            "min width 0.1270 mm; actual 0.1000 mm)",
            "items": [
                item("Track [<no net>] on F.Cu, length 10.0000 mm", 110, 110, "t1")
            ],
            "severity": "error",
            "type": "track_width",
        },
        {
            "description": "Text height out of range (rule 'Silkscreen text' min height "
            "1.0000 mm; actual 0.5000 mm)",
            "items": [item("PCB text 'tiny' on F.Silkscreen", 125, 140, "t2")],
            "severity": "error",
            "type": "text_height",
        },
        {
            "description": "Text thickness out of range (rule 'Silkscreen text' min "
            "thickness 0.1500 mm; actual 0.1000 mm)",
            "items": [item("PCB text 'tiny' on F.Silkscreen", 125, 140, "t3")],
            "severity": "error",
            "type": "text_thickness",
        },
    ]
    dump_json(d / "rules_drc.json", rules)
    write(
        d / "bom_raw.csv",
        '"Value","Reference","Footprint","Part"\n'
        '"100nF","C1,C3","jlcpcb:C0603","C900003"\n'
        '"4.7uF","C2","jlcpcb:C0805","C900004"\n'
        '"LED","D1","demo-board:LED_0603","C900008"\n'
        '"hdr","J1","Connector_PinHeader_2.54mm:PinHeader_2x04","C900006"\n'
        '"330R","R1,R2","jlcpcb:R0603","C900001"\n'
        '"4k7","R4","jlcpcb:R0603","C900002"\n'
        '"KF-MOD-1","U1","jlcpcb:MOD-SMD_KF1","C900005"\n',
    )
    # as kicad-cli 10.0.6 writes it: back side first, rotations not normalised
    smd_rows: list[PosRow] = [
        ("R4", "4k7", "R0603", 117.5, -65.25, 180, "bottom"),
        ("C1", "100nF", "C0603", 120, -60, -90, "top"),
        ("C2", "4.7uF", "C0805", 122.5, -62.75, -90, "top"),
        ("C3", "100nF", "C0603", 125, -60, 90, "top"),
        ("D1", "LED", "LED_0603", 140, -60, 0, "top"),
        ("R1", "330R", "R0603", 110, -60, 0, "top"),
        ("R2", "330R", "R0603", 112.5, -60, 90, "top"),
        ("U1", "KF-MOD-1", "MOD-SMD_KF1", 130, -70, 0, "top"),
    ]
    tht_rows: list[PosRow] = [
        ("J1", "hdr", "PinHeader_2x04", 105, -80, 90, "top"),
        ("TP1", "TP", "PinHeader_1x01", 150, -80, 0, "top"),
    ]

    def pos(rows: list[PosRow]) -> str:
        out = "Ref,Val,Package,PosX,PosY,Rot,Side\n"
        for ref, val, pkg, x, y, r, side in rows:
            out += f'"{ref}","{val}","{pkg}",{x:.6f},{y:.6f},{r:.6f},{side}\n'
        return out

    write(d / "pos_smd.csv", pos(smd_rows))
    back, front = smd_rows[:1], smd_rows[1:]
    write(d / "pos_all.csv", pos(back + sorted(front + tht_rows)))
    layers = ["F_Cu", "In1_Cu", "In2_Cu", "B_Cu", "F_Paste", "B_Paste"]
    layers += ["F_Silkscreen", "B_Silkscreen", "F_Mask", "B_Mask", "Edge_Cuts"]
    ext = {"F_Cu": "gtl", "In1_Cu": "g1", "In2_Cu": "g2", "B_Cu": "gbl"}
    ext |= {"F_Paste": "gtp", "B_Paste": "gbp", "F_Silkscreen": "gto"}
    ext |= {"B_Silkscreen": "gbo", "F_Mask": "gts", "B_Mask": "gbs", "Edge_Cuts": "gm1"}
    write(
        d / "gerber_names_4layer.txt",
        "".join(f"board-{name}.{ext[name]}\n" for name in layers),
    )


# ---------------------------------------------------------------- io


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def dump_json(path: Path, data: object) -> None:
    write(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


GENERATED = (
    "demo-board",
    "expected",
    "kicad_cli",
    "jlcpcb_search",
    "easyeda2kicad_output",
)


def generate(root: Path) -> None:
    for name in GENERATED:
        if (root / name).exists():
            shutil.rmtree(root / name)
    write_project(root)
    write_kicad_cli_samples(root)
    write_catalog(root)
    write_images(root)
    write_e2k(root)


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE)
