import os
import random
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

from stream_prompter.prompts import (PromptPicker, PromptSet, decode_text, display_name,
                                     folder_signature, load_sets, parse_prompts)
from stream_prompter.storage import BUNDLED_SETS_DIR


def make_set(key, prompts):
    return PromptSet(key, display_name(key), Path(f"{key}.txt"), list(prompts))


class ParsingTests(unittest.TestCase):
    def test_parse_skips_comments_blanks_and_duplicates(self):
        text = "# header\n\n  First  \nSecond\n# note\nFirst\n\nThird\n"
        self.assertEqual(parse_prompts(text), ["First", "Second", "Third"])

    def test_display_name(self):
        self.assertEqual(display_name("hot_takes"), "Hot Takes")
        self.assertEqual(display_name("IRL Stories"), "IRL Stories")
        self.assertEqual(display_name("would_you__rather"), "Would You Rather")

    def test_decode_handles_common_windows_encodings(self):
        self.assertEqual(decode_text("﻿Café".encode("utf-8")), "Café")
        self.assertEqual(decode_text("Café".encode("utf-16")), "Café")
        self.assertEqual(decode_text("Café".encode("cp1252")), "Café")


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_sets_reads_txt_files_only(self):
        (self.dir / "b_set.txt").write_text("one\ntwo\n", encoding="utf-8")
        (self.dir / "A Set.TXT").write_text("x\n", encoding="utf-8")
        (self.dir / "notes.md").write_text("ignored\n", encoding="utf-8")
        (self.dir / ".hidden.txt").write_text("ignored\n", encoding="utf-8")
        sets = load_sets(self.dir)
        self.assertEqual([s.name for s in sets], ["A Set", "B Set"])
        self.assertEqual(sets[1].prompts, ["one", "two"])

    def test_missing_folder(self):
        self.assertEqual(load_sets(self.dir / "nope"), [])
        self.assertIsNone(folder_signature(self.dir / "nope"))

    def test_signature_changes_on_add_edit_remove(self):
        f = self.dir / "s.txt"
        f.write_text("a\n", encoding="utf-8")
        s1 = folder_signature(self.dir)
        f.write_text("a\nb\n", encoding="utf-8")
        future = time.time() + 5
        os.utime(f, (future, future))
        s2 = folder_signature(self.dir)
        (self.dir / "t.txt").write_text("c\n", encoding="utf-8")
        s3 = folder_signature(self.dir)
        f.unlink()
        s4 = folder_signature(self.dir)
        self.assertEqual(len({s1, s2, s3, s4}), 4)

    def test_bundled_sets_are_valid(self):
        sets = load_sets(BUNDLED_SETS_DIR)
        self.assertGreaterEqual(len(sets), 5)
        for s in sets:
            self.assertIsNone(s.error, s.key)
            self.assertGreaterEqual(len(s.prompts), 10, s.key)


class PickerTests(unittest.TestCase):
    def setUp(self):
        random.seed(1234)

    def test_empty(self):
        p = PromptPicker()
        p.update([])
        self.assertIsNone(p.next())
        p.update([make_set("empty", [])])
        self.assertIsNone(p.next())
        self.assertEqual(p.total, 0)

    def test_mixed_uses_every_prompt_before_repeating(self):
        a = make_set("a", [f"a{i}" for i in range(10)])
        b = make_set("b", ["b0", "b1"])
        p = PromptPicker()
        p.update([a, b])
        self.assertEqual(p.total, 12)
        for _ in range(5):
            round_ = [p.next()[0] for _ in range(12)]
            self.assertEqual(sorted(round_), sorted(a.prompts + b.prompts))

    def test_no_back_to_back_repeats(self):
        p = PromptPicker()
        p.update([make_set("a", ["x", "y"]), make_set("b", ["z"])])
        draws = [p.next()[0] for _ in range(300)]
        self.assertTrue(all(x != y for x, y in zip(draws, draws[1:])))

    def test_duplicates_across_sets_count_once(self):
        p = PromptPicker()
        p.update([make_set("a", ["same", "a1"]), make_set("b", ["same", "b1"])])
        self.assertEqual(p.total, 3)

    def test_balanced_gives_sets_equal_turns(self):
        big = make_set("big", [f"big{i}" for i in range(40)])
        small = make_set("small", ["s0", "s1"])
        p = PromptPicker()
        p.update([big, small], balanced=True)
        counts = Counter(p.next()[1] for _ in range(200))
        self.assertEqual(counts["big"], 100)
        self.assertEqual(counts["small"], 100)

    def test_mixed_favours_bigger_sets(self):
        big = make_set("big", [f"big{i}" for i in range(40)])
        small = make_set("small", ["s0", "s1"])
        p = PromptPicker()
        p.update([big, small])
        counts = Counter(p.next()[1] for _ in range(420))
        self.assertEqual(counts["big"], 400)
        self.assertEqual(counts["small"], 20)

    def test_toggling_sets_keeps_history(self):
        a = make_set("a", [f"a{i}" for i in range(6)])
        b = make_set("b", [f"b{i}" for i in range(4)])
        p = PromptPicker()
        p.update([a])
        used = {p.next()[0] for _ in range(4)}
        p.update([a, b])                      # switch b on
        next_six = [p.next()[0] for _ in range(6)]
        self.assertFalse(used & set(next_six), "prompts shown before the toggle came back too soon")

    def test_disabled_set_never_picked(self):
        a = make_set("a", ["a0", "a1"])
        b = make_set("b", ["b0", "b1"])
        p = PromptPicker()
        p.update([a, b])
        p.next()
        p.update([a])
        self.assertEqual({p.next()[1] for _ in range(50)}, {"a"})


if __name__ == "__main__":
    unittest.main()
