"""Tests for the bdpl JSON bridge."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ffmpeg_bdrip.bdmv_analysis import chapter_positions, collect_text_candidates, duration_text, episode_timeline, quick_scan_bdmv, scan_bdmv, title_playlist_links


class BdmvAnalysisTests(unittest.TestCase):
    def test_title_links_and_chapter_positions(self):
        data = {"analysis": {"disc_hints": {"title_playlists": {"1": [2, 2, 3], "2": [7]}}}}
        self.assertEqual(title_playlist_links(data), [("1", "00002.mpls"), ("1", "00003.mpls"), ("2", "00007.mpls")])
        playlist = {"play_items": [{"in_time": 450000, "duration_ms": 10000},
                                   {"in_time": 900000, "duration_ms": 20000}],
                    "chapters": [{"mark_id": 0, "play_item_ref": 0, "timestamp": 675000},
                                 {"mark_id": 1, "play_item_ref": 1, "timestamp": 1125000}]}
        self.assertEqual(chapter_positions(playlist), [(1, 5000), (2, 15000)])

    def test_episode_timeline_uses_only_its_segments(self):
        view = episode_timeline({"segments": [{"clip_id": "00007", "duration_ms": 1500, "label": "BODY"}]})
        self.assertEqual(view["play_items"], [{"m2ts": "00007.m2ts", "duration_ms": 1500, "label": "BODY"}])

    def test_duration_text(self):
        self.assertEqual(duration_text(3661000), "1:01:01")
        self.assertEqual(duration_text(None), "—")

    def test_scan_accepts_disc_root_and_reads_json(self):
        with tempfile.TemporaryDirectory() as folder:
            bdmv = Path(folder) / "BDMV"
            (bdmv / "PLAYLIST").mkdir(parents=True)
            data = {"schema_version": "bdpl.disc.v1", "playlists": []}
            with patch("ffmpeg_bdrip.bdmv_analysis.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps(data)
                self.assertEqual(scan_bdmv(Path(folder), "bdpl.exe"), data)
                self.assertEqual(run.call_args.args[0][:3], ["bdpl.exe", "scan", str(bdmv.resolve())])

    def test_rejects_missing_bdmv(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "BDMV/PLAYLIST"):
                scan_bdmv(Path(folder), "bdpl.exe")

    def test_quick_scan_lists_playlists_without_media(self):
        with tempfile.TemporaryDirectory() as folder:
            bdmv = Path(folder) / "BDMV"
            playlist = bdmv / "PLAYLIST"
            playlist.mkdir(parents=True)
            (playlist / "00001.mpls").write_bytes(b"MPLS")
            data = quick_scan_bdmv(Path(folder))
            self.assertEqual(data["scan_mode"], "quick")
            self.assertEqual(data["playlists"][0]["mpls"], "00001.mpls")
            self.assertIsNone(data["playlists"][0]["duration_ms"])

    def test_collects_metadata_and_jar_text_without_running_code(self):
        with tempfile.TemporaryDirectory() as folder:
            bdmv = Path(folder) / "BDMV"
            (bdmv / "PLAYLIST").mkdir(parents=True)
            meta = bdmv / "META" / "DL"
            meta.mkdir(parents=True)
            (meta / "bdmt_jpn.xml").write_text("<root><name>第一話の題名</name></root>", encoding="utf-8")
            jar = bdmv / "JAR"
            jar.mkdir()
            with zipfile.ZipFile(jar / "00000.jar", "w") as archive:
                archive.writestr("menu/messages.properties", "episode1=第一話の題名\nplay=再生する\n")
                label = "特典映像".encode("utf-8")
                archive.writestr("menu/Menu.class", b"\xca\xfe\xba\xbe\x00\x00\x00\x34\x00\x02\x01"
                                 + len(label).to_bytes(2, "big") + label)
            results = collect_text_candidates(Path(folder))
            self.assertIn({"source": "META/DL/bdmt_jpn.xml", "text": "第一話の題名"}, results)
            self.assertIn({"source": "JAR/00000.jar!/menu/messages.properties", "text": "再生する"}, results)
            self.assertIn({"source": "JAR/00000.jar!/menu/Menu.class", "text": "特典映像"}, results)
