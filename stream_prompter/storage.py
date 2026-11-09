"""Paths, settings, starter prompt sets and small file helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
BUNDLED_SETS_DIR = PACKAGE_DIR / "default_sets"
ASSETS_DIR = PACKAGE_DIR / "assets"

DEFAULT_SETTINGS = {
    "device": "System default",
    "threshold": -40.0,
    "minutes": 5.0,
    "hide_delay": 0.0,       # seconds the prompt stays after you start talking; -1 = until next prompt
    "sound": "off",          # off | system | builtin:<id> | file:<name>.wav (in the sounds folder)
    "volume": 70.0,          # percent
    "on_top": False,
    "output_path": "",       # "" = <data dir>/current_prompt.txt
    "sets_dir": "",          # "" = <data dir>/prompt_sets
    "disabled_sets": [],     # file names (without .txt) of sets switched off
    "balance_sets": False,
    "seeded_sets": [],       # starter sets already copied once
    "geometry": "",
}


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / "StreamPrompter"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StreamPrompter"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "stream-prompter"


def _valid(value, default) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(default, list):
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, type(default))


def write_text_atomic(path, text: str) -> None:
    """Write via a temp file so readers such as OBS never see a half-written file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        path.write_text(text, encoding="utf-8")


def open_path(path) -> None:
    """Open a file or folder with the system's default app."""
    path = str(path)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - local path chosen by the user
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Storage:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.default_sets_dir = self.data_dir / "prompt_sets"
        self.default_output = self.data_dir / "current_prompt.txt"
        self.sounds_dir = self.data_dir / "sounds"
        self.log_path = self.data_dir / "stream_prompter.log"

    def load_settings(self) -> dict:
        settings = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if isinstance(data, dict):
            for key, default in DEFAULT_SETTINGS.items():
                if key in data and _valid(data[key], default):
                    value = data[key]
                    if isinstance(default, float):
                        value = float(value)
                    elif isinstance(value, list):
                        value = list(value)
                    settings[key] = value
            # 1.0 had an on/off "clear prompt when I talk" switch; off meant "until next prompt".
            if "hide_delay" not in data and data.get("clear_on_speech") is False:
                settings["hide_delay"] = -1.0
            # 1.0.0 had a "beep" switch that played the system bell.
            if "sound" not in data and data.get("beep") is True:
                settings["sound"] = "system"
        return settings

    def save_settings(self, settings: dict) -> None:
        data = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
        write_text_atomic(self.settings_path, json.dumps(data, indent=2))

    def seed_default_sets(self, settings: dict, source: Path | None = None) -> list[str]:
        """Copy starter sets into the default sets folder.

        Each starter file is copied once. Later updates that ship new starter sets add them,
        but a starter set the user deleted is not brought back.
        """
        source = Path(source) if source else BUNDLED_SETS_DIR
        self.default_sets_dir.mkdir(parents=True, exist_ok=True)
        seeded = set(settings.get("seeded_sets", []))
        added = []
        for src in sorted(source.glob("*.txt")):
            if src.name in seeded:
                continue
            dst = self.default_sets_dir / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)
                added.append(src.name)
            seeded.add(src.name)
        settings["seeded_sets"] = sorted(seeded)
        return added
