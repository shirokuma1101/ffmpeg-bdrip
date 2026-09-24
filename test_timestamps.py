"""Regression test for MKV files whose timestamps begin well after zero."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from bdmv2mkv import build_parser, find_tool, make_command


class TimestampTests(unittest.TestCase):
    def test_remux_resets_large_start_offset(self):
        ffmpeg = find_tool("ffmpeg")
        ffprobe = find_tool("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg/ffprobe not installed")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "offset.mkv"
            output = Path(folder) / "fixed.mkv"
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                 "testsrc2=size=64x64:rate=1", "-f", "lavfi", "-i",
                 "sine=frequency=440:sample_rate=48000", "-t", "2", "-c:v", "mpeg4",
                 "-c:a", "pcm_s16le", "-output_ts_offset", "4200", str(source)],
                check=True, capture_output=True,
            )
            args = build_parser().parse_args([str(source), str(output)])
            command = make_command(ffmpeg, args, source, output)
            self.assertNotIn("-copyts", command)
            subprocess.run(command, check=True, capture_output=True)
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=start_time,duration",
                 "-of", "json", str(output)],
                check=True, capture_output=True, text=True,
            )
            timing = json.loads(result.stdout)["format"]
            self.assertLess(abs(float(timing["start_time"])), 0.1)
            self.assertLess(float(timing["duration"]), 5)


if __name__ == "__main__":
    unittest.main()
