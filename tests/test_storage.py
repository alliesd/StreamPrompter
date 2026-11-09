import json
import tempfile
import unittest
from pathlib import Path

from stream_prompter.storage import DEFAULT_SETTINGS, Storage, write_text_atomic


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.storage = Storage(self.root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_when_no_file(self):
        self.assertEqual(self.storage.load_settings(), DEFAULT_SETTINGS)

    def test_round_trip(self):
        s = self.storage.load_settings()
        s.update(threshold=-33.0, disabled_sets=["hot_takes"], balance_sets=True)
        self.storage.save_settings(s)
        loaded = self.storage.load_settings()
        self.assertEqual(loaded["threshold"], -33.0)
        self.assertEqual(loaded["disabled_sets"], ["hot_takes"])
        self.assertTrue(loaded["balance_sets"])

    def test_bad_values_fall_back_to_defaults(self):
        self.storage.settings_path.write_text(json.dumps({
            "threshold": -35,            # int is fine for a float setting
            "minutes": "five",           # wrong type
            "on_top": 1,                 # int is not a bool
            "disabled_sets": ["a", 2],   # list with a non-string
            "unknown": True,
        }), encoding="utf-8")
        s = self.storage.load_settings()
        self.assertEqual(s["threshold"], -35.0)
        self.assertIsInstance(s["threshold"], float)
        self.assertEqual(s["minutes"], DEFAULT_SETTINGS["minutes"])
        self.assertEqual(s["on_top"], DEFAULT_SETTINGS["on_top"])
        self.assertEqual(s["disabled_sets"], [])
        self.assertNotIn("unknown", s)

    def test_migrates_old_clear_on_speech_switch(self):
        self.storage.settings_path.write_text(json.dumps({"clear_on_speech": False}), encoding="utf-8")
        self.assertEqual(self.storage.load_settings()["hide_delay"], -1.0)
        self.storage.settings_path.write_text(json.dumps({"clear_on_speech": True}), encoding="utf-8")
        self.assertEqual(self.storage.load_settings()["hide_delay"], 0.0)
        self.storage.settings_path.write_text(json.dumps({"clear_on_speech": False, "hide_delay": 10}),
                                              encoding="utf-8")
        self.assertEqual(self.storage.load_settings()["hide_delay"], 10.0)

    def test_migrates_old_beep_switch(self):
        self.storage.settings_path.write_text(json.dumps({"beep": True}), encoding="utf-8")
        self.assertEqual(self.storage.load_settings()["sound"], "system")
        self.storage.settings_path.write_text(json.dumps({"beep": False}), encoding="utf-8")
        self.assertEqual(self.storage.load_settings()["sound"], "off")
        self.storage.settings_path.write_text(json.dumps({"beep": True, "sound": "builtin:pop"}),
                                              encoding="utf-8")
        self.assertEqual(self.storage.load_settings()["sound"], "builtin:pop")

    def test_corrupt_file(self):
        self.storage.settings_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.storage.load_settings(), DEFAULT_SETTINGS)

    def test_seeding_copies_once_and_respects_deletions(self):
        src = self.root / "bundled"
        src.mkdir()
        (src / "one.txt").write_text("1\n", encoding="utf-8")
        (src / "two.txt").write_text("2\n", encoding="utf-8")
        s = self.storage.load_settings()

        self.assertEqual(self.storage.seed_default_sets(s, src), ["one.txt", "two.txt"])
        (self.storage.default_sets_dir / "one.txt").unlink()           # user deletes a starter set
        (self.storage.default_sets_dir / "two.txt").write_text("mine\n", encoding="utf-8")  # and edits one
        (src / "three.txt").write_text("3\n", encoding="utf-8")          # an update ships a new one

        self.assertEqual(self.storage.seed_default_sets(s, src), ["three.txt"])
        self.assertFalse((self.storage.default_sets_dir / "one.txt").exists())
        self.assertEqual((self.storage.default_sets_dir / "two.txt").read_text(encoding="utf-8"), "mine\n")
        self.assertEqual(s["seeded_sets"], ["one.txt", "three.txt", "two.txt"])

    def test_atomic_write(self):
        p = self.root / "out.txt"
        write_text_atomic(p, "hello")
        write_text_atomic(p, "")
        self.assertEqual(p.read_text(encoding="utf-8"), "")
        self.assertFalse((self.root / "out.txt.tmp").exists())


if __name__ == "__main__":
    unittest.main()
