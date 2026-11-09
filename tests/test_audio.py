"""Audio tests run against a fake sounddevice module, so no mic is needed."""

import importlib
import sys
import time
import types
import unittest

import numpy as np


class _PortAudioError(Exception):
    pass


def _fake_sounddevice():
    sd = types.ModuleType("sounddevice")
    sd.PortAudioError = _PortAudioError
    devices = [
        dict(name="Speakers", max_input_channels=0, hostapi=0, default_samplerate=48000.0),
        dict(name="USB Mic", max_input_channels=1, hostapi=0, default_samplerate=48000.0),
        dict(name="USB Mic", max_input_channels=1, hostapi=1, default_samplerate=48000.0),
        dict(name="Stereo Only", max_input_channels=2, hostapi=1, default_samplerate=44100.0),
    ]
    sd.query_hostapis = lambda: ({"name": "MME"}, {"name": "Windows WASAPI"})

    def query_devices(device=None, kind=None):
        if kind == "output":
            return dict(name="Speakers", max_output_channels=2, default_samplerate=48000.0)
        if kind:
            return devices[1 if device is None else device]
        return devices

    sd.query_devices = query_devices
    sd.opened = []
    sd.played = []
    sd.play = lambda data, samplerate=None, **kw: sd.played.append((data, samplerate))

    class InputStream:
        def __init__(self, device, channels, samplerate, blocksize, callback):
            if devices[device or 1]["name"] == "Stereo Only" and channels == 1:
                raise _PortAudioError("Invalid number of channels")
            sd.opened.append((device, channels, samplerate, blocksize))
            self.callback, self.blocksize, self.channels = callback, blocksize, channels

        def feed(self, amplitude, blocks=1):
            for _ in range(blocks):
                self.callback(np.full((self.blocksize, self.channels), amplitude, dtype=np.float32),
                              self.blocksize, None, None)

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.InputStream = InputStream
    return sd


class AudioTests(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("sounddevice")
        self.sd = sys.modules["sounddevice"] = _fake_sounddevice()
        import stream_prompter.audio as audio
        self.audio = importlib.reload(audio)

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = self._saved

    def test_block_db(self):
        self.assertAlmostEqual(self.audio.block_db(np.ones(100)), 0.0, places=6)
        self.assertAlmostEqual(self.audio.block_db(np.full(100, 0.1)), -20.0, places=4)
        self.assertEqual(self.audio.block_db(np.zeros(100)), -120.0)

    def test_lists_only_input_devices(self):
        self.assertEqual(self.audio.list_input_devices(), {
            "USB Mic  (MME)": 1,
            "USB Mic  (Windows WASAPI)": 2,
            "Stereo Only  (Windows WASAPI)": 3,
        })

    def test_falls_back_to_stereo(self):
        mon = self.audio.MicMonitor(-40)
        self.assertEqual(mon.start(3), "Stereo Only")
        self.assertEqual(self.sd.opened[-1][:2], (3, 2))
        self.assertEqual(self.sd.opened[-1][3], 4410)  # 100 ms blocks at the device's rate

    def test_silence_and_speech_tracking(self):
        mon = self.audio.MicMonitor(-40)
        mon.start(None)
        before = mon.snapshot()[1]
        time.sleep(0.01)
        mon.stream.feed(0.001, blocks=5)           # -60 dB: quiet
        self.assertEqual(mon.snapshot()[1], before)
        self.assertEqual(mon.snapshot()[2], 0)
        mon.stream.feed(0.3, blocks=3)             # about -10 dB: talking
        level, last_sound, loud = mon.snapshot()
        self.assertGreater(last_sound, before)
        self.assertEqual(loud, 3)
        self.assertAlmostEqual(level, 20 * np.log10(0.3), places=3)

    def test_output_and_play(self):
        self.assertEqual(self.audio.default_output(), (48000, 2))
        data = np.zeros((10, 2), np.float32)
        self.audio.play(data, 48000)
        self.assertIs(self.sd.played[-1][0], data)
        self.assertEqual(self.sd.played[-1][1], 48000)

    def test_noise_floor_and_calibration(self):
        levels = [-55.0] * 28 + [-20.0, -18.0]      # quiet room with two short noises
        self.assertAlmostEqual(self.audio.noise_floor(levels), -55.0)
        self.assertIsNone(self.audio.noise_floor([]))
        noise, thr = self.audio.calibrated_threshold([-55.0] * 30, margin_db=8)
        self.assertEqual((round(noise), thr), (-55, -47))
        self.assertEqual(self.audio.calibrated_threshold([-5.0] * 30, 8)[1], -10)   # clamped


if __name__ == "__main__":
    unittest.main()
