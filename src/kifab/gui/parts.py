"""Parts tab: search the fab's catalogue as you type, add parts, sync the schematic."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from kifab.core.library import SyncResult, VendorResult, sync_parts, vendor_part
from kifab.core.lockfile import Lockfile, load_lockfile
from kifab.core.parts import LibraryType, PartHit, SearchPage
from kifab.core.project import Project
from kifab.core.reporter import Reporter
from kifab.fabs.base import get_fab
from kifab.gui.issues import Index
from kifab.gui.jobs import Job, Task

COLUMNS = ("Part", "Type", "Stock", "1 pc", "Package", "MPN", "Description", "")
BADGE = {
    LibraryType.BASIC: ("Basic", QColor(30, 140, 60)),
    LibraryType.PREFERRED: ("Preferred", QColor(40, 110, 180)),
    LibraryType.EXTENDED: ("Extended", QColor(190, 120, 0)),
    LibraryType.UNKNOWN: ("?", QColor(120, 120, 120)),
}
SEARCH_DELAY_MS = 400

#: (title, task, on_done) -> the job, or None if another job is running
JobRunner = Callable[[str, Task, Callable[[object], None]], Job | None]


class PartModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.hits: list[PartHit] = []
        self.vendored: set[str] = set()

    def set_hits(self, hits: list[PartHit]) -> None:
        self.beginResetModel()
        self.hits = hits
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
                "in project" if hit.id in self.vendored else "",
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


class PartsPanel(QWidget):
    def __init__(
        self,
        run_job: JobRunner,
        project: Callable[[], Project | None],
        log: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._run_job = run_job
        self._project = project
        self._log = log
        self._search_job: Job | None = None
        self._pending = False
        self.query = QLineEdit()
        self.query.setPlaceholderText("Search parts: value, package, MPN or C-number")
        self.query.setClearButtonEnabled(True)
        self.basic_only = QCheckBox("Basic only")
        self.status = QLabel("")
        self.model = PartModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(
            6, QHeaderView.ResizeMode.Stretch
        )
        self.table.doubleClicked.connect(lambda _: self.add_selected())
        self.add_button = QPushButton("Add to project")
        self.add_button.clicked.connect(self.add_selected)
        self.sync_button = QPushButton("Sync from schematic")
        self.sync_button.setToolTip("Vendor every part number the schematic uses")
        self.sync_button.clicked.connect(self.sync)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(SEARCH_DELAY_MS)
        self.timer.timeout.connect(self.search_now)
        self.query.textChanged.connect(lambda _: self.timer.start())
        self.basic_only.toggled.connect(lambda _: self.timer.start())
        self.query.returnPressed.connect(self.search_now)

        top = QHBoxLayout()
        top.addWidget(self.query)
        top.addWidget(self.basic_only)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status)
        bottom.addStretch()
        bottom.addWidget(self.sync_button)
        bottom.addWidget(self.add_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.table)
        layout.addLayout(bottom)
        self.refresh()

    # ------------------------------------------------------------ state

    def refresh(self) -> None:
        """Update buttons and the "in project" marks for the current project."""
        project = self._project()
        has_config = project is not None and project.config is not None
        self.add_button.setEnabled(has_config)
        self.sync_button.setEnabled(has_config)
        lock: Lockfile | None = None
        if project is not None:
            try:
                lock = load_lockfile(project.lock_path)
            except Exception:  # a broken lock is reported by check
                lock = None
        self.model.set_vendored({p.id for p in lock.parts} if lock else set())

    def selected(self) -> PartHit | None:
        rows = self.table.selectionModel().selectedRows()
        return self.model.hits[rows[0].row()] if rows else None

    # ------------------------------------------------------------ search

    def search_now(self) -> Job | None:
        self.timer.stop()
        text = self.query.text().strip()
        if not text:
            self.model.set_hits([])
            self.status.setText("")
            return None
        if self._search_job is not None:
            self._pending = True  # search again when the running one ends
            return None
        project = self._project()
        fab = project.fab() if project else None
        catalog = (fab or get_fab("jlcpcb")).catalog()
        basic = self.basic_only.isChecked()
        job = Job(
            "search",
            lambda _r: catalog.search(text, page_size=50, basic_only=basic),
        )
        job.done.connect(lambda page: self._show(page, text))
        job.failed.connect(lambda msg: self.status.setText(f"search failed: {msg}"))
        job.finished.connect(self._search_finished)
        self._search_job = job
        self.status.setText(f"searching {text!r}...")
        job.start()
        return job

    def _show(self, page: object, text: str) -> None:
        if not isinstance(page, SearchPage):
            return
        self.model.set_hits(list(page.hits))
        self.status.setText(
            f"{len(page.hits)} of {page.total} parts for {text!r}"
            if page.hits
            else f"nothing found for {text!r}"
        )

    def _search_finished(self) -> None:
        job, self._search_job = self._search_job, None
        if job is not None:
            job.deleteLater()
        if self._pending:
            self._pending = False
            self.search_now()

    @property
    def searching(self) -> bool:
        return self._search_job is not None or self._pending

    # ------------------------------------------------------------ add / sync

    def add_selected(self) -> Job | None:
        project = self._project()
        hit = self.selected()
        if project is None or project.config is None or hit is None:
            return None
        part_id = hit.id

        def done(result: object) -> None:
            if isinstance(result, VendorResult):
                what = ", ".join(result.symbols + result.footprints)
                self._log(f"added {result.part_id}: {what}")
                for issue in result.issues:
                    self._log(f"  {issue.severity.value}: {issue.message}")
                self._log("  (reopen the project in KiCad if it does not show up)")
            self.refresh()

        return self._run_job(
            f"add {part_id}", lambda _r: vendor_part(project, part_id), done
        )

    def sync(self) -> Job | None:
        project = self._project()
        if project is None or project.config is None:
            return None

        def task(reporter: Reporter) -> object:
            return sync_parts(project, delay=0.5, reporter=reporter)

        def done(result: object) -> None:
            if isinstance(result, SyncResult):
                self._log(
                    f"sync: {len(result.vendored)} vendored, {len(result.skipped)} "
                    f"already vendored, {len(result.failed)} failed"
                )
            self.refresh()

        return self._run_job("sync", task, done)
