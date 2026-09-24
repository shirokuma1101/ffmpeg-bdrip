"""Persistent GUI presets; stream positions are relative to each stream type."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


PRESETS_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ffmpeg-bdrip" / "presets.json"
NEW_DEFAULT_NAME = "音声2本（字幕あり・PCM）"


def default_presets() -> list[dict]:
    common = {"deep_probe": True, "strip_metadata": True, "no_chapters": True,
              "audio_count": "", "start": "", "duration": ""}

    def stream(kind: str, ordinal: int, codec: str = "copy") -> dict:
        return {"type": kind, "ordinal": ordinal, "codec": codec}

    video = stream("video", 0)
    audio_pcm = stream("audio", 0, "pcm_s24le")
    subtitle = stream("subtitle", 0)
    return [
        {"name": "通常（字幕あり・音声PCM）", "source": "bdrip_normal.bat", "settings": common.copy(),
         "streams": [video, audio_pcm, subtitle]},
        {"name": "通常（字幕なし・音声PCM）", "source": "bdrip_normal_nosub.bat", "settings": common.copy(),
         "streams": [video, audio_pcm]},
        {"name": "音声2本（字幕なし・PCM）", "source": "bdrip_multiaudio_nosub.bat", "settings": common.copy(),
         "streams": [video, audio_pcm, stream("audio", 1, "pcm_s24le")]},
        {"name": NEW_DEFAULT_NAME, "source": "bdrip_multiaudio_nosub.bat + bdrip_normal.bat",
         "settings": common.copy(),
         "streams": [video, audio_pcm, stream("audio", 1, "pcm_s24le"), subtitle]},
        {"name": "音声3本（字幕あり・PCM）", "source": "bdrip_multiaudio3.bat", "settings": common.copy(),
         "streams": [video, audio_pcm, stream("audio", 1, "pcm_s24le"), stream("audio", 2, "pcm_s24le"), subtitle]},
        {"name": "映像＋音声コピー", "source": "conv.bat", "settings": common.copy(),
         "streams": [video, stream("audio", 0)]},
        {"name": "映像・音声・字幕をコピー", "source": "一般設定",
         "settings": {"deep_probe": False, "strip_metadata": False, "no_chapters": False,
                      "audio_count": "", "start": "", "duration": ""},
         "streams": None},
    ]


def load_presets(path: Path = PRESETS_PATH) -> list[dict]:
    if not path.exists():
        return default_presets()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("presets"), list):
        raise ValueError("プリセットファイルの形式が正しくありません。")
    for preset in data["presets"]:
        if not isinstance(preset, dict) or not isinstance(preset.get("name"), str):
            raise ValueError("プリセットの名前が正しくありません。")
    deleted_defaults = data.get("deleted_defaults", [])
    if not isinstance(deleted_defaults, list):
        raise ValueError("プリセットの削除履歴が正しくありません。")
    presets = data["presets"]
    if NEW_DEFAULT_NAME not in {preset["name"] for preset in presets} and NEW_DEFAULT_NAME not in deleted_defaults:
        addition = next(preset for preset in default_presets() if preset["name"] == NEW_DEFAULT_NAME)
        previous = next((index for index, preset in enumerate(presets)
                         if preset["name"] == "音声2本（字幕なし・PCM）"), len(presets) - 1)
        presets.insert(previous + 1, addition)
    return presets


def save_presets(presets: list[dict], path: Path = PRESETS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".presets-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            names = {preset["name"] for preset in presets}
            deleted_defaults = [preset["name"] for preset in default_presets() if preset["name"] not in names]
            json.dump({"version": 1, "presets": presets, "deleted_defaults": deleted_defaults},
                      file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
