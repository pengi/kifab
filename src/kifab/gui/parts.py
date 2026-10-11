"""Parts tab: the project library (usage, remove), catalogue search (add), details."""

from __future__ import annotations

from collections.abc import Callable

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from kifab.core.inventory import (
    LibraryContents,
    LibraryPart,
    MissingPart,
    RemoveError,
    library_contents,
    remove_part,
)
from kifab.core.library import SyncResult, VendorResult, sync_parts, vendor_part
from kifab.core.parts import LibraryType, PartHit, SearchPage, SearchSort
from kifab.core.project import Project
from kifab.core.quick import QuickResult, format_value, parse_quick, quick_search
from kifab.core.reporter import Reporter
from kifab.core.sourcing import Sourcing, sourcing_for
from kifab.fabs.base import get_fab
from kifab.libraries.base import PartSource
from kifab.suppliers.base import Catalog
from kifab.gui.detail import PartDetail
from kifab.gui.issues import SORT_ROLE, Index
from kifab.gui.jobs import Job, Task
from kifab.kicad.schematic import sorted_refs

COLUMNS = ("Part", "Type", "Stock", "1 pc", "Package", "MPN", "Description", "")
BADGE = {
    LibraryType.BASIC: ("Basic", QColor(30, 140, 60)),
    LibraryType.PREFERRED: ("Preferred", QColor(40, 110, 180)),
    LibraryType.EXTENDED: ("Extended", QColor(190, 120, 0)),
    LibraryType.UNKNOWN: ("?", QColor(120, 120, 120)),
}
SEARCH_DELAY_MS = 400
PAGE_SIZE = 50  # results per request
_TYPE_ORDER = {
    LibraryType.BASIC: 0,
    LibraryType.PREFERRED: 1,
    LibraryType.EXTENDED: 2,
    LibraryType.UNKNOWN: 3,
}

#: (title, task, on_done) -> the job, or None if another job is running
JobRunner = Callable[[str, Task, Callable[[object], None]], Job | None]


LIBRARY_COLUMNS = (
    "Part",
    "Status",
    "Uses",
    "Used by",
    "Symbol",
    "Footprint",
    "Description",
)
_MISSING_COLOUR = QColor(190, 120, 0)
_UNUSED_COLOUR = QColor(130, 130, 130)


@dataclass(frozen=True)
class LibraryRow:
    """A row of the project-library table: a vendored part or a missing one."""

    part: LibraryPart | None = None
    missing: MissingPart | None = None

    @property
    def id(self) -> str:
        if self.part is not None:
            return self.part.id
        assert self.missing is not None
        return self.missing.id

    @property
    def refs(self) -> list[str]:
        if self.part is not None:
            return sorted_refs(
                set(self.part.schematic_refs) | set(self.part.board_refs)
            )
        assert self.missing is not None
        return list(self.missing.refs)


class LibraryModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[LibraryRow] = []

    def set_contents(self, contents: LibraryContents | None) -> None:
        self.beginResetModel()
        self.rows = []
        if contents is not None:
            self.rows = [LibraryRow(part=p) for p in contents.parts]
            self.rows += [LibraryRow(missing=m) for m in contents.not_vendored]
        self.endResetModel()

    def row_for(self, part_id: str) -> int:
        return next((i for i, r in enumerate(self.rows) if r.id == part_id), -1)

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(LIBRARY_COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = 0
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return LIBRARY_COLUMNS[section]
        return None

    def data(self, index: Index, role: int = 0) -> object:
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()
        refs = row.refs
        if role == SORT_ROLE:
            if col == 2:
                return len(refs)
            return str(self.data(index, Qt.ItemDataRole.DisplayRole)).lower()
        if role == Qt.ItemDataRole.DisplayRole:
            if row.part is not None:
                status = "in library" if refs else "in library, unused"
                symbols = ", ".join(row.part.part.symbols)
                footprints = ", ".join(row.part.footprints)
                description = row.part.description
            else:
                status, symbols, footprints = "not vendored", "", ""
                description = ""
            return (
                row.id,
                status,
                str(len(refs)),
                ",".join(refs),
                symbols,
                footprints,
                description,
            )[col]
        if role == Qt.ItemDataRole.ForegroundRole and col == 1:
            if row.part is None:
                return QBrush(_MISSING_COLOUR)
            if not refs:
                return QBrush(_UNUSED_COLOUR)
        if role == Qt.ItemDataRole.TextAlignmentRole and col == 2:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            if row.part is None:
                return "Used in the schematic but not in the library: Sync adds it"
            return "\n".join(row.part.part.files)
        return None


class PartModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[PartHit] = []
        self.vendored: set[str] = set()
        self.notes: dict[str, str] = {}  # part id -> e.g. "alt. 5.1kΩ (+8.5%)"

    def set_hits(
        self, hits: list[PartHit], notes: dict[str, str] | None = None
    ) -> None:
        self.beginResetModel()
        self.hits = hits
        self.notes = notes or {}
        self.endResetModel()

    def set_vendored(self, ids: set[str]) -> None:
        self.beginResetModel()
        self.vendored = ids
        self.endResetModel()

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.hits)

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = 0
    ) -> object:
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return COLUMNS[section]
        return None

    def data(self, index: Index, role: int = 0) -> object:
        if not index.isValid():
            return None
        hit = self.hits[index.row()]
        col = index.column()
        if role == SORT_ROLE:
            if col == 1:
                return _TYPE_ORDER[hit.library_type]
            if col == 2:
                return hit.stock
            if col == 3:
                price = hit.unit_price(1)
                return price if price is not None else math.inf
            return str(self.data(index, Qt.ItemDataRole.DisplayRole)).lower()
        if role == Qt.ItemDataRole.DisplayRole:
            price = hit.unit_price(1)
            return (
                hit.id,
                BADGE[hit.library_type][0],
                f"{hit.stock:,}",
                f"${price:.4f}" if price is not None else "-",
                hit.package,
                hit.mpn,
                hit.description,
                "; ".join(
                    n
                    for n in (
                        self.notes.get(hit.id, ""),
                        "in project" if hit.id in self.vendored else "",
                    )
                    if n
                ),
            )[col]
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 1:
                return QBrush(BADGE[hit.library_type][1])
            if col == 2 and hit.stock == 0:
                return QBrush(QColor(200, 30, 30))
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (2, 3):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [f"{hit.manufacturer} {hit.mpn}", hit.description]
            lines += [f"{name}: {value}" for name, value in hit.attributes]
            if hit.datasheet:
                lines.append(hit.datasheet)
            return "\n".join(lines)
        return None


def _table(model: QAbstractTableModel) -> tuple[QTableView, QSortFilterProxyModel]:
    """A table showing ``model`` through a proxy, sorted by clicking a title.

    Until a title is clicked the model's own order is kept (search ranking).
    """
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setSortRole(SORT_ROLE)
    table = QTableView()
    table.setModel(proxy)
    table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
    table.verticalHeader().hide()
    table.setWordWrap(False)
    table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(True)
    return table, proxy


def _source_row(table: QTableView, proxy: QSortFilterProxyModel) -> int:
    """The model row of the table's selected row, or -1."""
    rows = table.selectionModel().selectedRows()
    return proxy.mapToSource(rows[0]).row() if rows else -1


def _select_source_row(
    table: QTableView, proxy: QSortFilterProxyModel, row: int
) -> None:
    index = proxy.mapFromSource(proxy.sourceModel().index(row, 0))
    if index.isValid():
        table.selectRow(index.row())


class PartsPanel(QWidget):
    def __init__(
        self,
        run_job: JobRunner,
        project: Callable[[], Project | None],
        log: Callable[[str], None],
        cache: Path,
    ) -> None:
        super().__init__()
        self._run_job = run_job
        self._project = project
        self._log = log
        self._search_job: Job | None = None
        self._pending = False
        self.contents: LibraryContents | None = None

        # project library
        self.library_model = LibraryModel()
        self.library_table, self.library_proxy = _table(self.library_model)
        header = self.library_table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.library_table.selectionModel().currentRowChanged.connect(
            lambda current, _: self._library_selected(
                self.library_proxy.mapToSource(current).row()
            )
        )
        self.library_table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_remove()
        )
        self.library_status = QLabel("")
        self.sync_button = QPushButton("Sync from schematic")
        self.sync_button.setToolTip("Vendor every part number the schematic uses")
        self.sync_button.clicked.connect(self.sync)
        self.remove_button = QPushButton("Remove from project")
        self.remove_button.setToolTip(
            "Remove the part from kifab.lock and delete the library files only it uses"
        )
        self.remove_button.clicked.connect(lambda: self.remove_selected())
        self.find_button = QPushButton("Search at fab")
        self.find_button.setToolTip("Look the part up in the fab's catalogue below")
        self.find_button.clicked.connect(lambda: self.find_selected())
        self.refresh_button = QPushButton("Refresh from fab")
        self.refresh_button.setToolTip(
            "Fetch the part again: fills in a missing description, and offers to "
            "replace vendored files that differ from the fab's current ones"
        )
        self.refresh_button.clicked.connect(lambda: self.refresh_selected())
        self._select_after_search: str | None = None
        self._refresh_kept: tuple[str, list[str]] | None = None
        self._replace: bool | None = None

        # catalogue search
        self.query = QLineEdit()
        self.query.setPlaceholderText(
            "Search: value, package, MPN or C-number; passives: R 4k7 0603, C 18p, L 10u"
        )
        self.query.setToolTip(
            "R, C or L then a value (4k7, 4.7k, 4700, 1R5, 18p, 100n, 4u7) lists only "
            "parts of that kind and value, Basic first; more words narrow it down"
        )
        self.query.setClearButtonEnabled(True)
        self.basic_only = QCheckBox("Basic only")
        self.alternatives = QCheckBox("±10% alternatives")
        self.alternatives.setToolTip(
            "For R/C/L searches: also list the E24 values within ±10%"
        )
        self.status = QLabel("")
        self.model = PartModel()
        self.table, self.proxy = _table(self.model)
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self.table.doubleClicked.connect(lambda _: self.add_selected())
        self.table.selectionModel().currentRowChanged.connect(
            lambda current, _: self._hit_selected(self.proxy.mapToSource(current).row())
        )
        self.more_button = QPushButton("Load more")
        self.more_button.setToolTip("Fetch the next page of results from the fab")
        self.more_button.setEnabled(False)
        self.more_button.clicked.connect(lambda: self.load_more())
        self.last_page: SearchPage | None = None
        self.sort = SearchSort.RELEVANCE
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._sort_clicked)
        self.add_button = QPushButton("Add to project")
        self.add_button.clicked.connect(self.add_selected)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(SEARCH_DELAY_MS)
        self.timer.timeout.connect(self.search_now)
        self.query.textChanged.connect(lambda _: self.timer.start())
        self.basic_only.toggled.connect(lambda _: self.timer.start())
        self.alternatives.toggled.connect(lambda _: self.timer.start())
        self.query.returnPressed.connect(self.search_now)

        self.detail = PartDetail(self._catalog, self._part_source, project, cache)

        library_box = QGroupBox("Project library")
        library_layout = QVBoxLayout(library_box)
        library_layout.addWidget(self.library_table)
        row = QHBoxLayout()
        row.addWidget(self.library_status)
        row.addStretch()
        row.addWidget(self.find_button)
        row.addWidget(self.refresh_button)
        row.addWidget(self.sync_button)
        row.addWidget(self.remove_button)
        library_layout.addLayout(row)

        search_box = QGroupBox("Find parts at the fab")
        search_layout = QVBoxLayout(search_box)
        top = QHBoxLayout()
        top.addWidget(self.query)
        top.addWidget(self.basic_only)
        top.addWidget(self.alternatives)
        search_layout.addLayout(top)
        search_layout.addWidget(self.table)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status)
        bottom.addWidget(self.more_button)
        bottom.addStretch()
        bottom.addWidget(self.add_button)
        search_layout.addLayout(bottom)

        lists = QSplitter(Qt.Orientation.Vertical)
        lists.addWidget(library_box)
        lists.addWidget(search_box)
        detail_box = QGroupBox("Part")
        QVBoxLayout(detail_box).addWidget(self.detail)
        detail_box.setMinimumWidth(380)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(lists)
        split.addWidget(detail_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.refresh()

    # ------------------------------------------------------------ state

    def _sourcing(self) -> Sourcing:
        project = self._project()
        sourcing = project.sourcing() if project else None
        return sourcing or sourcing_for(get_fab("jlcpcb"))

    def _catalog(self) -> Catalog:
        return self._sourcing().catalog()

    def _part_source(self) -> PartSource:
        return self._sourcing().part_source()

    def refresh(self) -> None:
        """Reload the project library and update buttons and "in project" marks."""
        project = self._project()
        has_config = project is not None and project.config is not None
        self.add_button.setEnabled(has_config)
        self.sync_button.setEnabled(has_config)
        selected = self.selected_library_row()
        contents: LibraryContents | None = None
        if project is None:
            self.library_status.setText("no project open")
        elif project.config is None:
            self.library_status.setText(
                "no kifab.toml: this KiCad project has no fab library"
            )
        else:
            try:
                contents = library_contents(project)
            except Exception as exc:  # broken lock or schematic: check reports it
                self.library_status.setText(f"cannot read the library: {exc}")
            else:
                self.library_status.setText(self._summary(contents))
        self.contents = contents
        self.library_model.set_contents(contents)
        self.model.set_vendored(
            {p.id for p in contents.parts} if contents is not None else set()
        )
        if selected is not None:
            self.select_library(selected.id)
        self._update_remove()
        if project is None:
            self.detail.clear()

    @staticmethod
    def _summary(contents: LibraryContents) -> str:
        used = sum(1 for p in contents.parts if p.used)
        text = f"{len(contents.parts)} parts, {used} in use"
        if contents.not_vendored:
            text += f"; {len(contents.not_vendored)} used but not vendored"
        if contents.without_number:
            text += f"; {len(contents.without_number)} symbols without a part number"
        return text

    def _update_remove(self) -> None:
        row = self.selected_library_row()
        project = self._project()
        has_config = project is not None and project.config is not None
        vendored = row is not None and row.part is not None
        self.remove_button.setEnabled(vendored)
        self.refresh_button.setEnabled(vendored and has_config)
        self.find_button.setEnabled(row is not None)

    def selected(self) -> PartHit | None:
        row = _source_row(self.table, self.proxy)
        return self.model.hits[row] if 0 <= row < len(self.model.hits) else None

    def selected_library_row(self) -> LibraryRow | None:
        row = _source_row(self.library_table, self.library_proxy)
        rows = self.library_model.rows
        return rows[row] if 0 <= row < len(rows) else None

    def select_library(self, part_id: str) -> bool:
        """Select ``part_id`` in the project-library table, however it is sorted."""
        row = self.library_model.row_for(part_id)
        if row >= 0:
            _select_source_row(self.library_table, self.library_proxy, row)
        return row >= 0

    def select_hit(self, part_id: str) -> bool:
        """Select ``part_id`` in the search results, however they are sorted."""
        ids = [h.id for h in self.model.hits]
        if part_id in ids:
            _select_source_row(self.table, self.proxy, ids.index(part_id))
        return part_id in ids

    def _library_selected(self, row: int) -> None:
        self._update_remove()
        if not 0 <= row < len(self.library_model.rows):
            return
        self.table.clearSelection()
        entry = self.library_model.rows[row]
        self.detail.show_part(entry.id, library_part=entry.part)

    def _hit_selected(self, row: int) -> None:
        if not 0 <= row < len(self.model.hits):
            return
        self.library_table.clearSelection()
        hit = self.model.hits[row]
        part = self.contents.get(hit.id) if self.contents is not None else None
        self.detail.show_part(hit.id, hit=hit, library_part=part)

    # ------------------------------------------------------------ search
    def search_now(self, page: int = 1) -> Job | None:
        """Search for the query text; ``page`` > 1 appends the next page."""
        self.timer.stop()
        text = self.query.text().strip()
        if not text:
            self.model.set_hits([])
            self.status.setText("")
            self.last_page = None
            self.more_button.setEnabled(False)
            return None
        if self._search_job is not None:
            self._pending = True  # search again when the running one ends
            return None
        catalog = self._catalog()
        basic = self.basic_only.isChecked()
        quick = parse_quick(text)
        wide = self.alternatives.isChecked()
        sort = self.sort

        def task(_reporter: Reporter) -> object:
            if quick is not None:
                return quick_search(
                    catalog, quick, with_alternatives=wide, basic_only=basic, sort=sort
                )
            return catalog.search(
                text, page=page, page_size=PAGE_SIZE, basic_only=basic, sort=sort
            )

        job = Job("search", task)
        job.done.connect(lambda result: self._show(result, text))
        job.failed.connect(lambda msg: self.status.setText(f"search failed: {msg}"))
        job.finished.connect(self._search_finished)
        self._search_job = job
        self.status.setText(f"searching {text!r}...")
        self.more_button.setEnabled(False)
        job.start()
        return job

    def load_more(self) -> Job | None:
        """Append the next page of the last (plain) search."""
        last = self.last_page
        if last is None or last.page >= last.pages:
            return None
        return self.search_now(page=last.page + 1)

    def _sort_clicked(self, column: int, order: Qt.SortOrder) -> None:
        """Clicking Stock or 1 pc asks the fab to sort, when it holds more results.

        The table sorts what is loaded either way; a truncated result is first
        fetched again in that order, so its top is the catalogue's top.
        """
        wanted = SearchSort.RELEVANCE
        if column == 2 and order == Qt.SortOrder.DescendingOrder:
            wanted = SearchSort.STOCK
        elif column == 3 and order == Qt.SortOrder.AscendingOrder:
            wanted = SearchSort.PRICE
        if wanted is self.sort or wanted is SearchSort.RELEVANCE:
            return
        self.sort = wanted
        last = self.last_page
        truncated = last is None or last.total > len(self.model.hits)
        if self.query.text().strip() and truncated:
            self.search_now()

    def _sort_note(self) -> str:
        return {
            SearchSort.RELEVANCE: "",
            SearchSort.STOCK: ", most in stock first",
            SearchSort.PRICE: ", cheapest first",
        }[self.sort]

    def _show(self, result: object, text: str) -> None:
        if isinstance(result, QuickResult):
            self.last_page = None
            self._show_quick(result)
            return
        if not isinstance(result, SearchPage):
            return
        hits = list(result.hits)
        if result.page > 1:  # load more: append what is new
            known = {h.id for h in self.model.hits}
            hits = self.model.hits + [h for h in hits if h.id not in known]
        self.last_page = result
        self.model.set_hits(hits)
        self._select_found()
        self.more_button.setEnabled(result.page < result.pages)
        self.status.setText(
            f"{len(hits)} of {result.total} parts for {text!r}{self._sort_note()}"
            if hits
            else f"nothing found for {text!r}"
        )

    def _show_quick(self, result: QuickResult) -> None:
        unit = result.query.kind.unit
        notes = {
            q.hit.id: f"alt. {format_value(q.value)}{unit} ({q.deviation:+.1%})"
            for q in result.hits
            if not q.exact
        }
        self.model.set_hits([q.hit for q in result.hits], notes)
        self._select_found()
        what = " ".join((result.query.label, *result.query.extra))
        kind = result.query.kind.name.lower()
        if not result.hits:
            self.status.setText(f"no {kind} {what} found")
            return
        exact = sum(1 for q in result.hits if q.exact)
        text = f"{exact} {kind}(s) {what}"
        if notes:
            text += f" + {len(notes)} alternative(s)"
        self.status.setText(text)

    def _search_finished(self) -> None:
        job, self._search_job = self._search_job, None
        if job is not None:
            job.deleteLater()
        if self._pending:
            self._pending = False
            self.search_now()

    def wait(self) -> None:
        """Let background work finish (before the window goes away)."""
        if self._search_job is not None:
            self._search_job.wait(10000)
        self.detail.wait()

    @property
    def searching(self) -> bool:
        return self._search_job is not None or self._pending

    # ------------------------------------------------------------ add / sync

    def _select_found(self) -> None:
        """After "Search at fab": select the part in the results."""
        wanted = self._select_after_search
        if wanted is not None and self.select_hit(wanted):
            self._select_after_search = None
            self.table.setFocus()

    def find_selected(self) -> Job | None:
        """Search the fab's catalogue for the selected project-library part."""
        row = self.selected_library_row()
        if row is None:
            return None
        self._select_after_search = row.id
        self.query.setText(row.id)
        return self.search_now()

    def refresh_selected(self, replace: bool | None = None) -> Job | None:
        """Fetch the selected library part again (fills in a missing description).

        When the fab's files now differ from the vendored ones, asks whether to
        replace them; ``replace`` answers that in advance (tests, scripting).
        """
        project = self._project()
        row = self.selected_library_row()
        if project is None or project.config is None or row is None or row.part is None:
            return None
        part_id = row.id
        catalog = self._catalog()
        self._replace = replace
        self._refresh_kept = None

        def done(result: object) -> None:
            if isinstance(result, VendorResult):
                written = ", ".join(result.written) or "nothing changed"
                self._log(f"refreshed {result.part_id}: {written}")
                if result.kept:
                    self._refresh_kept = (result.part_id, result.kept)
            self.refresh()

        job = self._run_job(
            f"refresh {part_id}",
            lambda _r: vendor_part(project, part_id, catalog=catalog, fresh=True),
            done,
        )
        if job is not None:
            # connected after the window's own handler: the job slot is free then
            job.finished.connect(self._after_refresh)
        return job

    def _after_refresh(self) -> None:
        pending, self._refresh_kept = self._refresh_kept, None
        if pending is None:
            return
        part_id, kept = pending
        replace = self._replace
        if replace is None:
            answer = QMessageBox.question(
                self,
                "Refresh part",
                f"The fab's current files for {part_id} differ from the vendored "
                "ones:\n\n" + "\n".join(kept) + "\n\nReplace the vendored files? "
                "Shared files change for every part using them.",
            )
            replace = answer == QMessageBox.StandardButton.Yes
        if not replace:
            self._log(f"  kept the vendored files of {part_id}: {', '.join(kept)}")
            return
        project = self._project()
        if project is None:
            return

        def done(result: object) -> None:
            if isinstance(result, VendorResult):
                self._log(f"replaced {', '.join(result.written)}")
            self.refresh()

        self._run_job(
            f"replace {part_id}",
            lambda _r: vendor_part(project, part_id, refresh=True, fresh=False),
            done,
        )

    def add_selected(self) -> Job | None:
        """Add the selected search hit (or the selected not-vendored library row)."""
        project = self._project()
        if project is None or project.config is None:
            return None
        hit = self.selected()
        row = self.selected_library_row()
        if hit is not None:
            part_id = hit.id
        elif row is not None and row.missing is not None:
            part_id = row.id
        else:
            return None
        catalog = None if hit is not None else self._catalog()

        def done(result: object) -> None:
            if isinstance(result, VendorResult):
                what = ", ".join(result.symbols + result.footprints)
                self._log(f"added {result.part_id}: {what}")
                for issue in result.issues:
                    self._log(f"  {issue.severity.value}: {issue.message}")
                self._log("  (reopen the project in KiCad if it does not show up)")
            self.refresh()

        return self._run_job(
            f"add {part_id}",
            lambda _r: vendor_part(project, part_id, hit=hit, catalog=catalog),
            done,
        )

    def sync(self) -> Job | None:
        project = self._project()
        if project is None or project.config is None:
            return None

        def task(reporter: Reporter) -> object:
            return sync_parts(
                project, delay=0.5, reporter=reporter, catalog=self._catalog()
            )

        def done(result: object) -> None:
            if isinstance(result, SyncResult):
                self._log(
                    f"sync: {len(result.vendored)} vendored, {len(result.skipped)} "
                    f"already vendored, {len(result.failed)} failed"
                )
            self.refresh()

        return self._run_job("sync", task, done)

    def remove_selected(self, force: bool | None = None) -> bool:
        """Remove the selected library part; asks first if it is still used.

        ``force`` answers that question in advance (tests, scripting).
        """
        project = self._project()
        row = self.selected_library_row()
        if project is None or row is None or row.part is None:
            return False
        refs = row.refs
        if refs and force is None:
            answer = QMessageBox.question(
                self,
                "Remove part",
                f"{row.id} is still used by {', '.join(refs)}.\n\nRemove it from the "
                "library anyway? The schematic and board keep referring to it until "
                "you change them.",
            )
            force = answer == QMessageBox.StandardButton.Yes
            if not force:
                return False
        try:
            result = remove_part(project, row.id, force=bool(force))
        except (RemoveError, OSError, ValueError) as exc:
            self._log(f"error: {exc}")
            QMessageBox.warning(self, "Remove part", str(exc))
            return False
        what = ", ".join(result.symbols) or "no symbols"
        self._log(f"removed {result.part_id}: {what}")
        for path in result.deleted:
            self._log(f"  deleted {path}")
        for path in result.kept:
            self._log(f"  kept {path} (other parts use it)")
        if result.used_by:
            self._log(f"  still referenced by {','.join(result.used_by)}")
        self.detail.clear()
        self.refresh()
        return True
