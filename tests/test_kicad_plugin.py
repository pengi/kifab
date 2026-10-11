import json
import re
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kifab.cli.main import main
from kifab.core.kicad_plugin import (
    PluginError,
    default_plugin_dir,
    install_plugin,
    manifest,
    uninstall_plugin,
)
from kifab.gui.app import parse_args


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="Linux paths")
def test_default_plugin_dir_is_lowercase_kicad(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_plugin_dir() == tmp_path / ".local/share/kicad/10.0/plugins"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_plugin_dir() == tmp_path / "data/kicad/10.0/plugins"


FAKE_KIPY = """
from types import SimpleNamespace
class DocumentType:
    DOCTYPE_PCB = 1
    DOCTYPE_SCHEMATIC = 2
class KiCad:
    def get_open_documents(self, kind):
        if kind == DocumentType.DOCTYPE_PCB:
            return [SimpleNamespace(project=SimpleNamespace(path="{project}"))]
        return []
"""


def test_manifest_follows_kicads_plugin_schema() -> None:
    """The rules of KiCad's api.v1 plugin schema that this manifest touches."""
    data = manifest()
    for key in ("identifier", "name", "description", "runtime", "actions"):
        assert key in data
    assert re.fullmatch(
        r"[a-zA-Z][-_a-zA-Z0-9.]{0,98}[a-zA-Z0-9]", str(data["identifier"])
    )
    runtime = data["runtime"]
    assert isinstance(runtime, dict) and runtime["type"] in ("python", "exec")
    actions = data["actions"]
    assert isinstance(actions, list) and actions
    scopes = {"pcb", "schematic", "footprint", "symbol", "project_manager"}
    for action in actions:
        assert isinstance(action, dict)
        for key in ("identifier", "name", "description", "entrypoint"):
            assert isinstance(action[key], str) and action[key]
        assert re.fullmatch(
            r"[a-zA-Z][-_a-zA-Z0-9.]{0,48}[a-zA-Z0-9]", action["identifier"]
        )
        assert set(action["scopes"]) <= scopes
        for icon in action["icons-light"] + action["icons-dark"]:
            assert icon.endswith(".png")
        json.dumps(action)  # plain JSON only


def fake_gui(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "gui-args.txt"
    gui = tmp_path / "kifab-gui"
    gui.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"open({str(log)!r}, 'w').write('\\n'.join(sys.argv[1:]))\n"
    )
    gui.chmod(0o755)
    return gui, log


def wait_for(path: Path) -> str:
    for _ in range(200):
        if path.is_file() and path.read_text():
            return path.read_text()
        time.sleep(0.02)
    raise AssertionError(f"{path} was not written")


def test_install_and_run_actions(tmp_path: Path) -> None:
    gui, log = fake_gui(tmp_path)
    result = install_plugin(tmp_path / "plugins", kifab_gui=gui)
    d = result.directory
    assert d == tmp_path / "plugins" / "kifab"
    for name in ("plugin.json", "requirements.txt", "open_kifab.py"):
        assert (d / name).is_file()
    assert (d / "icon-light.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    # run the action as KiCad would, with a fake kicad-python that reports a project
    kipy = tmp_path / "site" / "kipy"
    (kipy / "proto" / "common").mkdir(parents=True)
    (kipy / "__init__.py").write_text(FAKE_KIPY.format(project="/boards/hat"))
    (kipy / "proto" / "__init__.py").write_text("")
    (kipy / "proto" / "common" / "__init__.py").write_text("")
    (kipy / "proto" / "common" / "types.py").write_text(
        "from kipy import DocumentType\n"
    )
    env = {**os.environ, "PYTHONPATH": str(tmp_path / "site")}
    env.pop("KIFAB_PROJECT", None)
    assert not (d / "add_part.py").exists()
    assert len(json.loads((d / "plugin.json").read_text())["actions"]) == 1
    subprocess.run([sys.executable, str(d / "open_kifab.py")], check=True, env=env)
    assert wait_for(log).splitlines() == ["/boards/hat"]
    log.unlink()
    # without kicad-python: no project argument, kifab-gui opens empty
    env.pop("PYTHONPATH")
    subprocess.run([sys.executable, str(d / "open_kifab.py")], check=True, env=env)
    time.sleep(0.2)
    assert not log.exists() or log.read_text() == ""


def test_install_refuses_and_uninstall(tmp_path: Path) -> None:
    gui, _ = fake_gui(tmp_path)
    plugins = tmp_path / "plugins"
    install_plugin(plugins, kifab_gui=gui)
    with pytest.raises(PluginError, match="--force"):
        install_plugin(plugins, kifab_gui=gui)
    install_plugin(plugins, kifab_gui=gui, force=True)
    assert uninstall_plugin(plugins) == plugins / "kifab"
    assert uninstall_plugin(plugins) is None
    (plugins / "kifab").mkdir()
    with pytest.raises(PluginError, match="not a kifab plugin"):
        install_plugin(plugins, kifab_gui=gui)
    with pytest.raises(PluginError, match="not a kifab plugin"):
        uninstall_plugin(plugins)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = str(tmp_path / "plugins")
    assert main(["kicad-plugin", "install", "--dest", dest]) == 0
    out = capsys.readouterr().out
    assert "IPC API" in out and "kifab-gui" in out
    assert main(["kicad-plugin", "install", "--dest", dest]) == 1
    assert main(["kicad-plugin", "uninstall", "--dest", dest]) == 0


def test_gui_args() -> None:
    args = parse_args(["/x", "--tab", "parts"])
    assert args.project == Path("/x") and args.tab == "parts"
    assert parse_args([]).project is None


def test_platform_choice() -> None:
    from kifab.gui.platform import choose_platform

    def missing() -> str | None:
        return "libxcb-icccm.so.4"

    def fine() -> str | None:
        return None

    x11 = {"DISPLAY": ":0"}
    assert choose_platform(x11, fine, "linux") == choose_platform({}, fine, "darwin")
    error = choose_platform(x11, missing, "linux").error
    assert error and "libxcb-icccm.so.4 is missing" in error
    assert "sudo apt install libxcb-cursor0" in error
    wayland = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"}
    assert choose_platform(wayland, missing, "linux").platform == "wayland"
    assert choose_platform({}, fine, "linux").error  # no display at all
    assert choose_platform({"QT_QPA_PLATFORM": "xcb"}, missing, "linux").error is None


def test_xcb_plugin_check_names_the_library(tmp_path: Path) -> None:
    from kifab.gui.platform import missing_xcb_library

    broken = tmp_path / "libqxcb.so"
    broken.write_bytes(b"not a shared object")
    assert missing_xcb_library(broken)  # some loader error, not a crash
