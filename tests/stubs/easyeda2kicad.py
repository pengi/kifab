"""Stand-in for ``python -m easyeda2kicad`` used by the tests (no network).

Writes the captured output of ``tests/fixtures/easyeda2kicad_output/<id>/`` to the
requested ``--output``, like the real tool with ``--full``. Unknown parts fail
like a network error; ``STUB_E2K_LOG=<file>`` records each call.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def main() -> int:
    args = sys.argv[1:]
    log = os.environ.get("STUB_E2K_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(os.getcwd() + " " + " ".join(args) + "\n")
    lcsc = next(a.split("=", 1)[1] for a in args if a.startswith("--lcsc_id="))
    out = Path(args[args.index("--output") + 1])
    captured = FIXTURES / "easyeda2kicad_output" / lcsc
    print("-- easyeda2kicad.py v1.0.1 --")
    if not captured.is_dir():
        print(f"[ERROR] Failed to fetch data from EasyEDA API for part {lcsc}")
        return 1
    for item in captured.iterdir():
        if item.name.startswith("jlcpcb"):
            target = out.parent / (out.name + item.name[len("jlcpcb") :])
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy(item, target)
    print(f"[INFO] Created Kicad symbol for ID : {lcsc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
