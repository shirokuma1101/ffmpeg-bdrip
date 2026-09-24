"""Shared ffprobe helpers for a file and a folder of media files."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


MEDIA_EXTENSIONS = {".ts", ".m2ts", ".mts", ".mkv", ".mp4", ".m4v", ".mov",
                    ".avi", ".webm", ".vro", ".vob", ".mpg", ".mpeg", ".mka"}
SHOW_ENTRIES = (
    "format=duration,start_time,format_name:"
    "stream=index,codec_type,codec_name,width,height,channels,channel_layout:"
    "stream_tags=language,title:stream_disposition=default"
)


def iter_media_files(folder: Path, recursive: bool = False):
    files = folder.rglob("*") if recursive else folder.iterdir()
    for path in sorted(files, key=lambda item: str(item).casefold()):
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
            yield path


def probe_media(path: Path, executable: str, timeout: int = 60) -> dict:
    result = subprocess.run(
        [executable, "-v", "error", "-show_entries", SHOW_ENTRIES, "-of", "json", os.fspath(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"ffprobe終了コード: {result.returncode}")
    data = json.loads(result.stdout)
    if not isinstance(data.get("streams"), list):
        raise ValueError("ストリーム情報を取得できませんでした。")
    return data


def format_duration(data: dict) -> str:
    duration = data.get("format", {}).get("duration")
    try:
        seconds = int(float(duration))
    except (TypeError, ValueError):
        return "—"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def describe_stream(stream: dict) -> str:
    kind = stream.get("codec_type", "unknown")
    details = [f"#{stream.get('index', '?')}",
               {"video": "映像", "audio": "音声", "subtitle": "字幕", "attachment": "添付"}.get(kind, kind),
               stream.get("codec_name") or "不明"]
    if kind == "video" and stream.get("width") and stream.get("height"):
        details.append(f"{stream['width']}×{stream['height']}")
    if kind == "audio":
        if stream.get("channel_layout"):
            details.append(str(stream["channel_layout"]))
        elif stream.get("channels"):
            details.append(f"{stream['channels']}ch")
    tags = stream.get("tags") or {}
    if tags.get("language"):
        details.append(str(tags["language"]))
    if tags.get("title"):
        details.append(str(tags["title"]))
    if (stream.get("disposition") or {}).get("default"):
        details.append("既定")
    return " ・ ".join(details)


def stream_counts(data: dict) -> tuple[int, int, int]:
    streams = data.get("streams", [])
    return tuple(sum(stream.get("codec_type") == kind for stream in streams)
                 for kind in ("video", "audio", "subtitle"))
