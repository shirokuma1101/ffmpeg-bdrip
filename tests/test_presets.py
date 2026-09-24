"""Tests for preset persistence and stream-relative mapping."""

from __future__ import annotations

import tempfile
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from ffmpeg_bdrip.gui import App
from ffmpeg_bdrip.presets import NEW_DEFAULT_NAME, default_presets, load_presets, save_presets


class Variable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeGUI:
    def __init__(self):
        self.vars = {"custom_maps": Variable(False), "deep": Variable(False), "strip": Variable(False),
                     "chapters": Variable(False), "audio_count": Variable(""), "start": Variable(""),
                     "duration": Variable("")}
        self.map_rows = {"audio": [], "subtitle": []}
        self.video_map_var = Variable("なし")
        self.stream_controls = [
            (4, "video", Variable(True), None),
            (5, "audio", Variable(True), Variable("copy")),
            (7, "audio", Variable(True), Variable("copy")),
            (8, "subtitle", Variable(True), None),
        ]
        self.map_options = {
            "video": [("#4 映像", 4)],
            "audio": [("#5 音声", 5), ("#7 音声", 7)],
            "subtitle": [("#8 字幕", 8)],
        }
        self.probe_status = SimpleNamespace(configure=lambda **_kwargs: None)

    def toggle_custom_maps(self):
        pass

    def add_map_row(self, kind, label):
        self.map_rows[kind].append((None, Variable(label), Variable("copy") if kind == "audio" else None))

    def namespace(self):
        return SimpleNamespace(stream_maps=App.selected_custom_maps(self))


class PresetTests(unittest.TestCase):
    def test_defaults_apply_using_stream_type_positions(self):
        gui = FakeGUI()
        preset = next(item for item in default_presets() if item["source"] == "bdrip_multiaudio_nosub.bat")
        App.apply_preset_streams(gui, preset)
        self.assertEqual(
            App.selected_custom_maps(gui),
            [(4, "video", "copy"), (5, "audio", "pcm_s24le"), (7, "audio", "pcm_s24le")],
        )
        self.assertFalse(gui.stream_controls[3][2].get())
        App.apply_preset_settings(gui, preset)
        saved = App.current_preset(gui, "再保存")
        self.assertEqual(saved["streams"], preset["streams"])
        self.assertTrue(saved["settings"]["deep_probe"])

    def test_missing_stream_does_not_change_selection(self):
        gui = FakeGUI()
        preset = next(item for item in default_presets() if item["source"] == "bdrip_multiaudio3.bat")
        with self.assertRaisesRegex(ValueError, "3 本目"):
            App.apply_preset_streams(gui, preset)
        self.assertTrue(all(control[2].get() for control in gui.stream_controls))

    def test_two_audio_with_subtitle_uses_pcm_for_both_tracks(self):
        gui = FakeGUI()
        preset = next(item for item in default_presets() if item["name"] == NEW_DEFAULT_NAME)
        App.apply_preset_streams(gui, preset)
        self.assertEqual(App.selected_custom_maps(gui), [
            (4, "video", "copy"), (5, "audio", "pcm_s24le"),
            (7, "audio", "pcm_s24le"), (8, "subtitle", "copy"),
        ])

    def test_new_default_is_added_to_legacy_file_and_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "presets.json"
            older = [preset for preset in default_presets() if preset["name"] != NEW_DEFAULT_NAME]
            path.write_text(json.dumps({"version": 1, "presets": older}, ensure_ascii=False), encoding="utf-8")
            loaded = load_presets(path)
            self.assertIn(NEW_DEFAULT_NAME, [preset["name"] for preset in loaded])
            save_presets([preset for preset in loaded if preset["name"] != NEW_DEFAULT_NAME], path)
            self.assertNotIn(NEW_DEFAULT_NAME, [preset["name"] for preset in load_presets(path)])

    def test_save_delete_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "presets.json"
            presets = load_presets(path)
            self.assertGreater(len(presets), 0)
            presets.append({"name": "個人用", "settings": {}, "streams": None})
            save_presets(presets, path)
            self.assertEqual(load_presets(path)[-1]["name"], "個人用")
            save_presets(presets[:-1], path)
            self.assertNotIn("個人用", [item["name"] for item in load_presets(path)])


if __name__ == "__main__":
    unittest.main()
