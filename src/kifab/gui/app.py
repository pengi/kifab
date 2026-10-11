"""``kifab-gui`` entry point."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from kifab.gui.platform import choose_platform


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kifab-gui")
    parser.add_argument("project", nargs="?", type=Path, help="project to open")
    parser.add_argument(
        "--tab",
        choices=("parts", "results"),
        default="parts",
        help="tab to show first (results: check and bundle findings)",
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="install a desktop launcher and icon for kifab-gui (Linux), then exit",
    )
    parser.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="remove the desktop launcher and icon, then exit",
    )
    return parser.parse_args(list(argv))


def _desktop(args: argparse.Namespace) -> int:
    from kifab.core.desktop import install_desktop, uninstall_desktop
    from kifab.core.kicad_plugin import PluginError

    try:
        if args.install_desktop:
            done = install_desktop()
            print(f"installed {done.desktop_file} (runs {done.kifab_gui})")
            print(f"installed {done.icon_file}")
        else:
            for path in uninstall_desktop():
                print(f"removed {path}")
    except PluginError as exc:
        print(f"kifab-gui: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.install_desktop or args.uninstall_desktop:
        return _desktop(args)
    choice = choose_platform(os.environ)
    if choice.error:
        print(f"kifab-gui: {choice.error}", file=sys.stderr)
        return 2
    if choice.platform:
        os.environ["QT_QPA_PLATFORM"] = choice.platform
    try:
        from PySide6.QtWidgets import QApplication

        from kifab.gui.main_window import MainWindow
    except ImportError as exc:
        print(
            f"kifab-gui needs PySide6: pip install 'kifab[gui]' ({exc})",
            file=sys.stderr,
        )
        return 2
    app = QApplication.instance() or QApplication([sys.argv[0]])
    window = MainWindow()
    if args.project is not None:
        window.open_project(args.project)
    if args.tab == "results":
        window.tabs.setCurrentIndex(1)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
