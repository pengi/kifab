"""GUI smoke tests, headless (Qt's offscreen platform). Skipped without PySide6."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from kifab.core.git import GitCli  # noqa: E402
from kifab.core.issues import Code, Severity  # noqa: E402
from kifab.gui.issues import IssueModel  # noqa: E402
from kifab.gui.jobs import Job  # noqa: E402
from kifab.gui.main_window import MainWindow  # noqa: E402
from kifab.gui.new_project import NewProjectDialog, NewProjectRequest  # noqa: E402
from kifab.core.issues import error, info  # noqa: E402


@pytest.fixture(scope="module")
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def window(app: QApplication, monkeypatch: pytest.MonkeyPatch) -> Iterator[MainWindow]:
    messages: list[str] = []

    def warn(*args: object) -> int:
        messages.append(str(args[2]))
        return 0

    monkeypatch.setattr(QMessageBox, "warning", warn)
    w = MainWindow()
    w.messages = messages  # type: ignore[attr-defined]  # test-only inspection hook
    yield w
    w.close()


def finish(window: MainWindow, timeout: float = 60.0) -> None:
    """Wait until the running job is done and its queued signals delivered."""
    deadline = time.monotonic() + timeout
    while window.job is not None:
        assert time.monotonic() < deadline, "job did not finish"
        window.job.wait(50)
        QCoreApplication.processEvents()
    QCoreApplication.processEvents()


def test_issue_model() -> None:
    model = IssueModel()
    model.set_issues([info(Code.DNP, "x"), error(Code.KICAD_OPEN, "y", ref="R1")])
    assert model.rowCount() == 2 and model.columnCount() == 5
    assert model.data(model.index(0, 0)) == "error"  # errors first
    assert model.data(model.index(0, 3)) == "[R1]"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole) == 2
    assert model.count(Severity.INFO) == 1


def test_job_reports_across_threads(app: QApplication) -> None:
    steps: list[tuple[str, str]] = []
    results: list[object] = []

    def task(reporter: Job) -> int:
        reporter.step("one", "ok")
        reporter.issue(info(Code.DNP, "x"))
        return 42

    job = Job("t", task)  # type: ignore[arg-type]  # Job is the Reporter it passes
    job.stepped.connect(lambda a, b: steps.append((a, b)))
    job.done.connect(results.append)
    job.start()
    job.wait(5000)
    QCoreApplication.processEvents()
    assert steps == [("one", "ok")] and results == [42]


def test_new_project_check_and_rules(window: MainWindow, tmp_path: Path) -> None:
    assert not window.toolbar_actions["check"].isEnabled()
    request = NewProjectRequest(tmp_path, "demo", "jlcpcb", 4, git=True, ci=True)
    assert window.create_project(request)
    assert window.project is not None and window.project.name == "demo"
    assert (tmp_path / "demo" / ".github/workflows/release.yml").is_file()
    assert "layers: 4" in window.info.text()
    assert window.toolbar_actions["rules"].isEnabled()
    assert window.run_check() is not None
    assert not window.toolbar_actions["bundle"].isEnabled()  # busy
    finish(window)
    codes = {i.code for i in window.model.issues}
    assert codes == {Code.UNTRACKED_SOURCE}  # fresh repo, nothing committed
    assert "check: 0 error(s)" in window.log.toPlainText()
    window.apply_rules()
    assert "already match" in window.log.toPlainText()
    assert window.toolbar_actions["bundle"].isEnabled()


def test_bundle_example(
    window: MainWindow, demo_copy: Path, kicad_cli_stub: str
) -> None:
    assert window.open_project(demo_copy)
    assert not window.toolbar_actions["rules"].isEnabled()  # no kifab.toml
    window.skip_check.setChecked(True)
    window.run_bundle()
    finish(window)
    result = window.last_bundle
    assert result is not None and result.ok
    log = window.log.toPlainText()
    assert "gerbers" in log and "bundle ok" in log
    assert (demo_copy / "build" / "demo-board_gerbers.zip").is_file()
    assert window.toolbar_actions["build"].isEnabled()
    window.severity.setCurrentIndex(2)  # errors + warnings: the notes are hidden
    assert window.filter.rowCount() == 0 < window.model.rowCount()


def test_bundle_failure_is_shown(
    window: MainWindow, demo_copy: Path, kicad_cli_stub: str
) -> None:
    window.open_project(demo_copy)
    window.run_bundle()  # check errors: the example uses stock libraries
    finish(window)
    assert window.last_bundle is not None and not window.last_bundle.ok
    assert window.model.count(Severity.ERROR) > 0
    assert "bundle FAILED" in window.log.toPlainText()


def test_errors_go_to_a_message_box(window: MainWindow, tmp_path: Path) -> None:
    assert not window.open_project(tmp_path / "missing")
    assert not window.create_project(
        NewProjectRequest(tmp_path, "", "jlcpcb", 2, False, False)
    )
    messages = window.messages  # type: ignore[attr-defined]  # set by the fixture
    assert len(messages) == 2 and "does not exist" in messages[0]


def test_new_project_dialog(app: QApplication, tmp_path: Path) -> None:
    dialog = NewProjectDialog(start=tmp_path)
    dialog.name.setText(" board ")
    request = dialog.request()
    assert request.directory == tmp_path / "board"
    assert request.fab == "jlcpcb" and request.layers == 2 and request.git


def wait_search(window: MainWindow, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while window.parts.searching:
        assert time.monotonic() < deadline, "search did not finish"
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()


def test_parts_search_add_sync(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import FIXTURES
    from tests.helpers import schematic, symbol

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    assert window.create_project(
        NewProjectRequest(tmp_path, "p", "jlcpcb", 2, git=False, ci=False)
    )
    parts = window.parts
    assert parts.add_button.isEnabled()
    parts.query.setText("330R 0603")
    assert parts.timer.isActive()  # search-as-you-type waits for a pause
    parts.basic_only.setChecked(True)
    parts.search_now()
    parts.search_now()  # a second request while one runs is queued, not lost
    wait_search(window)
    assert [h.id for h in parts.model.hits] == ["C900001"]
    assert parts.model.data(parts.model.index(0, 1)) == "Basic"
    assert "1 of 1 parts" in parts.status.text()
    parts.table.selectRow(0)
    assert parts.add_selected() is not None
    finish(window)
    assert (
        "added C900001: jlcpcb:KF-R330-0603, jlcpcb:R0603" in window.log.toPlainText()
    )
    assert parts.model.data(parts.model.index(0, 7)) == "in project"
    project = window.project
    assert project is not None
    project.kicad.sch.write_text(
        schematic(
            symbol("U1", "C900005", project="p"), symbol("R1", "C900001", project="p")
        )
    )
    monkeypatch.setattr("kifab.gui.parts.sync_parts", _no_delay_sync)
    assert parts.sync() is not None
    finish(window)
    assert "sync: 1 vendored, 1 already vendored, 0 failed" in window.log.toPlainText()
    parts.query.setText("xyzzy-no-such-part")
    parts.search_now()
    wait_search(window)
    assert parts.model.hits == [] and "nothing found" in parts.status.text()


def _no_delay_sync(project: object, **kwargs: object) -> object:
    from kifab.core.library import sync_parts

    kwargs["delay"] = 0
    return sync_parts(project, **kwargs)  # type: ignore[arg-type]  # forwarded as given


def test_detail_record_order_and_tag(
    window: MainWindow,
    tmp_path: Path,
    kicad_cli_stub: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import FIXTURES
    from tests.test_bundle import make_project

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    project = make_project(tmp_path)
    git = GitCli()
    git.init(project.directory)
    subprocess.run(["git", "-C", str(project.directory), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(project.directory),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    assert window.open_project(project.directory)
    assert not window.toolbar_actions["order"].isEnabled()
    window.stock.setChecked(True)
    window.boards.setValue(3)
    window.run_bundle()
    finish(window)
    result = window.last_bundle
    assert result is not None and result.ok and result.availability is not None
    assert "stock" in window.log.toPlainText()
    window.table.selectRow(0)
    assert window.detail.text()
    assert window.toolbar_actions["order"].isEnabled()
    window.record_order()
    assert "recorded orders/" in window.log.toPlainText()
    assert window.create_tag("v9.9")
    assert "v9.9" in git.tags_at_head(project.directory)
    assert not window.create_tag("v9.9")  # exists already
