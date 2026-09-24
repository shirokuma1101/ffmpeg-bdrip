"""Tests for FFmpeg progress parsing and duration estimates."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from bdmv2mkv import build_parser, find_tool, make_command
from conversion_progress import estimate_remaining, gui_command, output_duration, parse_out_time


class ConversionProgressTests(unittest.TestCase):
    def test_eta_uses_remaining_media_time_and_speed(self):
        self.assertEqual(estimate_remaining(120, 30, "2x"), 45)
        self.assertEqual(estimate_remaining(120, 130, "2x"), 0)
        for total, speed in [(None, "2x"), (120, "N/A"), (120, "0x"), (120, "nanx")]:
            self.assertIsNone(estimate_remaining(total, 30, speed))

    def test_duration_accounts_for_source_offset_and_trim(self):
        data = {"format": {"start_time": "4200", "duration": "4260"}}
        args = build_parser().parse_args(["source.mkv", "out.mkv", "--start", "10", "--duration", "20"])
        self.assertEqual(output_duration(data, args), 20)
        args = build_parser().parse_args(["source.mkv", "out.mkv", "--start", "10"])
        self.assertEqual(output_duration(data, args), 50)
        self.assertIsNone(output_duration(None, args))

    def test_ffmpeg_emits_parseable_progress(self):
        ffmpeg = find_tool("ffmpeg")
        if not ffmpeg:
            self.skipTest("FFmpeg not installed")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mkv"
            target = Path(folder) / "target.mkv"
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=64x64:rate=2", "-t", "1", "-c:v", "mpeg4", str(source)],
                           check=True, capture_output=True)
            args = build_parser().parse_args([str(source), str(target)])
            result = subprocess.run(gui_command(make_command(ffmpeg, args, source, target)),
                                    check=True, capture_output=True, text=True)
            times = [parse_out_time(line.split("=", 1)[1]) for line in result.stdout.splitlines()
                     if line.startswith("out_time=")]
            self.assertTrue(times)
            self.assertAlmostEqual(times[-1], 1, delta=0.2)
            self.assertIn("progress=end", result.stdout)


if __name__ == "__main__":
    unittest.main()
