"""Notification sounds: built-in sounds generated in code, and custom .wav files."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAX_SECONDS = 10                       # longer custom sounds are cut off
MAX_FILE_BYTES = 50 * 1024 * 1024

# id -> display name, in dropdown order
BUILTIN = [
    ("chime", "Chime"),
    ("ding", "Ding"),
    ("pop", "Pop"),
    ("soft_bell", "Soft bell"),
    ("arcade", "Arcade"),
    ("classic", "Classic beep"),
]


# ---------------------------------------------------------------- built-in sounds

def _tone(freq, seconds, rate, partials=((1.0, 1.0),), tau=0.4, attack=0.004, square=False):
    t = np.arange(int(seconds * rate)) / rate
    if square:  # soft square wave from odd harmonics
        partials = tuple((m, 1.0 / m) for m in (1, 3, 5, 7, 9))
    wave = sum(amp * np.sin(2 * np.pi * freq * mult * t) for mult, amp in partials)
    env = np.minimum(1.0, t / attack) * (np.exp(-t / tau) if tau else 1.0)
    return wave * env


def _mix(seconds, rate, parts):
    out = np.zeros(int(seconds * rate))
    for offset, sig in parts:
        start = int(offset * rate)
        end = min(len(out), start + len(sig))
        out[start:end] += sig[: end - start]
    return out


def _finish(x, rate, peak):
    fade = min(len(x), int(0.01 * rate))
    x = x.copy()
    x[len(x) - fade:] *= np.linspace(1.0, 0.0, fade)
    top = float(np.max(np.abs(x))) or 1.0
    return (x / top * peak).astype(np.float32)


def synth(sound_id: str, rate: int) -> np.ndarray:
    """Mono float32 samples for a built-in sound at the given sample rate."""
    bell = ((1.0, 1.0), (2.0, 0.25), (3.0, 0.08))
    if sound_id == "chime":
        x = _mix(0.95, rate, [(0.0, _tone(1046.5, 0.8, rate, bell, tau=0.30)),
                              (0.12, _tone(1568.0, 0.8, rate, bell, tau=0.35))])
        return _finish(x, rate, 0.8)
    if sound_id == "ding":
        x = _tone(880.0, 1.0, rate, ((1.0, 1.0), (2.0, 0.4), (3.0, 0.15), (4.2, 0.05)), tau=0.45)
        return _finish(x, rate, 0.8)
    if sound_id == "pop":
        t = np.arange(int(0.12 * rate)) / rate
        freq = 300.0 + 600.0 * np.exp(-t / 0.015)
        phase = 2 * np.pi * np.cumsum(freq) / rate
        x = np.sin(phase) * np.minimum(1.0, t / 0.002) * np.exp(-t / 0.03)
        return _finish(x, rate, 0.8)
    if sound_id == "soft_bell":
        x = _tone(587.3, 1.6, rate, ((1.0, 1.0), (2.76, 0.18), (5.4, 0.06)), tau=0.8, attack=0.01)
        return _finish(x, rate, 0.5)
    if sound_id == "arcade":
        x = _mix(0.34, rate, [(0.0, _tone(987.8, 0.07, rate, square=True, tau=None)),
                              (0.07, _tone(1318.5, 0.27, rate, square=True, tau=0.12))])
        return _finish(x, rate, 0.45)
    if sound_id == "classic":
        t = np.arange(int(0.18 * rate)) / rate
        x = np.sin(2 * np.pi * 880.0 * t) * np.minimum(1.0, t / 0.005)
        return _finish(x, rate, 0.6)
    raise KeyError(sound_id)


# ---------------------------------------------------------------- custom .wav files

def read_wav(path):
    """Decode a WAV file to float32 samples shaped (frames, channels) and its sample rate.

    Handles 8/16/24/32-bit PCM and 32/64-bit float, including WAVE_FORMAT_EXTENSIBLE.
    """
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file is larger than 50 MB")
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    fmt = pcm = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if fmt is None or len(fmt) < 16 or pcm is None:
        raise ValueError("WAV file has no audio data")

    tag, channels, rate = struct.unpack_from("<HHI", fmt, 0)
    bits = struct.unpack_from("<H", fmt, 14)[0]
    if tag == 0xFFFE and len(fmt) >= 26:          # extensible: real format is in the sub-format GUID
        tag = struct.unpack_from("<H", fmt, 24)[0]
    width = bits // 8
    if channels < 1 or rate < 1 or width < 1:
        raise ValueError("WAV header is invalid")
    frame = width * channels
    pcm = pcm[: len(pcm) - len(pcm) % frame]
    if not pcm:
        raise ValueError("WAV file has no audio data")

    if tag == 1 and width == 1:
        x = (np.frombuffer(pcm, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 1 and width == 2:
        x = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and width == 3:
        b = np.frombuffer(pcm, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = (np.where(v >= 1 << 23, v - (1 << 24), v)).astype(np.float32) / float(1 << 23)
    elif tag == 1 and width == 4:
        x = np.frombuffer(pcm, "<i4").astype(np.float32) / float(1 << 31)
    elif tag == 3 and width in (4, 8):
        x = np.frombuffer(pcm, "<f4" if width == 4 else "<f8").astype(np.float32)
    else:
        raise ValueError(f"unsupported WAV encoding ({bits}-bit, format {tag})")

    x = np.nan_to_num(x.reshape(-1, channels))[: int(MAX_SECONDS * rate)]
    return x, rate


def list_custom(folder) -> list[Path]:
    try:
        files = [p for p in Path(folder).iterdir()
                 if p.suffix.lower() == ".wav" and not p.name.startswith(".") and p.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name.lower())


# ---------------------------------------------------------------- playback prep

def prepare(samples, src_rate: int, dst_rate: int, out_channels: int, gain: float) -> np.ndarray:
    """Fit samples to the output device (rate, channel count) and apply volume."""
    x = np.asarray(samples, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    x = x[:, :2]
    if out_channels <= 1 and x.shape[1] > 1:
        x = x.mean(axis=1, keepdims=True)
    if src_rate != dst_rate and len(x) > 1:
        n_out = max(1, int(round(len(x) * dst_rate / src_rate)))
        src_t = np.arange(len(x))
        dst_t = np.arange(n_out) * (src_rate / dst_rate)
        x = np.stack([np.interp(dst_t, src_t, x[:, c]) for c in range(x.shape[1])], axis=1)
    fade = min(len(x), int(0.005 * dst_rate))
    if fade:
        x = x.copy()
        x[len(x) - fade:] *= np.linspace(1.0, 0.0, fade)[:, None]
    return np.clip(x * gain, -1.0, 1.0).astype(np.float32)
