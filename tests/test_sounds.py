import struct
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from stream_prompter import sounds


def write_pcm_wav(path, samples, rate, width, channels=1):
    """samples: float array in [-1, 1], shape (frames, channels)."""
    x = np.asarray(samples, dtype=np.float64).reshape(-1, channels)
    if width == 1:
        raw = np.round(x * 127 + 128).astype(np.uint8).tobytes()
    elif width == 2:
        raw = np.round(x * 32767).astype("<i2").tobytes()
    elif width == 3:
        v = np.round(x * (2 ** 23 - 1)).astype(np.int32).ravel()
        raw = b"".join(int(s).to_bytes(3, "little", signed=True) for s in v)
    else:
        raw = np.round(x * (2 ** 31 - 1)).astype("<i4").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(raw)


def write_float_wav(path, samples, rate, extensible=False):
    x = np.asarray(samples, dtype="<f4")
    data = x.tobytes()
    if extensible:
        guid = struct.pack("<H", 3) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, rate, rate * 4, 4, 32, 22, 32, 4) + guid
    else:
        fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"LIST" + struct.pack("<I", 4) + b"INFO"           # an extra chunk to skip
    body += b"data" + struct.pack("<I", len(data)) + data
    Path(path).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


class BuiltinTests(unittest.TestCase):
    def test_every_builtin_renders_cleanly(self):
        for sid, _label in sounds.BUILTIN:
            for rate in (44100, 48000):
                x = sounds.synth(sid, rate)
                with self.subTest(sound=sid, rate=rate):
                    self.assertEqual(x.dtype, np.float32)
                    self.assertTrue(0.05 * rate < len(x) < 2 * rate)
                    self.assertTrue(np.isfinite(x).all())
                    self.assertLessEqual(np.abs(x).max(), 0.8 + 1e-6)
                    self.assertGreater(np.abs(x).max(), 0.3)
                    self.assertLess(abs(x[0]), 0.05)        # no click at the start
                    self.assertLess(abs(x[-1]), 0.01)       # or the end

    def test_unknown_builtin(self):
        with self.assertRaises(KeyError):
            sounds.synth("nope", 48000)


class WavTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.wave = 0.5 * np.sin(np.linspace(0, 20 * np.pi, 2000))

    def tearDown(self):
        self.tmp.cleanup()

    def test_pcm_widths(self):
        for width, tol in ((1, 0.01), (2, 1e-4), (3, 1e-6), (4, 1e-6)):   # output is float32
            p = self.dir / f"w{width}.wav"
            write_pcm_wav(p, self.wave, 22050, width)
            x, rate = sounds.read_wav(p)
            with self.subTest(width=width):
                self.assertEqual((rate, x.shape), (22050, (2000, 1)))
                self.assertLess(np.abs(x[:, 0] - self.wave).max(), tol)

    def test_stereo(self):
        stereo = np.stack([self.wave, -self.wave], axis=1)
        p = self.dir / "stereo.wav"
        write_pcm_wav(p, stereo, 44100, 2, channels=2)
        x, _ = sounds.read_wav(p)
        self.assertEqual(x.shape, (2000, 2))
        self.assertLess(np.abs(x - stereo).max(), 1e-4)

    def test_float_and_extensible(self):
        for ext in (False, True):
            p = self.dir / f"float{ext}.wav"
            write_float_wav(p, self.wave, 48000, extensible=ext)
            x, rate = sounds.read_wav(p)
            with self.subTest(extensible=ext):
                self.assertEqual(rate, 48000)
                self.assertLess(np.abs(x[:, 0] - self.wave).max(), 1e-6)

    def test_long_files_are_cut(self):
        p = self.dir / "long.wav"
        write_pcm_wav(p, np.zeros(8000 * 15), 8000, 2)
        x, _ = sounds.read_wav(p)
        self.assertEqual(len(x), 8000 * sounds.MAX_SECONDS)

    def test_rejects_bad_files(self):
        bad = self.dir / "bad.wav"
        bad.write_bytes(b"ID3\x03 this is an mp3")
        with self.assertRaisesRegex(ValueError, "not a WAV"):
            sounds.read_wav(bad)
        empty = self.dir / "empty.wav"
        write_pcm_wav(empty, np.zeros(0), 8000, 2)
        with self.assertRaisesRegex(ValueError, "no audio"):
            sounds.read_wav(empty)

    def test_list_custom(self):
        for name in ("b.wav", "A.WAV", "c.mp3", ".hidden.wav"):
            (self.dir / name).write_bytes(b"x")
        self.assertEqual([p.name for p in sounds.list_custom(self.dir)], ["A.WAV", "b.wav"])
        self.assertEqual(sounds.list_custom(self.dir / "missing"), [])


class PrepareTests(unittest.TestCase):
    def test_resample_downmix_and_gain(self):
        x = np.stack([np.full(1000, 0.5), np.full(1000, 0.1)], axis=1)
        out = sounds.prepare(x, 24000, 48000, out_channels=1, gain=0.5)
        self.assertEqual(out.shape, (2000, 1))
        self.assertAlmostEqual(float(out[100, 0]), 0.15, places=5)   # mean(0.5, 0.1) * 0.5
        self.assertEqual(float(out[-1, 0]), 0.0)                     # faded out

    def test_mono_to_stereo_device_and_clipping(self):
        out = sounds.prepare(np.full(480, 0.9, np.float32), 48000, 48000, out_channels=2, gain=4.0)
        self.assertEqual(out.shape, (480, 1))          # mono stays mono; PortAudio sends it to both ears
        self.assertLessEqual(float(out.max()), 1.0)

    def test_extra_channels_dropped(self):
        out = sounds.prepare(np.zeros((100, 6)), 48000, 48000, out_channels=2, gain=1.0)
        self.assertEqual(out.shape, (100, 2))


if __name__ == "__main__":
    unittest.main()
