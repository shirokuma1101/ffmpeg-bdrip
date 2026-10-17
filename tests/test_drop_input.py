"""Tests for input-file drag and drop handling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ffmpeg_bdrip.drop import file_from_drop, files_from_drop
from ffmpeg_bdrip.gui import App


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class DropInputTests(unittest.TestCase):
    def test_file_with_spaces_and_single_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "movie one.mkv"
            path.touch()
            raw = "{" + str(path) + "}"
            self.assertEqual(file_from_drop(raw, lambda data: (data[1:-1],)), path.resolve())
            with self.assertRaisesRegex(ValueError, "1つだけ"):
                file_from_drop(raw, lambda _data: (str(path), str(path)))
            with self.assertRaisesRegex(ValueError, "ファイルをドロップ"):
                file_from_drop(raw, lambda _data: (str(Path(folder)),))
            self.assertEqual(files_from_drop(raw, lambda _data: (str(path), str(path))),
                             [path.resolve(), path.resolve()])

    def test_new_input_updates_only_automatic_output(self):
        old = Path("old.mkv")
        new = Path("new.mkv")
        gui = type("FakeGUI", (), {})()
        gui.vars = {"input": Variable(str(old)), "output": Variable("old.remux.mkv")}
        App.set_input_file(gui, new)
        self.assertEqual(gui.vars["output"].get(), "new.remux.mkv")
        gui.vars["output"].set("custom.mkv")
        App.set_input_file(gui, Path("third.mkv"))
        self.assertEqual(gui.vars["output"].get(), "custom.mkv")


if __name__ == "__main__":
    unittest.main()
