"""Stream Prompter main window."""

from __future__ import annotations

import argparse
import logging
import math
import re
import shutil
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import APP_NAME, __version__
from .prompts import PromptPicker, display_name, folder_signature, load_sets
from .storage import ASSETS_DIR, Storage, open_path, write_text_atomic
from .theme import (ACCENT, BG, BLUE, BORDER, FG, FIELD, GRAY_BAR, GREEN, MUTED, PANEL,
                    YELLOW, setup_theme)

try:
    from . import audio, sounds
    AUDIO_ERROR = None
except (ImportError, OSError) as e:  # missing package, or PortAudio library missing on Linux
    audio = sounds = None
    AUDIO_ERROR = e

log = logging.getLogger("stream_prompter")

DB_MIN, DB_MAX = -80.0, 0.0
TICK_MS = 100
NOISE_WINDOW_SECONDS = 15
CALIBRATE_SECONDS = 3
CALIBRATE_MARGIN_DB = 8
# How long a prompt stays once you start talking. None = until the next prompt replaces it.
HIDE_STOPS = [(0, "Instantly"), (3, "3 s"), (5, "5 s"), (10, "10 s"), (15, "15 s"), (30, "30 s"),
              (60, "1 min"), (120, "2 min"), (300, "5 min"), (None, "Until next prompt")]
RESCAN_EVERY_TICKS = 20           # look for changed set files every 2 s

NEW_SET_TEMPLATE = (
    "# One prompt per line. Lines starting with # are ignored.\n"
    "# Save the file and Stream Prompter picks up the changes automatically.\n"
    "\n"
)


def ask_string(root, title, prompt, ok_text="OK", topmost=False):
    """Small themed text-input dialog. Returns the text, or None if cancelled."""
    win = tk.Toplevel(root)
    win.withdraw()
    win.title(title)
    win.configure(bg=BG)
    win.transient(root)
    win.resizable(False, False)
    result = {"value": None}

    frame = ttk.Frame(win, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=prompt).pack(anchor="w")
    var = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=var, width=38)
    entry.pack(fill="x", pady=(8, 14))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")

    def ok(*_):
        result["value"] = var.get()
        win.destroy()

    ttk.Button(buttons, text=ok_text, style="Accent.TButton", command=ok).pack(side="right")
    ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
    win.bind("<Return>", ok)
    win.bind("<Escape>", lambda e: win.destroy())

    win.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - win.winfo_reqwidth()) // 2
    y = root.winfo_rooty() + (root.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
    win.attributes("-topmost", topmost)
    win.deiconify()
    entry.focus_set()
    win.grab_set()
    root.wait_window(win)
    return result["value"]


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    return name.strip().strip(".").strip()


class SetList(ttk.Frame):
    """Scrollable list of prompt sets with on/off checkboxes."""

    def __init__(self, parent, on_toggle, on_edit):
        super().__init__(parent, style="Panel.TFrame")
        self.on_toggle = on_toggle
        self.on_edit = on_edit
        self.vars: dict[str, tk.BooleanVar] = {}

        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, height=160, width=320)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.inner.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.bind_all("<Button-4>", self._on_wheel, add="+")
        self.bind_all("<Button-5>", self._on_wheel, add="+")

    def _on_wheel(self, event):
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        while widget is not None and widget is not self:
            widget = getattr(widget, "master", None)
        if widget is None or self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if event.num == 4:
            step = -1
        elif event.num == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")

    def render(self, sets, disabled, folder_missing=False):
        for child in self.inner.winfo_children():
            child.destroy()
        self.vars = {}
        if not sets:
            msg = ("This folder doesn't exist." if folder_missing
                   else "No .txt files in this folder yet.\nClick “New set…” or add files to the folder.")
            ttk.Label(self.inner, text=msg, style="PanelMuted.TLabel", justify="left").grid(
                row=0, column=0, sticky="w", padx=12, pady=12)
            return
        for row, s in enumerate(sets):
            var = tk.BooleanVar(value=s.key not in disabled)
            self.vars[s.key] = var
            ttk.Checkbutton(self.inner, text=s.name, variable=var, style="Panel.TCheckbutton",
                            command=lambda k=s.key: self.on_toggle(k, self.vars[k].get())).grid(
                row=row, column=0, sticky="w", padx=(10, 6), pady=3)
            if s.error:
                text, style = "can't read", "PanelWarn.TLabel"
            elif not s.prompts:
                text, style = "empty", "PanelWarn.TLabel"
            else:
                n = len(s.prompts)
                text, style = f"{n} prompt{'s' if n != 1 else ''}", "PanelMuted.TLabel"
            ttk.Label(self.inner, text=text, style=style).grid(row=row, column=1, sticky="e", padx=6)
            ttk.Button(self.inner, text="Edit", style="Small.TButton",
                       command=lambda p=s.path: self.on_edit(p)).grid(row=row, column=2, padx=(0, 8), pady=2)


class App:
    BAR_TOP, BAR_BOT = 3, 21

    def __init__(self, root, fonts, storage: Storage):
        self.root = root
        self.fonts = fonts
        self.storage = storage
        self.s = storage.load_settings()
        try:
            added = storage.seed_default_sets(self.s)
            if added:
                log.info("Copied starter prompt sets: %s", ", ".join(added))
        except OSError as e:
            log.warning("Couldn't copy starter prompt sets: %s", e)

        self.mon = audio.MicMonitor(self.s["threshold"])
        self.picker = PromptPicker()
        self.sets = []
        self.disabled = set(self.s["disabled_sets"])
        self._sets_sig = ()
        self.devices = {}
        self.paused = False
        self.timer_floor = time.monotonic()
        self.prompt_on_screen = False
        self.blocks_at_prompt = 0
        self.calibrate_start = None
        self._save_job = None
        self._last_minutes = self.s["minutes"] if self.s["minutes"] > 0 else 5.0
        self._state = None
        self._meter_w = 0
        self._ticks = 0

        self.device_var = tk.StringVar(value=self.s["device"])
        self.threshold_var = tk.DoubleVar(value=self.s["threshold"])
        self.minutes_var = tk.StringVar(value=f"{self.s['minutes']:g}")
        self.hide_idx_var = tk.DoubleVar(value=self._hide_index(self.s["hide_delay"]))
        self.hide_text = tk.StringVar()
        self.talk_started = None          # when you first spoke after the current prompt appeared
        self.prompt_set_name = ""
        self.sound_id = self.s["sound"]
        self.sound_label_var = tk.StringVar()
        self.volume_var = tk.DoubleVar(value=min(max(self.s["volume"], 0.0), 100.0))
        self.volume_text = tk.StringVar()
        self._sound_options = []          # [(id, label)]
        self._sound_cache = {}
        self.top_var = tk.BooleanVar(value=self.s["on_top"])
        self.balance_var = tk.BooleanVar(value=self.s["balance_sets"])
        self.output_path_var = tk.StringVar(value=self.s["output_path"] or str(storage.default_output))
        self.sets_dir_var = tk.StringVar(value=str(self.sets_dir))
        self.level_text = tk.StringVar(value="-- dB")
        self.noise_text = tk.StringVar(value="Background noise: --")
        self.thr_text = tk.StringVar()
        self.timer_text = tk.StringVar()
        self.state_text = tk.StringVar()
        self.prompt_var = tk.StringVar()
        self.prompt_src_var = tk.StringVar()
        self.sets_summary = tk.StringVar()
        self.status_text = tk.StringVar()

        self._build_ui()
        self._restore_geometry()
        self._on_threshold()
        self._on_hide()
        self._on_volume()
        self._refresh_sound_list()
        self.minutes_var.trace_add("write", self._changed)
        self._apply_on_top()
        self._rescan_sets(force=True)
        self._refresh_devices(initial=True)
        self._clear_prompt()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._save()
        self._tick()

    # ------------------------------------------------------------------ layout

    def _build_ui(self):
        self.root.title(APP_NAME)
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1, uniform="col")
        outer.columnconfigure(1, weight=1, uniform="col")
        outer.rowconfigure(1, weight=1)

        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.columnconfigure(0, weight=1)
        self._build_mic(left).grid(row=0, column=0, sticky="ew")
        self._build_options(left).grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self._build_sets(outer).grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._build_live(outer).grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))

        bottom = ttk.Frame(outer)
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(bottom, textvariable=self.status_text, style="Muted.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")
        ttk.Label(bottom, text=f"v{__version__}", style="Small.TLabel").grid(row=0, column=1, sticky="e")

    def _build_mic(self, parent):
        mic = ttk.Labelframe(parent, text=" MICROPHONE ", padding=(12, 8, 12, 12))
        mic.columnconfigure(1, weight=1)

        self.device_combo = ttk.Combobox(mic, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.device_combo.bind("<<ComboboxSelected>>", self._start_device)
        ttk.Button(mic, text="Refresh", command=self._refresh_devices).grid(row=0, column=2, padx=(8, 0))

        self.meter = tk.Canvas(mic, height=36, bg=BG, highlightthickness=0)
        self.meter.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Label(mic, textvariable=self.level_text, width=8, anchor="e",
                  font=self.fonts["bold"]).grid(row=1, column=2, sticky="ne", pady=(12, 0))
        c = self.meter
        self.m_bg = c.create_rectangle(0, self.BAR_TOP, 0, self.BAR_BOT, fill=FIELD, outline="")
        self.m_level = c.create_rectangle(0, self.BAR_TOP, 0, self.BAR_BOT, fill=GRAY_BAR, outline="")
        self.m_noise = c.create_rectangle(0, self.BAR_TOP, 0, self.BAR_BOT, fill=BLUE, outline="")
        self.m_thr = c.create_rectangle(0, 0, 0, self.BAR_BOT + 3, fill=YELLOW, outline="")
        self.m_ticks = [(db, c.create_text(0, self.BAR_BOT + 5, text=str(db), fill=MUTED,
                                            font=self.fonts["small"], anchor="n"))
                        for db in (-80, -60, -40, -20, 0)]

        self.noise_label = ttk.Label(mic, textvariable=self.noise_text, style="Muted.TLabel")
        self.noise_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.calib_btn = ttk.Button(mic, text="Calibrate", command=self._calibrate, width=14)
        self.calib_btn.grid(row=2, column=2, sticky="e", pady=(6, 0))

        ttk.Label(mic, text="Talk threshold").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Scale(mic, from_=-80, to=-5, variable=self.threshold_var,
                  command=self._on_threshold).grid(row=3, column=1, sticky="ew", padx=10, pady=(10, 0))
        ttk.Label(mic, textvariable=self.thr_text, width=8, anchor="e").grid(
            row=3, column=2, sticky="e", pady=(10, 0))
        ttk.Label(mic, text="Blue = background noise · Yellow = threshold. Calibrate: stay quiet 3 s.",
                  style="Small.TLabel").grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        return mic

    def _build_options(self, parent):
        opt = ttk.Labelframe(parent, text=" OPTIONS ", padding=(12, 8, 12, 12))
        opt.columnconfigure(1, weight=1)

        ttk.Label(opt, text="Silence before prompt").grid(row=0, column=0, sticky="w")
        mins = ttk.Frame(opt)
        mins.grid(row=0, column=1, columnspan=2, sticky="w", padx=10)
        ttk.Spinbox(mins, from_=0.5, to=120, increment=0.5, width=6,
                    textvariable=self.minutes_var).pack(side="left")
        ttk.Label(mins, text="minutes", style="Muted.TLabel").pack(side="left", padx=(6, 0))

        ttk.Label(opt, text="Hide prompt after I talk").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Scale(opt, from_=0, to=len(HIDE_STOPS) - 1, variable=self.hide_idx_var,
                  command=self._on_hide).grid(row=1, column=1, sticky="ew", padx=10, pady=(10, 0))
        ttk.Label(opt, textvariable=self.hide_text, width=17, anchor="e").grid(
            row=1, column=2, sticky="e", pady=(10, 0))

        ttk.Label(opt, text="Sound on new prompt").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.sound_combo = ttk.Combobox(opt, textvariable=self.sound_label_var, state="readonly",
                                        postcommand=self._refresh_sound_list)
        self.sound_combo.grid(row=2, column=1, sticky="ew", padx=10, pady=(10, 0))
        self.sound_combo.bind("<<ComboboxSelected>>", self._on_sound_selected)
        ttk.Button(opt, text="Test", command=lambda: self._play_sound(preview=True)).grid(
            row=2, column=2, sticky="e", pady=(10, 0))

        ttk.Label(opt, text="Volume").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Scale(opt, from_=0, to=100, variable=self.volume_var, command=self._on_volume).grid(
            row=3, column=1, sticky="ew", padx=10, pady=(8, 0))
        ttk.Label(opt, textvariable=self.volume_text, width=17, anchor="e").grid(
            row=3, column=2, sticky="e", pady=(8, 0))

        sbtn = ttk.Frame(opt)
        sbtn.grid(row=4, column=1, columnspan=2, sticky="w", padx=10, pady=(6, 0))
        ttk.Button(sbtn, text="Add sound…", style="Small.TButton", command=self._add_sound).pack(side="left")
        ttk.Button(sbtn, text="Open sounds folder", style="Small.TButton",
                   command=self._open_sounds_folder).pack(side="left", padx=(6, 0))

        checks = ttk.Frame(opt)
        checks.grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Checkbutton(checks, text="Always on top", variable=self.top_var,
                        command=self._apply_on_top).pack(side="left")

        ttk.Label(opt, text="OBS text file").grid(row=6, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(opt, textvariable=self.output_path_var, state="readonly").grid(
            row=6, column=1, sticky="ew", padx=10, pady=(12, 0))
        obtn = ttk.Frame(opt)
        obtn.grid(row=6, column=2, sticky="e", pady=(12, 0))
        ttk.Button(obtn, text="Browse…", command=self._browse_output).pack(side="left")
        ttk.Button(obtn, text="Copy", command=self._copy_output_path).pack(side="left", padx=(6, 0))
        return opt

    def _build_sets(self, parent):
        box = ttk.Labelframe(parent, text=" PROMPT SETS ", padding=(12, 8, 12, 12))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(2, weight=1)

        folder = ttk.Frame(box)
        folder.grid(row=0, column=0, sticky="ew")
        folder.columnconfigure(0, weight=1)
        ttk.Entry(folder, textvariable=self.sets_dir_var, state="readonly").grid(row=0, column=0, sticky="ew")
        ttk.Button(folder, text="Open folder", command=self._open_sets_folder).grid(row=0, column=1, padx=(8, 0))
        fbtn = ttk.Frame(box)
        fbtn.grid(row=1, column=0, sticky="w", pady=(6, 8))
        ttk.Button(fbtn, text="Change folder…", style="Small.TButton",
                   command=self._choose_sets_folder).pack(side="left")
        self.default_folder_btn = ttk.Button(fbtn, text="Use default folder", style="Small.TButton",
                                             command=self._use_default_sets_folder)
        self.default_folder_btn.pack(side="left", padx=(6, 0))

        self.set_list = SetList(box, on_toggle=self._toggle_set, on_edit=self._edit_set)
        self.set_list.grid(row=2, column=0, sticky="nsew")

        summary = ttk.Frame(box)
        summary.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        summary.columnconfigure(0, weight=1)
        self.summary_label = ttk.Label(summary, textvariable=self.sets_summary, style="Muted.TLabel")
        self.summary_label.grid(row=0, column=0, sticky="w")
        ttk.Button(summary, text="All on", style="Small.TButton",
                   command=lambda: self._set_all(True)).grid(row=0, column=1)
        ttk.Button(summary, text="All off", style="Small.TButton",
                   command=lambda: self._set_all(False)).grid(row=0, column=2, padx=(6, 0))

        actions = ttk.Frame(box)
        actions.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="New set…", style="Accent.TButton", command=self._new_set).pack(side="left")
        ttk.Button(actions, text="Rescan", command=lambda: self._rescan_sets(force=True)).pack(
            side="left", padx=(6, 0))
        ttk.Checkbutton(actions, text="Equal chance per set", variable=self.balance_var,
                        command=self._apply_sets).pack(side="right")
        ttk.Label(box, text="Each .txt file is a set, one prompt per line. Edits load automatically.",
                  style="Small.TLabel").grid(row=5, column=0, sticky="w", pady=(6, 0))
        return box

    def _build_live(self, parent):
        live = ttk.Labelframe(parent, text=" LIVE ", padding=(12, 8, 12, 12))
        live.columnconfigure(1, weight=1)
        live.rowconfigure(1, weight=1)

        self.state_label = ttk.Label(live, textvariable=self.state_text, width=10)
        self.state_label.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(live, maximum=100)
        self.progress.grid(row=0, column=1, sticky="ew", padx=10)
        ttk.Label(live, textvariable=self.timer_text, font=self.fonts["bold"]).grid(row=0, column=2, sticky="e")

        self.prompt_frame = tk.Frame(live, bg=PANEL, highlightthickness=2, highlightbackground=BORDER)
        self.prompt_frame.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        self.prompt_label = tk.Label(self.prompt_frame, textvariable=self.prompt_var, bg=PANEL, fg=MUTED,
                                     font=self.fonts["big"], justify="left", anchor="w", padx=16,
                                     wraplength=760)
        self.prompt_label.pack(fill="both", expand=True, pady=(16, 0))
        tk.Label(self.prompt_frame, textvariable=self.prompt_src_var, bg=PANEL, fg=MUTED,
                 font=self.fonts["small"], anchor="w", padx=16).pack(fill="x", pady=(2, 12))
        self.prompt_frame.bind("<Configure>",
                               lambda e: self.prompt_label.configure(wraplength=max(e.width - 40, 120)))

        btns = ttk.Frame(live)
        btns.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))
        self.pause_btn = ttk.Button(btns, text="Pause", command=self._toggle_pause, width=9)
        self.pause_btn.pack(side="left")
        ttk.Button(btns, text="Clear", command=self._clear_prompt).pack(side="right")
        ttk.Button(btns, text="Next prompt", style="Accent.TButton",
                   command=self._show_prompt).pack(side="right", padx=(0, 8))
        return live

    def _restore_geometry(self):
        self.root.update_idletasks()
        self.root.minsize(self.root.winfo_reqwidth(), self.root.winfo_reqheight())
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", self.s["geometry"])
        if not m:
            return
        w, h, x, y = (int(v) for v in m.groups())
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if -sw <= x <= 2 * sw - 100 and -sh // 2 <= y <= sh - 60:
            self.root.geometry(f"{w}x{h}+{x}+{y}")
        else:
            self.root.geometry(f"{w}x{h}")

    # ------------------------------------------------------------------ mic

    def _refresh_devices(self, initial=False):
        self.mon.stop()
        if not initial:
            audio.rescan_devices()
        try:
            self.devices = audio.list_input_devices()
        except Exception as e:
            self.devices = {}
            self._status(f"Couldn't list audio devices: {e}", error=True)
        labels = ["System default"] + list(self.devices)
        self.device_combo["values"] = labels
        if self.device_var.get() not in labels:
            self.device_var.set("System default")
        self._start_device()

    def _start_device(self, *_):
        index = self.devices.get(self.device_var.get())
        try:
            name = self.mon.start(index)
            self._status(f"Listening: {name}")
            log.info("Listening on %s", name)
        except Exception as e:
            self.mon.stop()
            self._status(f"Couldn't open mic: {e}", error=True)
            log.warning("Couldn't open mic %r: %s", self.device_var.get(), e)
        self.timer_floor = time.monotonic()
        self._changed()

    def _calibrate(self):
        if self.mon.stream is None:
            self._status("No mic open", error=True)
            return
        self.calibrate_start = time.monotonic()
        self.calib_btn.state(["disabled"])

    def _finish_calibration(self):
        levels = self.mon.levels_since(self.calibrate_start)
        self.calibrate_start = None
        self.calib_btn.state(["!disabled"])
        self.calib_btn.configure(text="Calibrate")
        if not levels:
            self._status("Calibration got no audio", error=True)
            return
        noise, thr = audio.calibrated_threshold(levels, CALIBRATE_MARGIN_DB)
        self.threshold_var.set(thr)
        self._on_threshold()
        self._status(f"Calibrated: room noise {noise:.0f} dB, threshold set to {thr} dB")

    def _on_threshold(self, *_):
        v = int(round(self.threshold_var.get()))
        self.mon.threshold_db = v
        self.thr_text.set(f"{v} dB")
        self._changed()

    @staticmethod
    def _hide_index(seconds):
        if seconds is None or seconds < 0:
            return len(HIDE_STOPS) - 1
        return min((abs(s - seconds), i) for i, (s, _) in enumerate(HIDE_STOPS) if s is not None)[1]

    def _hide_delay(self):
        """Seconds the prompt stays after you start talking, or None for until the next prompt."""
        idx = min(max(int(round(self.hide_idx_var.get())), 0), len(HIDE_STOPS) - 1)
        return HIDE_STOPS[idx][0]

    def _on_hide(self, *_):
        idx = min(max(int(round(self.hide_idx_var.get())), 0), len(HIDE_STOPS) - 1)
        if self.hide_idx_var.get() != idx:
            self.hide_idx_var.set(idx)          # snap the slider to the nearest stop
        self.hide_text.set(HIDE_STOPS[idx][1])
        self._changed()

    # ------------------------------------------------------------------ sound

    def _refresh_sound_list(self):
        """Built-in sounds, the system beep, and every .wav in the sounds folder."""
        options = [("off", "Off")]
        options += [(f"builtin:{sid}", label) for sid, label in sounds.BUILTIN]
        options.append(("system", "System beep"))
        options += [(f"file:{p.name}", f"{display_name(p.stem)} (custom)")
                    for p in sounds.list_custom(self.storage.sounds_dir)]
        self._sound_options = options
        self.sound_combo["values"] = [label for _, label in options]
        if self.sound_id not in dict(options):
            if self.sound_id.startswith("file:"):
                self._status(f"Sound file {self.sound_id[5:]} is missing, so sound is off", error=True)
            self.sound_id = "off"
            self._changed()
        self.sound_label_var.set(dict(options)[self.sound_id])

    def _on_sound_selected(self, *_):
        label = self.sound_label_var.get()
        self.sound_id = next((sid for sid, lbl in self._sound_options if lbl == label), "off")
        self._changed()
        if self.sound_id != "off":
            self._play_sound(preview=True)

    def _on_volume(self, *_):
        self.volume_text.set(f"{round(self.volume_var.get())}%")
        self._changed()

    def _load_sound(self, sid, rate):
        """(samples, sample rate) for a sound id, cached; custom files reload when they change."""
        if sid.startswith("builtin:"):
            key = (sid, rate)
            if key not in self._sound_cache:
                self._sound_cache[key] = (sounds.synth(sid[len("builtin:"):], rate), rate)
            return self._sound_cache[key]
        path = self.storage.sounds_dir / sid[len("file:"):]
        key = (sid, path.stat().st_mtime_ns)
        if key not in self._sound_cache:
            self._sound_cache[key] = sounds.read_wav(path)
        return self._sound_cache[key]

    def _play_sound(self, preview=False):
        sid = self.sound_id
        if sid == "off":
            if preview:
                self._status("Sound is off. Pick a sound from the list.")
            return
        if sid == "system":
            self.root.bell()
            return
        try:
            rate, channels = audio.default_output()
            samples, src_rate = self._load_sound(sid, rate)
            gain = (self.volume_var.get() / 100.0) ** 2      # feels more even than linear
            audio.play(sounds.prepare(samples, src_rate, rate, channels, gain), rate)
        except Exception as e:
            self._status(f"Couldn't play sound: {e}", error=True)
            log.warning("Couldn't play sound %s: %s", sid, e)

    def _add_sound(self):
        p = filedialog.askopenfilename(title="Choose a sound (.wav)",
                                       filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")])
        if not p:
            return
        src = Path(p)
        try:
            sounds.read_wav(src)
        except Exception as e:
            self._status(f"Can't use {src.name}: {e}", error=True)
            return
        folder = self.storage.sounds_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            stem = safe_filename(src.stem) or "sound"
            dst = folder / f"{stem}.wav"
            if not (dst.exists() and dst.resolve() == src.resolve()):
                n = 2
                while dst.exists():
                    dst = folder / f"{stem} ({n}).wav"
                    n += 1
                shutil.copyfile(src, dst)
        except OSError as e:
            self._status(f"Couldn't add sound: {e}", error=True)
            return
        self.sound_id = f"file:{dst.name}"
        self._refresh_sound_list()
        self._changed()
        self._status(f"Added sound “{display_name(dst.stem)}”")
        self._play_sound(preview=True)

    def _open_sounds_folder(self):
        try:
            self.storage.sounds_dir.mkdir(parents=True, exist_ok=True)
            open_path(self.storage.sounds_dir)
        except Exception as e:
            self._status(f"Couldn't open folder: {e}", error=True)

    # ------------------------------------------------------------------ prompt sets

    @property
    def sets_dir(self) -> Path:
        return Path(self.s["sets_dir"]).expanduser() if self.s["sets_dir"] else self.storage.default_sets_dir

    def _rescan_sets(self, force=False):
        sig = folder_signature(self.sets_dir)
        if not force and sig == self._sets_sig:
            return
        self._sets_sig = sig
        self.sets = load_sets(self.sets_dir)
        self.set_list.render(self.sets, self.disabled, folder_missing=sig is None)
        self.sets_dir_var.set(str(self.sets_dir))
        self.default_folder_btn.state(["disabled"] if not self.s["sets_dir"] else ["!disabled"])
        if sig is None:
            self._status(f"Prompt set folder not found: {self.sets_dir}", error=True)
        self._apply_sets()

    def _apply_sets(self):
        enabled = [s for s in self.sets if s.key not in self.disabled]
        self.picker.update(enabled, bool(self.balance_var.get()))
        total, on = self.picker.total, sum(1 for s in enabled if s.prompts)
        if total:
            self.sets_summary.set(f"{total} prompts from {on} of {len(self.sets)} sets")
            self.summary_label.configure(style="Muted.TLabel")
        else:
            self.sets_summary.set("No prompts: turn on a set with prompts in it")
            self.summary_label.configure(style="Warn.TLabel")
        if not self.prompt_on_screen:
            self.prompt_var.set(self._placeholder())
        self._changed()

    def _toggle_set(self, key, on):
        if on:
            self.disabled.discard(key)
        else:
            self.disabled.add(key)
        self._apply_sets()

    def _set_all(self, on):
        for key, var in self.set_list.vars.items():
            var.set(on)
            if on:
                self.disabled.discard(key)
            else:
                self.disabled.add(key)
        self._apply_sets()

    def _open_sets_folder(self):
        try:
            self.sets_dir.mkdir(parents=True, exist_ok=True)
            open_path(self.sets_dir)
        except Exception as e:
            self._status(f"Couldn't open folder: {e}", error=True)

    def _choose_sets_folder(self):
        p = filedialog.askdirectory(title="Prompt sets folder", initialdir=str(self.sets_dir), mustexist=True)
        if p:
            self.s["sets_dir"] = p
            self._rescan_sets(force=True)
            self._status(f"Using prompt sets from {p}")

    def _use_default_sets_folder(self):
        self.s["sets_dir"] = ""
        self.storage.default_sets_dir.mkdir(parents=True, exist_ok=True)
        self._rescan_sets(force=True)
        self._status("Using the default prompt sets folder")

    def _edit_set(self, path):
        try:
            open_path(path)
        except Exception as e:
            self._status(f"Couldn't open {Path(path).name}: {e}", error=True)

    def _new_set(self):
        raw = ask_string(self.root, "New prompt set", "Name for the new set:", ok_text="Create",
                         topmost=bool(self.top_var.get()))
        if raw is None:
            return
        name = safe_filename(raw)
        if not name:
            self._status("Set name can't be empty", error=True)
            return
        path = self.sets_dir / f"{name}.txt"
        try:
            self.sets_dir.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._status(f"“{name}” already exists; opened it for editing")
            else:
                path.write_text(NEW_SET_TEMPLATE, encoding="utf-8")
                self.disabled.discard(path.stem)
                self._status(f"Created “{name}”. Add one prompt per line and save.")
            open_path(path)
        except Exception as e:
            self._status(f"Couldn't create set: {e}", error=True)
        self._rescan_sets(force=True)

    # ------------------------------------------------------------------ prompts / output

    def _output_path(self):
        p = self.output_path_var.get().strip()
        return Path(p) if p else None

    def _browse_output(self):
        current = self._output_path()
        p = filedialog.asksaveasfilename(
            title="OBS text file", defaultextension=".txt",
            initialdir=str(current.parent) if current else None,
            initialfile=current.name if current else "current_prompt.txt",
            filetypes=[("Text files", "*.txt")])
        if p:
            self.output_path_var.set(p)
            self.s["output_path"] = p
            self._write_overlay(self.prompt_var.get() if self.prompt_on_screen else "")
            self._changed()

    def _copy_output_path(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.output_path_var.get())
        self._status("OBS text file path copied")

    def _write_overlay(self, text):
        path = self._output_path()
        if path is None:
            return
        try:
            write_text_atomic(path, text)
        except OSError as e:
            self._status(f"Can't write OBS file: {e}", error=True)

    def _placeholder(self):
        if self.picker.total == 0:
            return "No prompts available. Turn on a prompt set."
        return f"A prompt appears here after {self._minutes():g} min of silence."

    def _set_name(self, key):
        return next((s.name for s in self.sets if s.key == key), key)

    def _show_prompt(self):
        picked = self.picker.next()
        if picked is None:
            self._status("No prompt sets with prompts are turned on", error=True)
            return
        prompt, key = picked
        self.prompt_set_name = self._set_name(key)
        self.talk_started = None
        self.prompt_var.set(prompt)
        self.prompt_src_var.set(f"from {self.prompt_set_name}")
        self.prompt_label.configure(fg=FG)
        self.prompt_frame.configure(highlightbackground=ACCENT)
        self._write_overlay(prompt)
        self.prompt_on_screen = True
        self.blocks_at_prompt = self.mon.snapshot()[2]
        self.timer_floor = time.monotonic()
        self._play_sound()

    def _clear_prompt(self):
        self.prompt_on_screen = False
        self.talk_started = None
        self.prompt_label.configure(fg=MUTED)
        self.prompt_frame.configure(highlightbackground=BORDER)
        self.prompt_var.set(self._placeholder())
        self.prompt_src_var.set("")
        self._write_overlay("")

    def _toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.configure(text="Resume" if self.paused else "Pause")

    # ------------------------------------------------------------------ settings / misc

    def _minutes(self):
        try:
            v = float(self.minutes_var.get())
            if v > 0:
                self._last_minutes = v
        except ValueError:
            pass
        return self._last_minutes

    def _apply_on_top(self):
        self.root.attributes("-topmost", bool(self.top_var.get()))
        self._changed()

    def _status(self, msg, error=False):
        self.status_text.set(msg)
        self.status_label.configure(style="Warn.TLabel" if error else "Muted.TLabel")

    def _changed(self, *_):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(800, self._save)

    def _save(self):
        self._save_job = None
        self.s.update({
            "device": self.device_var.get(),
            "threshold": float(round(self.threshold_var.get())),
            "minutes": self._minutes(),
            "hide_delay": float(-1 if self._hide_delay() is None else self._hide_delay()),
            "sound": self.sound_id,
            "volume": float(round(self.volume_var.get())),
            "on_top": bool(self.top_var.get()),
            "balance_sets": bool(self.balance_var.get()),
            "disabled_sets": sorted(self.disabled),
        })
        if self.root.winfo_ismapped():
            self.s["geometry"] = self.root.geometry()
        try:
            self.storage.save_settings(self.s)
        except OSError as e:
            log.warning("Couldn't save settings: %s", e)

    def _on_close(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save()
        self.mon.stop()
        self._write_overlay("")
        log.info("Closed")
        self.root.destroy()

    # ------------------------------------------------------------------ meter + loop

    def _x(self, db, w):
        db = min(max(db, DB_MIN), DB_MAX)
        return (db - DB_MIN) / (DB_MAX - DB_MIN) * w

    def _draw_meter(self, level, thr, noise):
        c = self.meter
        w = max(c.winfo_width(), 60)
        c.coords(self.m_bg, 0, self.BAR_TOP, w, self.BAR_BOT)
        c.coords(self.m_level, 0, self.BAR_TOP, self._x(level, w), self.BAR_BOT)
        c.itemconfigure(self.m_level, fill=GREEN if level > thr else GRAY_BAR)
        xt = self._x(thr, w)
        c.coords(self.m_thr, xt - 1.5, 0, xt + 1.5, self.BAR_BOT + 3)
        if noise is None:
            c.itemconfigure(self.m_noise, state="hidden")
        else:
            xn = self._x(noise, w)
            c.coords(self.m_noise, xn - 1, self.BAR_TOP, xn + 1, self.BAR_BOT)
            c.itemconfigure(self.m_noise, state="normal")
        if w != self._meter_w:
            self._meter_w = w
            for db, item in self.m_ticks:
                anchor = "nw" if db == DB_MIN else "ne" if db == DB_MAX else "n"
                c.coords(item, self._x(db, w), self.BAR_BOT + 5)
                c.itemconfigure(item, anchor=anchor)

    def _tick(self):
        try:
            self._update()
        except Exception:
            log.exception("Error in update loop")
            self._status("Something went wrong; see stream_prompter.log", error=True)
        finally:
            self.root.after(TICK_MS, self._tick)

    def _update(self):
        now = time.monotonic()
        self._ticks += 1
        if self._ticks % RESCAN_EVERY_TICKS == 0:
            self._rescan_sets()

        level, last_sound, loud_blocks = self.mon.snapshot()
        thr = self.mon.threshold_db
        listening = self.mon.stream is not None

        noise = audio.noise_floor(self.mon.levels_since(now - NOISE_WINDOW_SECONDS))
        self._draw_meter(level if listening else DB_MIN, thr, noise)
        self.level_text.set(f"{level:.0f} dB" if listening else "-- dB")
        if noise is None:
            self.noise_text.set("Background noise: --")
            self.noise_label.configure(style="Muted.TLabel")
        else:
            close = noise > thr - 3
            self.noise_text.set(f"Background noise: {noise:.0f} dB"
                                + ("   (too close to threshold, press Calibrate)" if close else ""))
            self.noise_label.configure(style="Warn.TLabel" if close else "Muted.TLabel")

        if self.calibrate_start is not None:
            left = self.calibrate_start + CALIBRATE_SECONDS - now
            if left > 0:
                self.calib_btn.configure(text=f"Stay quiet… {math.ceil(left)}")
            else:
                self._finish_calibration()

        limit = self._minutes() * 60
        if self.paused or not listening or self.picker.total == 0:
            self.timer_floor = now
        silent_for = now - max(last_sound, self.timer_floor)

        if self.prompt_on_screen:
            if self.talk_started is None and loud_blocks > self.blocks_at_prompt:
                self.talk_started = now
            delay = self._hide_delay()
            if self.talk_started is not None and delay is not None:
                left = self.talk_started + delay - now
                if left <= 0:
                    self._clear_prompt()
                else:
                    self.prompt_src_var.set(f"from {self.prompt_set_name}  ·  hiding in {math.ceil(left)} s")
            else:
                self.prompt_src_var.set(f"from {self.prompt_set_name}")

        if listening and not self.paused and silent_for >= limit and self.picker.total:
            self._show_prompt()
            silent_for = 0.0

        if not self.prompt_on_screen:
            self.prompt_var.set(self._placeholder())
        self.progress["value"] = min(100.0, silent_for / limit * 100)
        self.timer_text.set(f"{_fmt(silent_for)} / {_fmt(limit)}")

        if self.paused:
            state = ("Paused", "Paused.TLabel")
        elif not listening:
            state = ("No mic", "NoMic.TLabel")
        elif now - last_sound < 0.4:
            state = ("● Talking", "Talk.TLabel")
        else:
            state = ("● Silent", "Silent.TLabel")
        if state != self._state:
            self._state = state
            self.state_text.set(state[0])
            self.state_label.configure(style=state[1])


def _fmt(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _setup_logging(path: Path):
    try:
        handler = RotatingFileHandler(path, maxBytes=256_000, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _set_icon(root):
    try:
        images = [tk.PhotoImage(file=str(ASSETS_DIR / name)) for name in ("icon_256.png", "icon_32.png")]
        root.iconphoto(True, *images)
        root._icon_images = images  # keep a reference so Tk doesn't drop them
    except (tk.TclError, OSError):
        pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stream-prompter", description=APP_NAME)
    parser.add_argument("--data-dir", help="folder for settings, prompt sets, the OBS file and the log")
    args, _unknown = parser.parse_known_args(argv)

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("StreamPrompter.App")
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    if audio is None:
        root.withdraw()
        hint = "\n\nOn Linux also run:  sudo apt install libportaudio2" if sys.platform.startswith("linux") else ""
        messagebox.showerror(APP_NAME, f"Audio support is missing:\n{AUDIO_ERROR}\n\n"
                                       f"Run:  pip install sounddevice numpy{hint}")
        return 1
    try:
        storage = Storage(args.data_dir)
    except OSError as e:
        root.withdraw()
        messagebox.showerror(APP_NAME, f"Can't create the data folder:\n{e}")
        return 1

    _setup_logging(storage.log_path)
    log.info("%s %s starting; data folder %s", APP_NAME, __version__, storage.data_dir)
    fonts = setup_theme(root)
    _set_icon(root)
    app = App(root, fonts, storage)

    def report_error(exc, value, tb):
        log.error("Unhandled error:\n%s", "".join(traceback.format_exception(exc, value, tb)))
        app._status(f"Error: {value}  (details in stream_prompter.log)", error=True)

    root.report_callback_exception = report_error
    root.mainloop()
    return 0
