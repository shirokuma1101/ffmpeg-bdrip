"""Tests for folder discovery and ffprobe summaries."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ffmpeg_bdrip.cli import find_tool
from ffmpeg_bdrip.probe import describe_stream, format_duration, iter_media_files, probe_media, stream_counts


class MediaProbeTests(unittest.TestCase):
    def test_folder_scan_respects_recursive_option(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nested = root / "nested"
            nested.mkdir()
            (root / "main.MKV").touch()
            (nested / "part.m2ts").touch()
            (root / "notes.txt").touch()
            self.assertEqual([path.name for path in iter_media_files(root)], ["main.MKV"])
            self.assertEqual({path.name for path in iter_media_files(root, recursive=True)}, {"main.MKV", "part.m2ts"})

    def test_summary_and_stream_details(self):
        result = {"format": {"duration": "3661.8"}, "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"index": 1, "codec_type": "audio", "codec_name": "flac", "channels": 2, "tags": {"language": "jpn"}},
            {"index": 2, "codec_type": "subtitle", "codec_name": "ass"},
        ]}
        self.assertEqual(format_duration(result), "1:01:01")
        self.assertEqual(stream_counts(result), (1, 1, 1))
        self.assertIn("1920×1080", describe_stream(result["streams"][0]))
        self.assertIn("jpn", describe_stream(result["streams"][1]))

    def test_bundled_ffprobe_reads_generated_media(self):
        ffmpeg = find_tool("ffmpeg")
        ffprobe = find_tool("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/ffprobe not installed")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.mkv"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=64x64:rate=1", "-t", "1", "-c:v", "mpeg4", str(path)], check=True)
            result = probe_media(path, ffprobe)
            self.assertEqual(stream_counts(result), (1, 0, 0))
            self.assertEqual(format_duration(result), "0:01")


if __name__ == "__main__":
    unittest.main()
