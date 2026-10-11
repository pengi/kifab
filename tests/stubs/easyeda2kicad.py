"""Stand-in for ``python -m easyeda2kicad`` used by the tests (no network).

Writes the captured output of ``tests/fixtures/easyeda2kicad_output/<id>/`` to the
requested ``--output``, like the real tool with ``--full``. Unknown parts fail
like the real tool when EasyEDA has no data; ``STUB_E2K_FAIL=network|tls``
fails like a blocked connection or an unverifiable certificate (the log lines
follow easyeda2kicad 1.0.1's wording). ``STUB_E2K_THROTTLE=<file>:<n>`` makes
the first n calls fail with HTTP 429 (the file counts calls).
``STUB_E2K_LOG=<file>`` records each call.
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
    fail = os.environ.get("STUB_E2K_FAIL", "")
    throttle = os.environ.get("STUB_E2K_THROTTLE")
    if throttle:
        counter, limit = throttle.rsplit(":", 1)
        calls = Path(counter)
        done = int(calls.read_text()) if calls.is_file() else 0
        calls.write_text(str(done + 1))
        if done < int(limit):
            fail = "throttle"
    if "--debug" in args:
        print("[DEBUG] Using system default SSL certificates")
    if fail == "network":
        print(
            "[ERROR] API request failed: <urlopen error [Errno -2] Name or service "
            "not known>"
        )
    elif fail == "tls":
        print(
            "[ERROR] API request failed: <urlopen error [SSL: "
            "CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get "
            "local issuer certificate (_ssl.c:1006)>"
        )
    elif fail == "throttle":
        print("[ERROR] API request failed: HTTP Error 429: Too Many Requests")
    elif not captured.is_dir() and "--debug" in args:
        print("[DEBUG] {'success': False, 'code': 404, 'result': None}")  # invented
    if fail or not captured.is_dir():
        print(f"[ERROR] Failed to fetch data from EasyEDA API for part {lcsc}")
        return 1
    for item in captured.iterdir():
        if item.name.startswith("easyeda"):
            target = out.parent / (out.name + item.name[len("easyeda") :])
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy(item, target)
    print(f"[INFO] Created Kicad symbol for ID : {lcsc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
