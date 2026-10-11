"""kifab-gui's own settings (recent projects) and cache (previews).

These belong to the GUI alone: the ``kifab`` command line never reads or writes
them, and nothing here is stored in a project. ``$KIFAB_GUI_HOME`` puts both
in one directory (tests use it); otherwise the platform's usual places are used.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENV_HOME = "KIFAB_GUI_HOME"
APP = "kifab-gui"
SETTINGS_NAME = "settings.json"
MAX_RECENT = 10


def _home() -> Path:
    return Path.home()


def config_dir(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    if env.get(ENV_HOME):
        return Path(env[ENV_HOME])
    if sys.platform == "win32":
        return Path(env.get("APPDATA") or _home() / "AppData" / "Roaming") / APP
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / APP
    return Path(env.get("XDG_CONFIG_HOME") or _home() / ".config") / APP


def cache_dir(env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    if env.get(ENV_HOME):
        return Path(env[ENV_HOME]) / "cache"
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or _home() / "AppData" / "Local"
        return Path(base) / APP / "cache"
    if sys.platform == "darwin":
        return _home() / "Library" / "Caches" / APP
    return Path(env.get("XDG_CACHE_HOME") or _home() / ".cache") / APP


@dataclass
class GuiSettings:
    recent: list[Path] = field(default_factory=list)  # most recent first
    path: Path | None = None  # where it is saved; None: not saved

    def add_recent(self, project: Path) -> None:
        project = Path(project)
        self.recent = [project] + [p for p in self.recent if p != project]
        del self.recent[MAX_RECENT:]

    def forget(self, project: Path) -> None:
        self.recent = [p for p in self.recent if p != Path(project)]

    def to_json(self) -> dict[str, object]:
        return {"version": 1, "recent": [str(p) for p in self.recent]}

    def save(self) -> None:
        """Write the settings; failing to is not worth bothering the user."""
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.to_json(), indent=2) + "\n", "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    @classmethod
    def load(cls, path: Path | None = None) -> GuiSettings:
        """The saved settings; defaults when the file is missing or unreadable."""
        path = path or config_dir() / SETTINGS_NAME
        settings = cls(path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        recent = data.get("recent") if isinstance(data, dict) else None
        if isinstance(recent, list):
            settings.recent = [Path(p) for p in recent if isinstance(p, str)][
                :MAX_RECENT
            ]
        return settings
