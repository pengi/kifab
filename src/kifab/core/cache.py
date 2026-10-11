"""kifab's per-user cache directory (shared by the CLI and the GUI).

Holds downloads that are slow or rate-limited to repeat, such as fetched parts.
Deleting it is always safe. ``$KIFAB_CACHE`` overrides the location (tests).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ENV_CACHE = "KIFAB_CACHE"


def user_cache_dir(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    if env.get(ENV_CACHE):
        return Path(env[ENV_CACHE])
    home = Path.home()
    if sys.platform == "win32":
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / "kifab"
    if sys.platform == "darwin":
        return home / "Library" / "Caches" / "kifab"
    return Path(env.get("XDG_CACHE_HOME") or home / ".cache") / "kifab"
