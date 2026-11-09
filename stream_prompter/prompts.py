"""Prompt sets: every .txt file in the sets folder is one set, one prompt per line."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PromptSet:
    key: str                 # file name without .txt; used to remember on/off
    name: str                # display name
    path: Path
    prompts: list[str] = field(default_factory=list)
    error: str | None = None


def display_name(stem: str) -> str:
    """hot_takes -> Hot Takes; names with capitals are kept as typed."""
    name = " ".join(stem.replace("_", " ").split())
    if not name:
        return stem
    return name.title() if name.islower() else name


def decode_text(raw: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with BOM, else Windows ANSI."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def parse_prompts(text: str) -> list[str]:
    prompts, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line in seen:
            continue
        seen.add(line)
        prompts.append(line)
    return prompts


def read_set(path) -> PromptSet:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as e:
        return PromptSet(path.stem, display_name(path.stem), path, error=str(e))
    return PromptSet(path.stem, display_name(path.stem), path, parse_prompts(decode_text(raw)))


def _set_files(folder: Path) -> list[Path]:
    return [p for p in folder.iterdir()
            if p.suffix.lower() == ".txt" and not p.name.startswith(".") and p.is_file()]


def load_sets(folder) -> list[PromptSet]:
    try:
        files = _set_files(Path(folder))
    except OSError:
        return []
    return sorted((read_set(p) for p in files), key=lambda s: (s.name.lower(), s.key))


def folder_signature(folder):
    """Changes whenever a set file is added, removed or edited. None if the folder is missing."""
    try:
        sig = []
        for p in _set_files(Path(folder)):
            st = p.stat()
            sig.append((p.name, st.st_mtime_ns, st.st_size))
        return tuple(sorted(sig))
    except OSError:
        return None


class _Rotation:
    """Shuffled order; no item repeats until every item has been used."""

    def __init__(self, items):
        self.items = list(items)
        self.queue = []

    def next(self, avoid=None):
        if not self.queue:
            self.queue = self.items[:]
            random.shuffle(self.queue)
            if len(self.queue) > 1 and self.queue[-1] == avoid:
                self.queue[0], self.queue[-1] = self.queue[-1], self.queue[0]
        return self.queue.pop()


class PromptPicker:
    """Picks prompts from the sets that are switched on.

    Mixed (default): every distinct prompt across the enabled sets is equally likely,
    so bigger sets come up more often.
    Balanced: the enabled sets take turns, so a small set comes up as often as a big one.
    In both modes a prompt is not repeated until the rest of its pool has been shown,
    and switching sets on or off does not reset that history.
    """

    def __init__(self):
        self.balanced = False
        self._contents: dict[str, tuple[str, ...]] = {}
        self._pool: list[tuple[str, str]] = []
        self._queue: list[tuple[str, str]] = []
        self._used: set[str] = set()
        self._set_rotations: dict[str, _Rotation] = {}
        self._turns: _Rotation | None = None
        self._last = None
        self._last_key = None

    @property
    def total(self) -> int:
        return len(self._pool)

    @property
    def set_count(self) -> int:
        return len(self._contents)

    def update(self, sets, balanced: bool = False) -> None:
        contents = {s.key: tuple(s.prompts) for s in sets if s.prompts}
        if contents == self._contents and balanced == self.balanced:
            return
        self._set_rotations = {
            k: self._set_rotations[k]
            if k in self._set_rotations and self._contents.get(k) == v else _Rotation(v)
            for k, v in contents.items()
        }
        self._contents = contents
        self.balanced = balanced
        self._turns = _Rotation(sorted(contents)) if contents else None
        pool = {}
        for key in sorted(contents):
            for prompt in contents[key]:
                pool.setdefault(prompt, key)
        self._pool = list(pool.items())
        self._used &= set(pool)
        self._queue = []

    def next(self):
        """Return (prompt, set_key), or None when no enabled set has prompts."""
        if not self._pool:
            return None
        if self.balanced:
            key = self._turns.next(avoid=self._last_key)
            rotation = self._set_rotations[key]
            prompt = rotation.next(avoid=self._last)
            if prompt == self._last and len(rotation.items) > 1:
                prompt = rotation.next()
            item = (prompt, key)
        else:
            if not self._queue:
                fresh = [it for it in self._pool if it[0] not in self._used]
                if not fresh:
                    self._used.clear()
                    fresh = list(self._pool)
                random.shuffle(fresh)
                if len(fresh) > 1 and fresh[-1][0] == self._last:
                    fresh[0], fresh[-1] = fresh[-1], fresh[0]
                self._queue = fresh
            item = self._queue.pop()
            self._used.add(item[0])
        self._last, self._last_key = item
        return item
