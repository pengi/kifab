"""The kifab main window: start page, project parts and library, check/bundle."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, QSortFilterProxyModel, Qt, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from kifab import __version__
from kifab.core.bundle import BundleOptions, BundleResult, bundle
from kifab.core.check import CheckResult, run_check
from kifab.core.ci import init_ci
from kifab.core.design_rules import apply_rules
from kifab.core.git import GitCli
from kifab.core.init import init_project
from kifab.core.issues import Issue, Severity
from kifab.core.order import record_order
from kifab.core.project import Project
from kifab.core.reporter import Reporter
from kifab.gui.assets import read_asset
from kifab.gui.issues import SORT_ROLE, IssueModel
from kifab.gui.jobs import Job
from kifab.gui.new_project import NewProjectDialog, NewProjectRequest
from kifab.gui.parts import PartsPanel
from kifab.gui.settings import GuiSettings, cache_dir

_SEVERITY_FILTER = (("All", -1), ("Errors", 2), ("Errors + warnings", 1))


class SeverityFilter(QSortFilterProxyModel):
    """Hides issues below a minimum severity rank (-1 shows all)."""

    def __init__(self) -> None:
        super().__init__()
        self.min_rank = -1

    def set_min_rank(self, rank: int) -> None:
        self.min_rank = rank
        self.invalidate()

    def filterAcceptsRow(self, row: int, parent: object) -> bool:
        model = self.sourceModel()
        rank = model.data(model.index(row, 0), SORT_ROLE)
        return isinstance(rank, int) and rank >= self.min_rank


def app_icon() -> QIcon:
    """The kifab application icon."""
    pixmap = QPixmap()
    pixmap.loadFromData(read_asset("kifab-icon.svg"))
    return QIcon(pixmap)


def logo_pixmap(height: int) -> QPixmap:
    """The kifab logo, rendered ``height`` px tall with a wordmark that reads on
    the current theme (Qt's SVG renderer ignores the file's dark-mode query)."""
    app = QGuiApplication.instance()
    assert isinstance(app, QGuiApplication)
    text = app.palette().windowText().color().name()
    svg = (
        read_asset("kifab-logo.svg")
        .decode()
        .replace(".word { stroke: #1d5c3f; }", f".word {{ stroke: {text}; }}")
    )
    renderer = QSvgRenderer(svg.encode())
    ratio = app.devicePixelRatio()
    size = renderer.defaultSize()
    width = round(height * size.width() / size.height())
    pixmap = QPixmap(round(width * ratio), round(height * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, width, height))
    painter.end()
    return pixmap


class StartPage(QWidget):
    """Shown while no project is open: new, open, and the recent projects."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window_ = window
        logo = QLabel()
        logo.setPixmap(logo_pixmap(96))
        logo.setAccessibleName("kifab")
        title = QLabel(
            f"<p>KiCad projects with the fab built in (version {__version__})</p>"
        )
        new = QPushButton("New project...")
        new.clicked.connect(window.new_project)
        open_ = QPushButton("Open project...")
        open_.clicked.connect(window.choose_project)
        for button in (new, open_):
            button.setMinimumHeight(36)
        self.recent = QListWidget()
        self.recent.itemActivated.connect(self._activated)
        self.empty = QLabel("No recent projects yet.")
        buttons = QHBoxLayout()
        buttons.addWidget(new)
        buttons.addWidget(open_)
        buttons.addStretch()
        column = QVBoxLayout()
        column.addWidget(logo)
        column.addWidget(title)
        column.addLayout(buttons)
        column.addSpacing(16)
        column.addWidget(QLabel("<b>Recent projects</b> (double-click to open)"))
        column.addWidget(self.recent, 1)
        column.addWidget(self.empty)
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        outer.addLayout(column, 3)
        outer.addStretch(1)

    def update_recent(self, recent: list[Path]) -> None:
        self.recent.clear()
        for path in recent:
            exists = path.exists()
            text = f"{path.stem}    {path.parent}" + ("" if exists else "  (missing)")
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            if not exists:
                item.setForeground(Qt.GlobalColor.gray)
            self.recent.addItem(item)
        self.empty.setVisible(not recent)

    def _activated(self, item: QListWidgetItem) -> None:
        self.window_.open_recent(Path(str(item.data(Qt.ItemDataRole.UserRole))))


def _button(action: QAction) -> QPushButton:
    """A real push button for ``action`` (a flat tool bar reads like a menu bar)."""
    button = QPushButton(action.icon(), action.text().replace("&", ""))
    button.setToolTip(action.toolTip())
    button.clicked.connect(action.trigger)
    button.setEnabled(action.isEnabled())
    action.changed.connect(lambda: button.setEnabled(action.isEnabled()))
    return button


class MainWindow(QMainWindow):
    def __init__(self, settings: GuiSettings | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f"kifab {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1280, 800)
        self.settings = settings if settings is not None else GuiSettings.load()
        self.project: Project | None = None
        self.job: Job | None = None
        self.last_bundle: BundleResult | None = None

        self.model = IssueModel()
        self.filter = SeverityFilter()
        self.filter.setSourceModel(self.model)
        self.filter.setSortRole(SORT_ROLE)
        self.table = QTableView()
        self.table.setModel(self.filter)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)

        self.info = QLabel("No project open. Open one or create a new one.")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.info.setWordWrap(True)
        self.skip_check = QCheckBox("Skip check")
        self.skip_drc = QCheckBox("Skip DRC")
        self.force = QCheckBox("Ignore KiCad lock files")
        self.stock = QCheckBox("Check stock (online)")
        self.boards = QSpinBox()
        self.boards.setRange(1, 100000)
        self.boards.setPrefix("boards: ")
        self.severity = QComboBox()
        for label, rank in _SEVERITY_FILTER:
            self.severity.addItem(label, rank)
        self.severity.currentIndexChanged.connect(
            lambda _: self.filter.set_min_rank(int(self.severity.currentData()))
        )

        side = QWidget()
        side_layout = QVBoxLayout(side)
        project_box = QGroupBox("Project")
        QVBoxLayout(project_box).addWidget(self.info)
        options_box = QGroupBox("Bundle options")
        options = QFormLayout(options_box)
        for check in (self.skip_check, self.skip_drc, self.force):
            options.addRow(check)
        options.addRow(self.stock)
        options.addRow(self.boards)
        filter_box = QGroupBox("Show")
        QVBoxLayout(filter_box).addWidget(self.severity)
        for box in (project_box, options_box, filter_box):
            side_layout.addWidget(box)
        side_layout.addStretch()

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.table.selectionModel().currentRowChanged.connect(
            lambda current, _: self._show_detail(current.row())
        )
        issues_tab = QWidget()
        issues_layout = QVBoxLayout(issues_tab)
        issues_layout.setContentsMargins(0, 0, 0, 0)
        issues_layout.addWidget(self.table)
        issues_layout.addWidget(self.detail)
        self.parts = PartsPanel(
            self.start_job, lambda: self.project, self._say, cache_dir()
        )
        self.tabs = QTabWidget()
        self.tabs.addTab(self.parts, "Parts")
        self.tabs.addTab(issues_tab, "Check && bundle results")
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.tabs)
        right.addWidget(self.log)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(side)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        self.start_page = StartPage(self)
        self.button_bar = QHBoxLayout()
        self.project_page = QWidget()
        page_layout = QVBoxLayout(self.project_page)
        page_layout.addLayout(self.button_bar)
        page_layout.addWidget(main, 1)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.start_page)
        self.pages.addWidget(self.project_page)
        self.setCentralWidget(self.pages)
        self._build_actions()
        self._update_actions()
        self.statusBar().showMessage("Ready")

    # ------------------------------------------------------------ actions

    def _icon(self, pixmap: QStyle.StandardPixmap) -> QIcon:
        return self.style().standardIcon(pixmap)

    def _build_actions(self) -> None:
        sp = QStyle.StandardPixmap
        self.toolbar_actions: dict[str, QAction] = {}
        specs: tuple[
            tuple[str, str, object, QKeySequence | None, QStyle.StandardPixmap | None],
            ...,
        ] = (
            ("new", "&New project...", self.new_project, QKeySequence(QKeySequence.StandardKey.New), sp.SP_FileDialogNewFolder),
            ("open", "&Open project...", self.choose_project, QKeySequence(QKeySequence.StandardKey.Open), sp.SP_DialogOpenButton),
            ("close", "&Close project", self.close_project, QKeySequence(QKeySequence.StandardKey.Close), sp.SP_DialogCloseButton),
            ("quit", "&Quit", self.close, QKeySequence(QKeySequence.StandardKey.Quit), None),
            ("check", "&Check", self.run_check, QKeySequence("F5"), sp.SP_DialogApplyButton),
            ("bundle", "&Bundle for the fab", self.run_bundle, QKeySequence("F6"), sp.SP_DialogSaveButton),
            ("rules", "Apply the fab's design &rules", self.apply_rules, None, None),
            ("kicad", "Open in &KiCad", self.open_in_kicad, None, sp.SP_DesktopIcon),
            ("build", "Open &build folder", self.open_build_dir, None, sp.SP_DirOpenIcon),
            ("order", "Record &order", self.record_order, None, None),
            ("tag", "&Tag release...", self.tag_release, None, None),
        )  # fmt: skip
        for key, label, slot, shortcut, icon in specs:
            action = QAction(label, self)
            action.triggered.connect(slot)
            if shortcut is not None:
                action.setShortcut(shortcut)
            if icon is not None:
                action.setIcon(self._icon(icon))
            self.toolbar_actions[key] = action
        a = self.toolbar_actions

        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        file_menu.addAction(a["new"])
        file_menu.addAction(a["open"])
        self.recent_menu = QMenu("Open &recent", self)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addMenu(self.recent_menu)
        file_menu.addAction(a["close"])
        file_menu.addSeparator()
        file_menu.addAction(a["quit"])
        project_menu = menu.addMenu("&Project")
        for key in ("check", "bundle", "rules"):
            project_menu.addAction(a[key])
        project_menu.addSeparator()
        for key in ("order", "tag"):
            project_menu.addAction(a[key])
        project_menu.addSeparator()
        for key in ("kicad", "build"):
            project_menu.addAction(a[key])

        self.buttons: dict[str, QPushButton] = {}
        for group in (("check", "bundle"), ("kicad", "build")):
            for key in group:
                self.buttons[key] = _button(a[key])
                self.button_bar.addWidget(self.buttons[key])
            self.button_bar.addSpacing(16)
        self.button_bar.addStretch()
        self._show_page()

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        for path in self.settings.recent:
            action = self.recent_menu.addAction(f"{path.stem}  ({path.parent})")
            action.triggered.connect(lambda _=False, p=path: self.open_recent(p))
        if self.settings.recent:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear list").triggered.connect(
                self.clear_recent
            )
        else:
            self.recent_menu.addAction("(none)").setEnabled(False)

    def clear_recent(self) -> None:
        self.settings.recent = []
        self.settings.save()
        self.start_page.update_recent([])

    def _show_page(self) -> None:
        if self.project is None:
            self.start_page.update_recent(self.settings.recent)
            self.pages.setCurrentWidget(self.start_page)
        else:
            self.pages.setCurrentWidget(self.project_page)

    # ------------------------------------------------------------ state

    def _busy(self) -> bool:
        return self.job is not None

    def _update_actions(self) -> None:
        has = self.project is not None
        idle = not self._busy()
        for key in ("check", "bundle", "kicad"):
            self.toolbar_actions[key].setEnabled(has and idle)
        self.toolbar_actions["rules"].setEnabled(
            has
            and idle
            and self.project is not None
            and self.project.config is not None
        )
        self.toolbar_actions["build"].setEnabled(
            has
            and self.project is not None
            and (self.project.directory / "build").is_dir()
        )
        self.toolbar_actions["new"].setEnabled(idle)
        self.toolbar_actions["order"].setEnabled(
            has
            and idle
            and self.project is not None
            and (self.project.directory / "build").is_dir()
        )
        self.toolbar_actions["tag"].setEnabled(has and idle)
        self.toolbar_actions["close"].setEnabled(has and idle)
        self.parts.refresh()
        self.toolbar_actions["open"].setEnabled(idle)

    def _say(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _error(self, title: str, message: str) -> None:
        self._say(f"error: {message}")
        QMessageBox.warning(self, title, message)

    def _show_project(self) -> None:
        p = self.project
        if p is None:
            self.info.setText("No project open.")
            return
        if p.config is None:
            config = "no kifab.toml (plain KiCad project)"
        else:
            config = (
                f"fab: {p.config.fab}<br>layers: {p.config.board.layers}<br>"
                f"rules: {p.config.rules.profile or 'default'}"
            )
        self.info.setText(f"<b>{p.name}</b><br>{p.directory}<br>{config}")
        self.setWindowTitle(f"{p.name} - kifab {__version__}")

    # ------------------------------------------------------------ project

    def open_project(self, path: Path) -> bool:
        if self._busy():
            return False
        try:
            project = Project.open(path)
        except Exception as exc:  # InputError, ConfigError, ...
            self._error("Cannot open project", str(exc))
            return False
        self.project = project
        self.model.clear()
        self.parts.detail.clear()
        self._show_project()
        self._say(f"opened {project.directory}")
        self.settings.add_recent(project.kicad.pro)
        self.settings.save()
        self._update_actions()
        self._show_page()
        self.tabs.setCurrentWidget(self.parts)
        return True

    def open_recent(self, path: Path) -> bool:
        if not path.exists():
            self._error(
                "Open recent", f"{path} no longer exists; removed from the list"
            )
            self.settings.forget(path)
            self.settings.save()
            self.start_page.update_recent(self.settings.recent)
            return False
        return self.open_project(path)

    def close_project(self) -> None:
        if self._busy() or self.project is None:
            return
        self._say(f"closed {self.project.directory}")
        self.project = None
        self.last_bundle = None
        self.model.clear()
        self.parts.detail.clear()
        self.parts.query.clear()
        self.parts.model.set_hits([])
        self._show_project()
        self.setWindowTitle(f"kifab {__version__}")
        self._update_actions()
        self._show_page()

    def choose_project(self) -> None:
        start = str(self.project.directory) if self.project else str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open KiCad project", start, "KiCad project (*.kicad_pro)"
        )
        if chosen:
            self.open_project(Path(chosen))

    def create_project(self, request: NewProjectRequest) -> bool:
        if not request.name:
            self._error("New project", "a project name is needed")
            return False
        try:
            git = GitCli()
            result = init_project(
                request.directory,
                request.name,
                request.fab,
                layers=request.layers,
                git=git if request.git else None,
            )
            if request.ci:
                init_ci(result.project, git)
        except Exception as exc:  # InitError, CiError, GitError
            self._error("New project", str(exc))
            return False
        self._say(f"created {result.project.directory} ({len(result.files)} files)")
        return self.open_project(result.project.directory)

    def new_project(self) -> None:
        start = self.project.directory.parent if self.project else None
        dialog = NewProjectDialog(self, start)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.create_project(dialog.request())

    def apply_rules(self) -> None:
        if self.project is None:
            return
        try:
            result = apply_rules(self.project, force=self.force.isChecked())
        except Exception as exc:  # KicadOpen, ConfigError
            self._error("Apply rules", str(exc))
            return
        if not result.changed:
            self._say("rules already match the fab's profile")
        for path in result.changed:
            self._say(f"updated {self.project.rel(path)}")
        if result.backup:
            self._say(f"backup in {self.project.rel(result.backup)}/")

    def open_in_kicad(self) -> None:
        if self.project is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.kicad.pro)))

    def _show_detail(self, proxy_row: int) -> None:
        if proxy_row < 0:
            self.detail.setText("")
            return
        source = self.filter.mapToSource(self.filter.index(proxy_row, 0))
        issue = self.model.issue_at(source.row())
        parts = [f"<b>{issue.severity.value}</b> [{issue.code.value}] {issue.message}"]
        if issue.ref:
            parts.append(f"parts: {issue.ref}")
        if issue.path:
            parts.append(f"file: {issue.path}")
        if issue.hint:
            parts.append(f"what to do: {issue.hint}")
        self.detail.setText("<br>".join(parts))

    def record_order(self) -> None:
        if self.project is None:
            return
        result = record_order(self.project, GitCli())
        if result.issues:
            self._error("Record order", "\n".join(i.message for i in result.issues))
            return
        assert result.directory is not None
        staged = " and staged in git" if result.staged else ""
        self._say(f"recorded {self.project.rel(result.directory)}/{staged}")

    def tag_release(self) -> None:
        if self.project is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Tag release",
            "Tag (push it to trigger the release workflow):",
            text="v",
        )
        if not ok or not name.strip():
            return
        self.create_tag(name.strip())

    def create_tag(self, name: str) -> bool:
        if self.project is None:
            return False
        try:
            GitCli().tag(self.project.directory, name, f"{self.project.name} {name}")
        except Exception as exc:  # GitError
            self._error("Tag release", str(exc))
            return False
        self._say(f"tagged {name}; push it with: git push origin {name}")
        return True

    def open_build_dir(self) -> None:
        if self.project is not None:
            build = self.project.directory / "build"
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(build)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.job is not None:
            self.job.wait(10000)
        self.parts.wait()
        super().closeEvent(event)

    # ------------------------------------------------------------ jobs

    def start_job(
        self,
        title: str,
        task: Callable[[Reporter], object],
        on_done: Callable[[object], None],
    ) -> Job | None:
        if self._busy():
            return None
        self.model.clear()
        self._say(f"--- {title}")
        self.statusBar().showMessage(f"{title}...")
        job = Job(title, task)
        job.stepped.connect(lambda name, detail: self._say(f"  {name:<10} {detail}"))
        job.found.connect(self._on_issue)
        job.done.connect(on_done)
        job.failed.connect(lambda msg: self._error(title, msg))
        job.finished.connect(self._job_finished)
        self.job = job
        self._update_actions()
        job.start()
        return job

    def _on_issue(self, issue: object) -> None:
        if isinstance(issue, Issue):
            self.model.add(issue)

    def _job_finished(self) -> None:
        job, self.job = self.job, None
        if job is not None:
            job.deleteLater()
        self._update_actions()

    def _summary(self, issues: list[Issue]) -> str:
        counts = {s: sum(1 for i in issues if i.severity is s) for s in Severity}
        return (
            f"{counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} "
            f"warning(s), {counts[Severity.INFO]} note(s)"
        )

    def run_check(self) -> Job | None:
        project = self.project
        if project is None:
            return None

        def done(result: object) -> None:
            if isinstance(result, CheckResult):
                self.model.set_issues(result.issues)
                text = f"check: {self._summary(result.issues)}"
                self._say(text)
                self.statusBar().showMessage(text)

        self.tabs.setCurrentIndex(1)
        return self.start_job(
            "check",
            lambda reporter: run_check(project, git=GitCli(), reporter=reporter),
            done,
        )

    def run_bundle(self) -> Job | None:
        project = self.project
        if project is None:
            return None
        options = BundleOptions(
            skip_check=self.skip_check.isChecked(),
            skip_drc=self.skip_drc.isChecked(),
            force=self.force.isChecked(),
            stock=self.stock.isChecked(),
            boards=self.boards.value(),
        )

        def done(result: object) -> None:
            if not isinstance(result, BundleResult):
                return
            self.last_bundle = result
            self.model.set_issues(result.issues)
            verdict = "ok" if result.ok else "FAILED"
            text = f"bundle {verdict}: {self._summary(result.issues)}"
            if result.files:
                text += f"; {len(result.files)} files in {result.out_dir}"
            self._say(text)
            self.statusBar().showMessage(text)
            self._update_actions()

        self.tabs.setCurrentIndex(1)
        return self.start_job(
            "bundle",
            lambda reporter: bundle(project, options, git=GitCli(), reporter=reporter),
            done,
        )
