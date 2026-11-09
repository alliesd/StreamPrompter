"""Microphone input and level measurement."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

BLOCK_SECONDS = 0.1
DB_FLOOR = -120.0


def block_db(block) -> float:
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    return max(20.0 * math.log10(max(rms, 1e-6)), DB_FLOOR)


def noise_floor(levels):
    """Quiet end of recent levels, i.e. the room's background noise."""
    return float(np.percentile(levels, 10)) if levels else None


def calibrated_threshold(levels, margin_db: float, low: float = -75, high: float = -10):
    """(room noise, threshold) from a few seconds of silence."""
    noise = float(np.percentile(levels, 95))
    return noise, int(round(min(max(noise + margin_db, low), high)))


def list_input_devices() -> dict[str, int]:
    hostapis = sd.query_hostapis()
    devices = {}
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            label = f"{d['name']}  ({hostapis[d['hostapi']]['name']})"
            devices.setdefault(label, i)
    return devices


def default_output() -> tuple[int, int]:
    """(sample rate, channel count) of the default output device."""
    info = sd.query_devices(kind="output")
    return int(info["default_samplerate"]), max(1, int(info["max_output_channels"]))


def play(samples, rate: int) -> None:
    """Play on the default output device without blocking (replaces any sound still playing)."""
    sd.play(samples, rate)


def rescan_devices() -> None:
    """Re-initialise PortAudio so newly plugged-in mics show up."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


class MicMonitor:
    def __init__(self, threshold_db: float):
        self.lock = threading.Lock()
        self.threshold_db = threshold_db
        self.level_db = DB_FLOOR
        self.last_sound = time.monotonic()
        self.loud_blocks = 0
        self.history = deque(maxlen=int(60 / BLOCK_SECONDS))
        self.stream = None

    def callback(self, indata, frames, time_info, status):
        db = block_db(indata)
        now = time.monotonic()
        with self.lock:
            self.level_db = db
            self.history.append((now, db))
            if db > self.threshold_db:
                self.last_sound = now
                self.loud_blocks += 1

    def snapshot(self):
        with self.lock:
            return self.level_db, self.last_sound, self.loud_blocks

    def levels_since(self, t: float) -> list[float]:
        with self.lock:
            return [db for ts, db in self.history if ts >= t]

    def start(self, device) -> str:
        """Open the mic (None = system default). Returns the device name."""
        self.stop()
        info = sd.query_devices(device, "input")
        rate = int(info["default_samplerate"])
        error = None
        # Mono first; some WASAPI mics only open with their native channel count.
        for channels in sorted({1, max(1, min(2, int(info["max_input_channels"])))}):
            try:
                stream = sd.InputStream(device=device, channels=channels, samplerate=rate,
                                        blocksize=int(rate * BLOCK_SECONDS), callback=self.callback)
            except sd.PortAudioError as e:
                error = e
                continue
            with self.lock:
                self.history.clear()
                self.level_db = DB_FLOOR
                self.last_sound = time.monotonic()
            stream.start()
            self.stream = stream
            return info["name"]
        raise error

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
