"""Read bdpl's structured BDMV analysis without modifying disc files."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def scan_bdmv(path: Path, executable: str | None = None) -> dict:
    folder = path.expanduser().resolve()
    if folder.name.upper() != "BDMV":
        folder = folder / "BDMV"
    if not (folder / "PLAYLIST").is_dir():
        raise ValueError("BDMV/PLAYLIST を含むフォルダを選択してください。")
    tool = executable or shutil.which("bdpl")
    if not tool:
        raise ValueError("bdpl が見つかりません。bdpl をインストールして PATH に追加してください。")
    result = subprocess.run([tool, "scan", str(folder), "--stdout", "--compact"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if result.returncode:
        raise ValueError(f"bdpl の解析に失敗しました: {result.stderr.strip() or result.stdout.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("bdpl のJSON出力を読み取れませんでした。") from exc
    if data.get("schema_version") != "bdpl.disc.v1" or not isinstance(data.get("playlists"), list):
        raise ValueError("対応していない bdpl の解析形式です。")
    return data


def quick_scan_bdmv(path: Path) -> dict:
    """Inventory BDMV metadata only, without reading any M2TS media stream."""
    folder = path.expanduser().resolve()
    if folder.name.upper() != "BDMV":
        folder /= "BDMV"
    playlist_dir = folder / "PLAYLIST"
    if not playlist_dir.is_dir():
        raise ValueError("BDMV/PLAYLIST を含むフォルダを選択してください。")
    playlists = [{"mpls": file.name, "duration_ms": None, "play_items": [], "chapters": []}
                 for file in sorted(playlist_dir.glob("*.mpls")) if file.is_file()]
    texts = collect_text_candidates(folder)
    title = next((entry["text"] for entry in texts if entry["source"].lower().startswith("meta/dl/bdmt_")), None)
    return {"disc": {"path": str(folder), "title": title}, "playlists": playlists,
            "episodes": [], "warnings": [], "text_candidates": texts, "scan_mode": "quick"}


def duration_text(milliseconds: float | None) -> str:
    if milliseconds is None:
        return "—"
    seconds = max(0, int(milliseconds / 1000))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def title_playlist_links(data: dict) -> list[tuple[str, str]]:
    """Return title-number to MPLS links from bdpl navigation hints."""
    hints = (data.get("analysis") or {}).get("disc_hints") or {}
    links = []
    seen = set()
    for title, numbers in (hints.get("title_playlists") or {}).items():
        for number in numbers:
            try:
                link = (str(title), f"{int(number):05d}.mpls")
                if link not in seen:
                    seen.add(link)
                    links.append(link)
            except (TypeError, ValueError):
                continue
    return links


def episode_timeline(episode: dict) -> dict:
    """Show an inferred episode's segments, not its entire Play-All playlist."""
    return {"play_items": [
        {"m2ts": f"{segment.get('clip_id', '?')}.m2ts",
         "duration_ms": segment.get("duration_ms", 0), "label": segment.get("label")}
        for segment in episode.get("segments", [])
    ], "chapters": []}


def chapter_positions(playlist: dict) -> list[tuple[int, float]]:
    """Convert 45-kHz absolute chapter ticks to playlist-relative milliseconds."""
    items = playlist.get("play_items") or []
    offsets = []
    elapsed = 0.0
    for item in items:
        offsets.append(elapsed)
        elapsed += max(0.0, float(item.get("duration_ms") or 0))
    result = []
    for chapter in playlist.get("chapters") or []:
        try:
            index = int(chapter["play_item_ref"])
            if not 0 <= index < len(items):
                continue
            ticks = float(chapter["timestamp"]) - float(items[index]["in_time"])
            position = offsets[index] + max(0.0, ticks / 45)
            if position <= elapsed + 1000:
                result.append((int(chapter.get("mark_id", len(result))) + 1, position))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(result, key=lambda entry: entry[1])


_TEXT_SUFFIXES = {".xml", ".txt", ".properties", ".json"}
_MAX_FILE_BYTES = 2_000_000
_MAX_FILES = 300
_MAX_RESULTS = 500


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _useful(value: str) -> str | None:
    value = " ".join(value.split())
    if not 2 <= len(value) <= 250 or not re.search(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]", value):
        return None
    if re.fullmatch(r"[0-9_.\\/:@-]+", value) or "/" in value or "\\" in value:
        return None
    return value


def _extract_text(raw: bytes, suffix: str) -> list[str]:
    if suffix == ".class":
        return _class_strings(raw)
    content = _decode(raw)
    if suffix == ".xml":
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return []
        return [text for elem in root.iter() if elem.text and (text := _useful(elem.text))]
    if suffix == ".json":
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return []
        values = []
        def visit(item):
            if isinstance(item, str):
                values.append(item)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
            elif isinstance(item, dict):
                for child in item.values():
                    visit(child)
        visit(content)
        return [text for value in values if (text := _useful(value))]
    values = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!", ";")):
            continue
        if suffix == ".properties" and "=" in line:
            line = line.split("=", 1)[1]
        if text := _useful(line):
            values.append(text)
    return values


def _class_strings(raw: bytes) -> list[str]:
    """Extract Java constant-pool UTF-8 entries without loading the class."""
    if len(raw) < 10 or raw[:4] != b"\xca\xfe\xba\xbe":
        return []
    count = int.from_bytes(raw[8:10], "big")
    offset = 10
    values = []
    index = 1
    sizes = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4,
             11: 4, 12: 4, 15: 3, 16: 2, 17: 4, 18: 4, 19: 2, 20: 2}
    while index < count and offset < len(raw):
        tag = raw[offset]
        offset += 1
        if tag == 1:
            if offset + 2 > len(raw):
                break
            length = int.from_bytes(raw[offset:offset + 2], "big")
            offset += 2
            if offset + length > len(raw):
                break
            candidate = _useful(raw[offset:offset + length].decode("utf-8", errors="replace"))
            if candidate and not candidate.startswith(("java.", "javax.", "org.")):
                values.append(candidate)
            offset += length
        elif tag in sizes:
            offset += sizes[tag]
            if tag in (5, 6):
                index += 1
        else:
            break
        index += 1
    return values


def collect_text_candidates(path: Path) -> list[dict[str, str]]:
    """Read bounded metadata/resources; never execute BD-J code or unpack archives."""
    folder = path.expanduser().resolve()
    if folder.name.upper() != "BDMV":
        folder /= "BDMV"
    if not (folder / "PLAYLIST").is_dir():
        raise ValueError("BDMV/PLAYLIST を含むフォルダを選択してください。")
    results: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, raw: bytes, suffix: str) -> None:
        for value in _extract_text(raw, suffix):
            key = (source, value)
            if key not in seen and len(results) < _MAX_RESULTS:
                seen.add(key)
                results.append({"source": source, "text": value})

    meta = folder / "META" / "DL"
    if meta.is_dir():
        for file in sorted(meta.glob("bdmt_*.xml"))[:_MAX_FILES]:
            if file.is_file() and file.stat().st_size <= _MAX_FILE_BYTES:
                try:
                    add(file.relative_to(folder).as_posix(), file.read_bytes(), ".xml")
                except (OSError, ValueError):
                    continue
    jar_folder = folder / "JAR"
    if jar_folder.is_dir():
        files_seen = 0
        for file in sorted(jar_folder.rglob("*")):
            if files_seen >= _MAX_FILES or len(results) >= _MAX_RESULTS:
                break
            if file.suffix.lower() not in _TEXT_SUFFIXES or not file.is_file() or not file.resolve().is_relative_to(folder):
                continue
            files_seen += 1
            try:
                if file.stat().st_size <= _MAX_FILE_BYTES:
                    add(file.relative_to(folder).as_posix(), file.read_bytes(), file.suffix.lower())
            except (OSError, ValueError):
                continue
        for archive in sorted(jar_folder.glob("*.jar"))[:_MAX_FILES]:
            try:
                with zipfile.ZipFile(archive) as zipped:
                    for member in zipped.infolist():
                        if files_seen >= _MAX_FILES or len(results) >= _MAX_RESULTS:
                            break
                        suffix = Path(member.filename).suffix.lower()
                        if member.is_dir() or suffix not in _TEXT_SUFFIXES | {".class"} or member.file_size > _MAX_FILE_BYTES:
                            continue
                        files_seen += 1
                        add(f"JAR/{archive.name}!/{member.filename}", zipped.read(member), suffix)
            except (OSError, zipfile.BadZipFile, RuntimeError, ValueError):
                continue
    return results
