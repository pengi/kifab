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
from PySide6.QtWidgets import QApplication, QMessageBox, QTableView  # noqa: E402

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
    # the search hit's description went into the symbol and shows in the library
    lib_row = parts.library_model.row_for("C900001")
    assert parts.library_model.data(parts.library_model.index(lib_row, 6)) == (
        "330R 0603 Chip Resistor (synthetic test part)"
    )
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


def wait_detail(window: MainWindow, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while window.parts.detail.busy:
        assert time.monotonic() < deadline, "detail did not finish loading"
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()


def test_start_page_recent_and_close(
    app: QApplication, window: MainWindow, demo_copy: Path, gui_home: Path
) -> None:
    from kifab.gui.settings import GuiSettings

    assert window.pages.currentWidget() is window.start_page
    menus = [a.text() for a in window.menuBar().actions()]
    assert menus == ["&File", "&Project"]
    assert not window.toolbar_actions["close"].isEnabled()
    assert not window.buttons["check"].isEnabled()
    assert window.open_project(demo_copy)
    assert window.pages.currentWidget() is window.project_page
    assert window.tabs.currentWidget() is window.parts  # parts first
    assert window.buttons["check"].isEnabled()  # follows its action
    assert window.buttons["bundle"].text() == "Bundle for the fab"
    pro = demo_copy / "demo-board.kicad_pro"
    assert GuiSettings.load(gui_home / "settings.json").recent == [pro]
    window.close_project()
    assert window.project is None
    assert window.pages.currentWidget() is window.start_page
    assert window.start_page.recent.count() == 1
    assert "demo-board" in window.start_page.recent.item(0).text()
    # a new window remembers it, and opens it from the list
    again = MainWindow()
    assert again.settings.recent == [pro]
    again.start_page.recent.itemActivated.emit(again.start_page.recent.item(0))
    assert again.project is not None and again.project.name == "demo-board"
    again.close()
    # a project that went away is dropped from the list
    missing = demo_copy.parent / "gone" / "gone.kicad_pro"
    window.settings.add_recent(missing)
    assert not window.open_recent(missing)
    assert missing not in GuiSettings.load(gui_home / "settings.json").recent
    window._fill_recent_menu()
    labels = [a.text() for a in window.recent_menu.actions()]
    assert any("demo-board" in label for label in labels) and "Clear list" in labels
    window.clear_recent()
    assert GuiSettings.load(gui_home / "settings.json").recent == []


def test_settings_file(tmp_path: Path) -> None:
    from kifab.gui.settings import MAX_RECENT, GuiSettings, cache_dir, config_dir

    path = tmp_path / "s" / "settings.json"
    settings = GuiSettings.load(path)
    assert settings.recent == []
    for i in range(MAX_RECENT + 3):
        settings.add_recent(tmp_path / f"p{i}.kicad_pro")
    settings.add_recent(tmp_path / "p5.kicad_pro")
    settings.save()
    loaded = GuiSettings.load(path)
    assert len(loaded.recent) == MAX_RECENT and loaded.recent[0].name == "p5.kicad_pro"
    path.write_text("{not json")
    assert GuiSettings.load(path).recent == []
    assert config_dir({"KIFAB_GUI_HOME": "/x"}) == Path("/x")
    assert cache_dir({"KIFAB_GUI_HOME": "/x"}) == Path("/x/cache")
    if os.name == "posix" and not str(config_dir({})).startswith(
        str(Path.home() / "Library")
    ):
        assert config_dir({"XDG_CONFIG_HOME": "/c"}) == Path("/c/kifab-gui")


def test_cli_does_not_touch_gui_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gui_home: Path
) -> None:
    import sys

    from kifab.cli.main import main
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    d = tmp_path / "p"
    assert main(["init", "p", "jlcpcb", "--dir", str(d), "--no-git"]) == 0
    assert main(["component", "add", "C900001", "--project", str(d)]) == 0
    assert main(["component", "list", str(d)]) == 0
    assert list(gui_home.iterdir()) == []
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, kifab.cli.main; "
            "print(any(m.startswith('kifab.gui') for m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.strip() == "False"


def _library_project(window: MainWindow, tmp_path: Path) -> Path:
    from tests.helpers import board, footprint, schematic, symbol

    assert window.create_project(
        NewProjectRequest(tmp_path, "p", "jlcpcb", 2, git=False, ci=False)
    )
    project = window.project
    assert project is not None
    from kifab.core.library import vendor_part

    for part in ("C900001", "C900004", "C900005"):
        vendor_part(project, part)
    project.kicad.sch.write_text(
        schematic(
            symbol("R1", "C900001"),
            symbol("R2", "C900001"),
            symbol("U1", "C900005"),
            symbol("C1", "C900003"),
        )
    )
    project.kicad.pcb.write_text(board(footprint("R1", "jlcpcb:R0603")))
    window.parts.refresh()
    return project.directory


def test_library_list_and_remove(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    directory = _library_project(window, tmp_path)
    parts = window.parts
    model = parts.library_model
    rows = {
        model.data(model.index(r, 0)): [
            model.data(model.index(r, c)) for c in range(model.columnCount())
        ]
        for r in range(model.rowCount())
    }
    assert rows["C900001"][1:4] == ["in library", "2", "R1,R2"]
    assert rows["C900004"][1:3] == ["in library, unused", "0"]
    assert rows["C900003"][1:4] == ["not vendored", "1", "C1"]
    assert "3 parts, 2 in use; 1 used but not vendored" in parts.library_status.text()
    assert not parts.remove_button.isEnabled()

    def select(part_id: str) -> None:
        parts.library_table.selectRow(model.row_for(part_id))
        wait_detail(window)

    select("C900004")
    assert parts.remove_button.isEnabled()
    assert parts.remove_selected()
    assert not (directory / "lib/jlcpcb/jlcpcb.pretty/C0805.kicad_mod").exists()
    assert model.row_for("C900004") == -1
    assert "removed C900004" in window.log.toPlainText()
    # a used part: the user is asked
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answers.pop(0))
    select("C900005")
    assert not parts.remove_selected()  # said no
    assert model.row_for("C900005") >= 0
    assert parts.remove_selected()  # said yes
    assert "still referenced by U1" in window.log.toPlainText()
    # the not-vendored row can be added from the library list
    select("C900003")
    assert not parts.remove_button.isEnabled()
    assert parts.add_selected() is not None
    finish(window)
    row = model.row_for("C900003")
    assert model.data(model.index(row, 1)) == "in library"


def test_part_detail(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gui_home: Path
) -> None:
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    _library_project(window, tmp_path)
    parts = window.parts
    detail = parts.detail
    parts.query.setText("KF-MOD-1")
    parts.search_now()
    wait_search(window)
    parts.table.selectRow(0)
    wait_detail(window)
    info = {}
    for r in range(detail.info.rowCount()):
        key, value = detail.info.item(r, 0), detail.info.item(r, 1)
        assert key is not None and value is not None
        info[key.text()] = value.text()
    assert info["MPN"] == "KF-MOD-1" and info["Stock"] == "1,200"
    assert info["Type"].startswith("Extended") and "Price 1-999" in info
    assert info["Used by"] == "U1"  # it is in the project library too
    assert "C900005.pdf" in detail.links.text() and "Datasheet" in detail.links.text()
    assert detail.photo.pixmap() is not None and not detail.photo.pixmap().isNull()
    # vendored: drawn from the project library straight away
    assert detail.footprint_view.drawing is not None
    assert "MOD-SMD_KF1" in detail.footprint_caption.text()
    assert detail.symbol_view.drawing is not None
    assert detail.fetch_button.isHidden()
    # not vendored: fetched on request into the GUI cache, not the project
    parts.query.setText("100nF 0603")
    parts.search_now()
    wait_search(window)
    ids = [h.id for h in parts.model.hits]
    parts.table.selectRow(ids.index("C900007"))
    wait_detail(window)
    assert detail.photo.text() == "no photo from the fab"
    assert not detail.fetch_button.isHidden()
    assert detail.footprint_view.drawing is None
    detail.fetch_drawings()
    wait_detail(window)
    assert detail.footprint_view.message.startswith(
        "fetch failed"
    )  # no output captured
    parts.table.selectRow(ids.index("C900003"))
    wait_detail(window)
    detail.fetch_drawings()
    wait_detail(window)
    assert detail.footprint_view.drawing is not None
    assert (gui_home / "cache" / "parts" / "C900003" / "C0603.kicad_mod").is_file()
    project = window.project
    assert project is not None
    assert not (project.directory / "lib/jlcpcb/jlcpcb.pretty/C0603.kicad_mod").exists()
    # the cache serves it next time without fetching
    parts.table.selectRow(ids.index("C900007"))
    parts.table.selectRow(ids.index("C900003"))
    assert detail.footprint_view.drawing is not None and detail.fetch_button.isHidden()
    # a library row without catalogue facts at hand is looked up
    parts.library_table.selectRow(parts.library_model.row_for("C900001"))
    wait_detail(window)
    assert detail.hit is not None and detail.hit.mpn == "KF-R330-0603"


def test_quick_search_in_the_search_box(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    parts = window.parts
    parts.query.setText("R 4k7 0603")
    parts.search_now()
    wait_search(window)
    assert [h.id for h in parts.model.hits] == ["C900002", "C900011"]
    assert parts.status.text() == "2 resistor(s) 4.7kΩ 0603"
    parts.alternatives.setChecked(True)
    parts.search_now()
    wait_search(window)
    ids = [h.id for h in parts.model.hits]
    assert ids == ["C900002", "C900011", "C900009", "C900010"]
    note = parts.model.data(parts.model.index(2, 7))
    assert note == "alt. 5.1kΩ (+8.5%)"
    assert "+ 2 alternative(s)" in parts.status.text()


def test_find_and_refresh_library_part(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kifab.core.library import vendor_part
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    assert window.create_project(
        NewProjectRequest(tmp_path, "p", "jlcpcb", 2, git=False, ci=False)
    )
    project = window.project
    assert project is not None
    vendor_part(project, "C900001")  # no description yet
    vendor_part(project, "C900002")  # its R0603 differs: the first copy is kept
    parts = window.parts
    parts.refresh()
    model = parts.library_model
    assert not parts.find_button.isEnabled() and not parts.refresh_button.isEnabled()
    # Search at fab: the part is looked up and selected in the results
    parts.library_table.selectRow(model.row_for("C900001"))
    assert parts.find_button.isEnabled() and parts.refresh_button.isEnabled()
    assert parts.find_selected() is not None
    wait_search(window)
    hit = parts.selected()
    assert hit is not None and hit.id == "C900001"
    wait_detail(window)
    assert parts.detail.hit is not None and parts.detail.hit.id == "C900001"
    # Refresh: the description is filled in
    parts.library_table.selectRow(model.row_for("C900001"))
    assert parts.refresh_selected() is not None
    finish(window)
    row = model.row_for("C900001")
    assert str(model.data(model.index(row, 6))).startswith("330R 0603")
    # Refresh where the fab's footprint differs: declined, then accepted
    r0603 = project.directory / "lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod"
    before = r0603.read_text()
    parts.library_table.selectRow(model.row_for("C900002"))
    parts.refresh_selected(replace=False)
    finish(window)
    assert r0603.read_text() == before
    assert "kept the vendored files of C900002" in window.log.toPlainText()
    parts.library_table.selectRow(model.row_for("C900002"))
    parts.refresh_selected(replace=True)
    finish(window)  # the refresh, then the replacing fetch it starts
    finish(window)
    assert r0603.read_text() != before and "4k7" in r0603.read_text()
    assert (
        "replaced lib/jlcpcb/jlcpcb.pretty/R0603.kicad_mod" in window.log.toPlainText()
    )


def test_parts_tables_sort_by_column(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    parts = window.parts
    parts.alternatives.setChecked(True)
    parts.query.setText("R 4k7 0603")
    parts.search_now()
    wait_search(window)

    def shown(table: QTableView) -> list[str]:
        model = table.model()
        return [str(model.index(r, 0).data()) for r in range(model.rowCount())]

    ranking = ["C900002", "C900011", "C900009", "C900010"]
    assert shown(parts.table) == ranking  # kifab's ranking until a title is clicked
    parts.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # stock, as numbers
    wait_search(window)  # asked the fab again, sorted by stock
    assert shown(parts.table) == ["C900002", "C900009", "C900010", "C900011"]
    parts.table.sortByColumn(3, Qt.SortOrder.AscendingOrder)  # price
    assert shown(parts.table)[0] == "C900009"  # $0.0022
    assert parts.select_hit("C900010")
    hit = parts.selected()
    assert hit is not None and hit.id == "C900010"
    wait_detail(window)
    assert parts.detail.part_id == "C900010"  # the selected row, not row 0's part
    # the project library sorts too
    _library_project(window, tmp_path)
    parts.library_table.sortByColumn(2, Qt.SortOrder.DescendingOrder)  # uses
    assert shown(parts.library_table)[0] == "C900001"  # R1, R2 (and R1 on the board)
    assert parts.select_library("C900004")
    row = parts.selected_library_row()
    assert row is not None and row.id == "C900004"
    wait_detail(window)


def test_fab_sorts_truncated_results_and_more_load(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kifab.core.parts import SearchSort
    from tests.conftest import FIXTURES

    monkeypatch.setenv("KIFAB_OFFLINE", str(FIXTURES))
    parts = window.parts
    parts.query.setText("100nF 0603")  # 2 of 1534 parts: truncated
    parts.search_now()
    wait_search(window)
    assert [h.id for h in parts.model.hits] == ["C900007", "C900003"]
    assert parts.more_button.isEnabled()
    parts.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    assert parts.sort is SearchSort.STOCK and parts.searching
    wait_search(window)
    assert [h.id for h in parts.model.hits] == ["C900003", "C900007"]  # the fab's order
    assert (
        parts.status.text() == "2 of 1534 parts for '100nF 0603', most in stock first"
    )
    assert parts.load_more() is not None
    wait_search(window)
    assert [h.id for h in parts.model.hits] == [
        "C900003",
        "C900007",
        "C900012",
        "C900004",
    ]
    assert parts.status.text().startswith("4 of 1534 parts")
    # ascending stock sorts what is loaded, without asking the fab
    parts.table.sortByColumn(2, Qt.SortOrder.AscendingOrder)
    assert not parts.searching and parts.sort is SearchSort.STOCK
