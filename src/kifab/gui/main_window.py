"""The kifab main window: project, issue list, check/bundle with live progress."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSortFilterProxyModel, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QInputDialog,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableView,
    QToolBar,
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
from kifab.gui.issues import SORT_ROLE, IssueModel
from kifab.gui.jobs import Job
from kifab.gui.new_project import NewProjectDialog, NewProjectRequest
from kifab.gui.parts import PartsPanel

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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"kifab {__version__}")
        self.resize(1100, 700)
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
        self.parts = PartsPanel(self.start_job, lambda: self.project, self._say)
        self.tabs = QTabWidget()
        self.tabs.addTab(issues_tab, "Issues")
        self.tabs.addTab(self.parts, "Parts")
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.tabs)
        right.addWidget(self.log)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(side)
        main.addWidget(right)
        main.setStretchFactor(1, 1)
        self.setCentralWidget(main)

        self.toolbar_actions: dict[str, QAction] = {}
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for key, label, slot, shortcut in (
            ("new", "New...", self.new_project, QKeySequence.StandardKey.New),
            ("open", "Open...", self.choose_project, QKeySequence.StandardKey.Open),
            ("check", "Check", self.run_check, QKeySequence("F5")),
            ("bundle", "Bundle", self.run_bundle, QKeySequence("F6")),
            ("rules", "Apply rules", self.apply_rules, None),
            ("kicad", "Open in KiCad", self.open_in_kicad, None),
            ("build", "Open build folder", self.open_build_dir, None),
            ("order", "Record order", self.record_order, None),
            ("tag", "Tag release...", self.tag_release, None),
        ):
            action = QAction(label, self)
            action.triggered.connect(slot)
            if shortcut is not None:
                action.setShortcut(shortcut)
            toolbar.addAction(action)
            self.toolbar_actions[key] = action
        self._update_actions()
        self.statusBar().showMessage("Ready")

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
        if hasattr(self, "parts"):
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
        try:
            self.project = Project.open(path)
        except Exception as exc:  # InputError, ConfigError, ...
            self._error("Cannot open project", str(exc))
            return False
        self.model.clear()
        self._show_project()
        self._say(f"opened {self.project.directory}")
        self._update_actions()
        return True

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

        return self.start_job(
            "bundle",
            lambda reporter: bundle(project, options, git=GitCli(), reporter=reporter),
            done,
        )
