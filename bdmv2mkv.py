"""Backward-compatible launcher for the ffmpeg-bdrip CLI."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ffmpeg_bdrip.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
