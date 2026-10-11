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
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
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
