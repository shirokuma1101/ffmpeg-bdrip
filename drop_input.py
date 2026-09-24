"""Validate file paths received from a Tk drag-and-drop event."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def file_from_drop(data: str, splitlist: Callable[[str], tuple[str, ...]]) -> Path:
    paths = splitlist(data)
    if len(paths) != 1:
        raise ValueError("入力ファイルは1つだけドロップしてください。")
    path = Path(paths[0]).expanduser()
    if not path.is_file():
        raise ValueError("ファイルをドロップしてください。フォルダは「フォルダ一覧…」から選択できます。")
    return path.resolve()
