import sys
from pathlib import Path

import pytest

from kifab.core.desktop import desktop_entry, install_desktop, uninstall_desktop
from kifab.core.kicad_plugin import PluginError
from kifab.gui.app import main

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux")


@linux_only
def test_install_and_uninstall(tmp_path: Path) -> None:
    gui = tmp_path / "bin" / "kifab-gui"
    done = install_desktop(tmp_path / "share", kifab_gui=gui)
    text = done.desktop_file.read_text()
    assert text == desktop_entry(gui, done.icon_file)  # deterministic
    assert f'Exec="{gui}" %f' in text
    assert f"Icon={done.icon_file}" in text
    assert done.icon_file.read_bytes().startswith(b"<svg")
    assert uninstall_desktop(tmp_path / "share") == (
        done.desktop_file,
        done.icon_file,
    )
    assert uninstall_desktop(tmp_path / "share") == ()


def test_exec_quotes_awkward_paths() -> None:
    entry = desktop_entry(Path('/a b/"x"/kifab-gui'), Path("/i.svg"))
    assert 'Exec="/a b/\\"x\\"/kifab-gui" %f' in entry


@linux_only
def test_flag_uses_xdg_data_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert main(["--install-desktop"]) == 0
    assert (tmp_path / "applications" / "kifab.desktop").is_file()
    assert main(["--uninstall-desktop"]) == 0
    assert not (tmp_path / "applications" / "kifab.desktop").exists()
    assert "removed" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform.startswith("linux"), reason="non-Linux")
def test_refused_off_linux(tmp_path: Path) -> None:
    with pytest.raises(PluginError):
        install_desktop(tmp_path)
