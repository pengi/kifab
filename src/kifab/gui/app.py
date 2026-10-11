"""``kifab-gui`` entry point."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kifab-gui")
    parser.add_argument("project", nargs="?", type=Path, help="project to open")
    parser.add_argument("--tab", choices=("issues", "parts"), default="issues")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
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
    if args.tab == "parts":
        window.tabs.setCurrentWidget(window.parts)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
