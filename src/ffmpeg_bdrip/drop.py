"""Validate file paths received from a Tk drag-and-drop event."""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def file_from_drop(data: str, splitlist: Callable[[str], tuple[str, ...]]) -> Path:
    paths = files_from_drop(data, splitlist)
    if len(paths) != 1:
        raise ValueError("入力ファイルは1つだけドロップしてください。")
    return paths[0]


def files_from_drop(data: str, splitlist: Callable[[str], tuple[str, ...]]) -> list[Path]:
    paths = [Path(value).expanduser() for value in splitlist(data)]
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("ファイルをドロップしてください。フォルダは「フォルダ一覧…」から選択できます。")
    return [path.resolve() for path in paths]
