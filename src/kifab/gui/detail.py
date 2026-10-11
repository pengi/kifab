"""Part detail panel: photo, footprint and symbol drawings, facts and links."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kifab.core.factscache import FactsCache
from kifab.core.inventory import LibraryPart
from kifab.core.parts import LibraryType, PartHit
from kifab.core.project import Project
from kifab.libraries.base import PartSource
from kifab.suppliers.base import Catalog
from kifab.gui.jobs import Job, Task
from kifab.gui.previews import (
    Preview,
    cached_preview,
    fetch_preview,
    library_preview,
    load_image,
    load_preview,
)
from kifab.kicad.drawing import (
    Arc,
    Circle,
    Drawing,
    Layer,
    Pad,
    Pin,
    Point,
    Polygon,
    Segment,
    arc_points,
)

TYPE_LABEL = {
    LibraryType.BASIC: "Basic",
    LibraryType.PREFERRED: "Preferred extended (no setup fee)",
    LibraryType.EXTENDED: "Extended (setup fee per part type)",
    LibraryType.UNKNOWN: "unknown",
}
#: cached price/stock older than this is refreshed once the user stays on a part
STALE_AFTER = 24 * 3600.0
#: how long the user must stay on a part before its stale facts are refreshed
DWELL_MS = 2000
_DIMMED = QColor(160, 160, 160)

# KiCad-like colours
_BOARD_BG = QColor(0, 16, 35)
_COLOURS = {
    Layer.COPPER: QColor(200, 52, 52),
    Layer.SILK: QColor(242, 237, 161),
    Layer.FAB: QColor(175, 175, 175),
    Layer.COURTYARD: QColor(255, 38, 226),
    Layer.OTHER: QColor(120, 120, 120),
}
_SYMBOL_BG = QColor(245, 244, 239)
_SYMBOL_LINE = QColor(132, 0, 0)
_SYMBOL_FILL = QColor(255, 255, 194)
_PIN = QColor(132, 0, 0)


def _safe_url(url: str) -> bool:
    return url.startswith(("https://", "http://"))


class DrawingView(QGraphicsView):
    """Shows a :class:`Drawing` scaled to fit, or a short message."""

    def __init__(self, symbol: bool) -> None:
        super().__init__()
        self.symbol = symbol
        self.setScene(QGraphicsScene(self))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setBackgroundBrush(QBrush(_SYMBOL_BG if symbol else _BOARD_BG))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.drawing: Drawing | None = None
        self.message = ""

    def show_message(self, text: str) -> None:
        self.drawing = None
        self.message = text
        scene = self.scene()
        scene.clear()
        item = scene.addText(text)
        item.setDefaultTextColor(
            QColor(90, 90, 90) if self.symbol else Qt.GlobalColor.gray
        )
        self.setSceneRect(item.boundingRect())
        self.resetTransform()

    def set_drawing(self, drawing: Drawing | None, empty: str = "no drawing") -> None:
        if drawing is None or not drawing.shapes:
            self.show_message(empty)
            return
        self.drawing = drawing
        self.message = ""
        scene = self.scene()
        scene.clear()
        flip = -1.0 if drawing.y_up else 1.0

        def pt(p: Point) -> QPointF:
            return QPointF(p.x, flip * p.y)

        for shape in drawing.shapes:
            if isinstance(shape, Pad):
                self._pad(scene, shape, pt)
        for shape in drawing.shapes:
            if not isinstance(shape, Pad):
                self._shape(scene, shape, pt)
        rect = scene.itemsBoundingRect()
        margin = max(rect.width(), rect.height()) * 0.08 + 0.1
        self.setSceneRect(rect.adjusted(-margin, -margin, margin, margin))
        self._fit()

    def _pen(self, layer: Layer, width: float) -> QPen:
        colour = _SYMBOL_LINE if self.symbol else _COLOURS[layer]
        pen = QPen(colour, width if width > 0 else 0.15)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        if width <= 0:
            pen.setCosmetic(True)
            pen.setWidthF(1.5)
        return pen

    def _fill(self, layer: Layer) -> QBrush:
        if self.symbol:
            return QBrush(_SYMBOL_FILL)
        return QBrush(_COLOURS[layer])

    def _pad(
        self, scene: QGraphicsScene, pad: Pad, pt: Callable[[Point], QPointF]
    ) -> None:
        w, h = pad.width, pad.height
        path = QPainterPath()
        rect = QRectF(-w / 2, -h / 2, w, h)
        if pad.shape == "circle":
            path.addEllipse(rect)
        elif pad.shape == "oval":
            r = min(w, h) / 2
            path.addRoundedRect(rect, r, r)
        elif pad.shape == "roundrect":
            r = min(w, h) * 0.25
            path.addRoundedRect(rect, r, r)
        else:
            path.addRect(rect)
        if pad.drill > 0:
            hole = QPainterPath()
            d = pad.drill
            hole.addEllipse(QRectF(-d / 2, -d / 2, d, d))
            path = path.subtracted(hole)
        item = scene.addPath(
            path, QPen(Qt.PenStyle.NoPen), QBrush(_COLOURS[Layer.COPPER])
        )
        item.setTransform(
            QTransform()
            .translate(pt(pad.center).x(), pt(pad.center).y())
            .rotate(-pad.rotation)
        )

    def _shape(
        self,
        scene: QGraphicsScene,
        shape: Segment | Circle | Arc | Polygon | Pin,
        pt: Callable[[Point], QPointF],
    ) -> None:
        if isinstance(shape, Pin):
            pen = QPen(_PIN, 0.15)
            scene.addLine(
                pt(shape.tip).x(),
                pt(shape.tip).y(),
                pt(shape.end).x(),
                pt(shape.end).y(),
                pen,
            )
            r = 0.3
            scene.addEllipse(
                pt(shape.tip).x() - r,
                pt(shape.tip).y() - r,
                2 * r,
                2 * r,
                QPen(_PIN, 0.08),
            )
            return
        pen = self._pen(shape.layer, shape.width)
        if isinstance(shape, Segment):
            a, b = pt(shape.start), pt(shape.end)
            scene.addLine(a.x(), a.y(), b.x(), b.y(), pen)
        elif isinstance(shape, Circle):
            c, r = pt(shape.center), shape.radius
            brush = self._fill(shape.layer) if shape.filled else QBrush()
            scene.addEllipse(c.x() - r, c.y() - r, 2 * r, 2 * r, pen, brush)
        elif isinstance(shape, Arc):
            path = QPainterPath()
            points = [pt(p) for p in arc_points(shape)]
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
            scene.addPath(path, pen)
        else:
            path = QPainterPath()
            points = [pt(p) for p in shape.points]
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
            if shape.closed:
                path.closeSubpath()
            brush = self._fill(shape.layer) if shape.filled else QBrush()
            scene.addPath(path, pen, brush)

    def _fit(self) -> None:
        if self.drawing is not None:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._fit()


class PictureArea(QWidget):
    """Symbol above footprint on the left, the photo on the right.

    Always twice as wide as high: symbol and footprint are 2:1 each and form a
    square together, the photo is a square of the same size. The children are
    placed by hand so the proportions hold whatever the panel does.
    """

    MIN_WIDTH = 280

    def __init__(
        self, symbol: QWidget, footprint: QWidget, photo: QWidget, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.symbol, self.footprint, self.photo = symbol, footprint, photo
        for child in (symbol, footprint, photo):
            child.setParent(self)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumWidth(self.MIN_WIDTH)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return width // 2

    def sizeHint(self) -> QSize:
        return QSize(2 * 200, 200)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        side = min(self.width() // 2, self.height())
        x0 = (self.width() - 2 * side) // 2
        half = side // 2
        self.symbol.setGeometry(x0, 0, side, half)
        self.footprint.setGeometry(x0, half, side, side - half)
        self.photo.setGeometry(x0 + side, 0, side, side)


class PartDetail(QWidget):
    """Everything known about one part, loaded in the background as needed."""

    def __init__(
        self,
        catalog: Callable[[], Catalog],
        source: Callable[[], PartSource],
        project: Callable[[], Project | None],
        cache: Path,
    ) -> None:
        super().__init__()
        self._catalog = catalog
        self._source = source
        self._project = project
        self.cache = cache
        self._jobs: list[Job] = []
        self._lookups: dict[str, PartHit | None] = {}
        self.facts = FactsCache(cache / "facts.sqlite")
        self.clock: Callable[[], float] = time.time  # tests replace it
        self.dwell_ms = DWELL_MS
        self.stale_after = STALE_AFTER
        #: when the shown hit was fetched, if it came from the cache (dimmed)
        self.cached_at: float | None = None
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._refresh_facts)
        self._pixmap: QPixmap | None = None
        self.part_id: str | None = None
        self.hit: PartHit | None = None
        self.library_part: LibraryPart | None = None

        self.title = QLabel("Select a part to see its details.")
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.photo = QLabel("")
        self.photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo.setStyleSheet("QLabel { background: white; color: gray; }")
        self.photo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.footprint_view = DrawingView(symbol=False)
        self.symbol_view = DrawingView(symbol=True)
        self.fetch_button = QPushButton("Show footprint and symbol")
        self.fetch_button.setToolTip(
            "Fetch the part's footprint and symbol from the fab's library for a look "
            "(kept in kifab-gui's cache, not added to the project)"
        )
        self.fetch_button.clicked.connect(self.fetch_drawings)
        self.links = QLabel("")
        self.links.setOpenExternalLinks(True)
        self.links.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.info = QTableWidget(0, 2)
        self.info.horizontalHeader().hide()
        self.info.verticalHeader().hide()
        self.info.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.info.horizontalHeader().setStretchLastSection(True)
        self.info.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.info.setWordWrap(True)

        self.pictures = PictureArea(
            self.symbol_view, self.footprint_view, self.photo, self
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.pictures)
        layout.addWidget(self.fetch_button)
        layout.addWidget(self.links)
        layout.addWidget(self.info, 1)
        self.clear()

    # ------------------------------------------------------------ public

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    def clear(self) -> None:
        self.part_id = None
        self.hit = None
        self.library_part = None
        self.title.setText("Select a part to see its details.")
        self.refresh_timer.stop()
        self.cached_at = None
        self._pixmap = None
        self.photo.clear()
        self.footprint_view.show_message("")
        self.symbol_view.show_message("")
        self.footprint_view.setToolTip("")
        self.symbol_view.setToolTip("")
        self.fetch_button.hide()
        self.links.setText("")
        self.info.setRowCount(0)

    def show_part(
        self,
        part_id: str,
        hit: PartHit | None = None,
        library_part: LibraryPart | None = None,
    ) -> None:
        """Show ``part_id``: catalogue facts from ``hit`` (looked up if None)."""
        self.clear()
        self.part_id = part_id
        self.hit = hit
        self.library_part = library_part
        self.title.setText(f"<b>{part_id}</b>")
        self._fill_info()
        self._load_drawings()
        if hit is not None:  # from a search answer: fresh
            self.facts.put(hit, self.clock())
            self._load_image()
        elif part_id in self._lookups:  # looked up earlier this session
            self.hit = self._lookups[part_id]
            self._fill_info()
            self._load_image()
        elif (cached := self.facts.get(part_id)) is not None:
            self.hit = cached.hit
            self.cached_at = cached.fetched
            self._fill_info()
            self._load_image()
            if self.clock() - cached.fetched > self.stale_after:
                self.refresh_timer.start(self.dwell_ms)
        else:
            self.photo.setText("looking up...")
            self._run(
                lambda _r: self._catalog().lookup(part_id), self._looked_up, part_id
            )

    def fetch_drawings(self) -> Job | None:
        part_id = self.part_id
        if part_id is None:
            return None
        self.fetch_button.setEnabled(False)
        self.footprint_view.show_message("fetching...")
        self.symbol_view.show_message("fetching...")
        cache = self.cache
        source = self._source()
        return self._run(
            lambda _r: load_preview(fetch_preview(source, cache, part_id)),
            self._show_preview,
            part_id,
            failed=self._fetch_failed,
        )

    # ------------------------------------------------------------ loading

    def _run(
        self,
        task: Task,
        on_done: Callable[[object], None],
        part_id: str,
        failed: Callable[[str], None] | None = None,
    ) -> Job:
        job = Job(f"detail {part_id}", task)

        def done(result: object) -> None:
            if self.part_id == part_id:  # the user may have moved on
                on_done(result)

        def fail(message: str) -> None:
            if self.part_id == part_id and failed is not None:
                failed(message)

        def finished() -> None:
            if job in self._jobs:
                self._jobs.remove(job)
            job.deleteLater()

        job.done.connect(done)
        job.failed.connect(fail)
        job.finished.connect(finished)
        self._jobs.append(job)
        job.start()
        return job

    def wait(self, timeout_ms: int = 30000) -> None:
        """Block until background loads are done (tests, shutdown)."""
        for job in list(self._jobs):
            job.wait(timeout_ms)

    def _looked_up(self, result: object) -> None:
        hit = result if isinstance(result, PartHit) else None
        if self.part_id is not None:
            self._lookups[self.part_id] = hit
        if hit is not None:
            self.facts.put(hit, self.clock())
        self.hit = hit
        self._fill_info()
        self._load_image()

    def _refresh_facts(self) -> None:
        """The user stayed on a part whose cached price/stock is stale."""
        part_id = self.part_id
        if part_id is None or self.cached_at is None:
            return
        catalog = self._catalog()
        self._run(
            lambda _r: catalog.lookup(part_id), self._refreshed, part_id
        )  # on failure the facts stay dimmed

    def _refreshed(self, result: object) -> None:
        if not isinstance(result, PartHit):
            return
        self._lookups[result.id] = result
        self.facts.put(result, self.clock())
        self.hit = result
        self.cached_at = None
        self._fill_info()

    def _load_image(self) -> None:
        hit = self.hit
        if hit is None or not hit.images:
            self._pixmap = None
            self.photo.setText(
                "no photo from the fab" if hit else "not in the catalogue"
            )
            return
        self.photo.setText("loading photo...")
        catalog = self._catalog()
        cache = self.cache
        url = hit.images[0]
        self._run(
            lambda _r: load_image(catalog, cache, url, hit.id),
            self._show_image,
            hit.id,
            failed=lambda msg: self.photo.setText("photo not available"),
        )

    def _show_image(self, data: object) -> None:
        pixmap = QPixmap()
        if not isinstance(data, bytes) or not pixmap.loadFromData(data):
            self.photo.setText("photo not readable")
            return
        self._pixmap = pixmap
        self._scale_photo()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._scale_photo()

    def _scale_photo(self) -> None:
        if self._pixmap is not None:
            self.photo.setPixmap(
                self._pixmap.scaled(
                    self.photo.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _load_drawings(self) -> None:
        project = self._project()
        part_id = self.part_id
        if part_id is None:
            return
        if self.library_part is not None and project is not None:
            files = library_preview(project, self.library_part)
        else:
            cached = cached_preview(self.cache, part_id)
            if cached is None:
                self.footprint_view.show_message("not fetched")
                self.symbol_view.show_message("not fetched")
                self.fetch_button.setEnabled(True)
                self.fetch_button.show()
                return
            files = cached
        self._show_preview(load_preview(files))

    def _show_preview(self, preview: object) -> None:
        if not isinstance(preview, Preview):
            return
        self.fetch_button.hide()
        self.footprint_view.set_drawing(preview.footprint, "no footprint")
        self.symbol_view.set_drawing(preview.symbol, "no symbol")
        self.footprint_view.setToolTip(preview.footprint_name)
        self.symbol_view.setToolTip(preview.symbol_name)

    def _fetch_failed(self, message: str) -> None:
        self.fetch_button.setEnabled(True)
        self.footprint_view.show_message("fetch failed")
        self.symbol_view.show_message("")
        self.footprint_view.setToolTip(message)

    # ------------------------------------------------------------ facts

    def _fill_info(self) -> None:
        rows: list[tuple[str, str]] = []
        dimmed: set[str] = set()  # row names whose value may be out of date
        hit = self.hit
        part = self.library_part
        if hit is not None:
            self.title.setText(
                f"<b>{hit.id}</b> &nbsp; {hit.mpn} &nbsp; <i>{hit.manufacturer}</i>"
                f"<br>{hit.description}"
            )
            rows += [
                ("Part", hit.id),
                ("MPN", hit.mpn),
                ("Manufacturer", hit.manufacturer),
                ("Package", hit.package),
                ("Category", hit.category),
                ("Type", TYPE_LABEL[hit.library_type]),
                ("Stock", f"{hit.stock:,}"),
                ("Minimum order", str(hit.min_order)),
            ]
            if self.cached_at is not None:
                when = datetime.fromtimestamp(self.cached_at).strftime("%Y-%m-%d %H:%M")
                rows.append(("Stock and prices as of", when))
                dimmed.add("Stock")
            for brk in hit.prices:
                upper = f"-{brk.max_qty}" if brk.max_qty is not None else "+"
                name = f"Price {brk.min_qty}{upper}"
                rows.append((name, f"${brk.unit_price:.4f}"))
                if self.cached_at is not None:
                    dimmed.add(name)
            rows += list(hit.attributes)
            links = []
            if _safe_url(hit.datasheet):
                links.append(f'<a href="{hit.datasheet}">Datasheet</a>')
            if _safe_url(hit.url):
                links.append(f'<a href="{hit.url}">Product page</a>')
            self.links.setText(" &nbsp; ".join(links))
        if part is not None:
            if hit is None and part.description:
                self.title.setText(f"<b>{part.id}</b><br>{part.description}")
            refs = sorted(set(part.schematic_refs) | set(part.board_refs))
            rows += [
                ("In project", ", ".join(part.part.symbols + part.footprints)),
                ("Used by", ", ".join(refs) or "nothing yet"),
                ("Description in KiCad", part.description or "(none)"),
                ("Fetched with", part.part.source),
            ]
        self.info.setRowCount(len(rows))
        for i, (name, value) in enumerate(rows):
            key = QTableWidgetItem(name)
            key.setForeground(QBrush(QColor(110, 110, 110)))
            self.info.setItem(i, 0, key)
            item = QTableWidgetItem(value)
            if name in dimmed:
                item.setForeground(QBrush(_DIMMED))
            self.info.setItem(i, 1, item)
        self.info.resizeRowsToContents()
