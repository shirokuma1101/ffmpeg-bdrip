"""Tests for batch output planning and per-file stream mapping."""

import tempfile
import unittest
from pathlib import Path

from ffmpeg_bdrip.batch import batch_targets, resolve_streams, stream_ordinals


class BatchTests(unittest.TestCase):
    def test_targets_and_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, second = root / "a.ts", root / "b.mkv"
            self.assertEqual(batch_targets([first, second]),
                             [(first.resolve(), first.resolve().with_suffix(".mkv")),
                              (second.resolve(), second.resolve().with_name("b.remux.mkv"))])
            with self.assertRaisesRegex(ValueError, "重複"):
                batch_targets([first, first])
            with self.assertRaisesRegex(ValueError, "重複"):
                batch_targets([first, root / "a.mkv"])

    def test_custom_output_folder_and_name_collisions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_a = root / "a"
            source_b = root / "b"
            destination = root / "output"
            source_a.mkdir()
            source_b.mkdir()
            destination.mkdir()
            first, second = source_a / "movie.ts", source_b / "other.mkv"
            self.assertEqual(batch_targets([first, second], destination),
                             [(first.resolve(), destination.resolve() / "movie.mkv"),
                              (second.resolve(), destination.resolve() / "other.remux.mkv")])
            with self.assertRaisesRegex(ValueError, "出力先が重複"):
                batch_targets([first, source_b / "movie.ts"], destination)
            with self.assertRaisesRegex(ValueError, "出力フォルダが存在しません"):
                batch_targets([first], root / "missing")

    def test_selection_follows_stream_ordinals(self):
        source = [{"index": 0, "codec_type": "video"}, {"index": 2, "codec_type": "audio"},
                  {"index": 3, "codec_type": "audio"}]
        target = [{"index": 5, "codec_type": "audio"}, {"index": 6, "codec_type": "video"},
                  {"index": 7, "codec_type": "audio"}]
        selected = stream_ordinals(source, [(0, "video", "copy"), (3, "audio", "pcm_s24le")])
        self.assertEqual(resolve_streams(target, selected),
                         [(6, "video", "copy"), (7, "audio", "pcm_s24le")])
        with self.assertRaisesRegex(ValueError, "2本目"):
            resolve_streams(target[:2], selected)
