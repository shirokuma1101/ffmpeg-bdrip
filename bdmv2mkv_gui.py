"""Backward-compatible launcher for the ffmpeg-bdrip GUI."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ffmpeg_bdrip.gui import main  # noqa: E402


if __name__ == "__main__":
    main()
