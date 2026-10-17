"""Resolve GUI stream selections against each file in a conversion batch."""

from __future__ import annotations

from pathlib import Path

from .cli import default_output


def batch_targets(paths: list[Path], output_folder: Path | None = None) -> list[tuple[Path, Path]]:
    if output_folder is not None:
        output_folder = output_folder.expanduser().resolve()
        if not output_folder.is_dir():
            raise ValueError(f"出力フォルダが存在しません: {output_folder}")
    targets = [(path.resolve(), (output_folder / default_output(path).name if output_folder
                                else default_output(path.resolve()))) for path in paths]
    outputs = [output for _, output in targets]
    sources = {source for source, _ in targets}
    if len(set(outputs)) != len(outputs):
        raise ValueError("複数の入力ファイルで出力先が重複します。")
    if any(output in sources for output in outputs):
        raise ValueError("出力先が別の入力ファイルと重複します。")
    return targets


def stream_ordinals(streams: list[dict], selection: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    positions: dict[str, int] = {}
    ordinals: dict[tuple[str, int], int] = {}
    for stream in streams:
        kind, index = stream.get("codec_type"), stream.get("index")
        if isinstance(index, int):
            ordinals[(kind, index)] = positions.get(kind, 0)
            positions[kind] = positions.get(kind, 0) + 1
    try:
        return [(ordinals[(kind, index)], kind, codec) for index, kind, codec in selection]
    except KeyError as exc:
        raise ValueError("選択されたストリームが解析結果に見つかりません。") from exc


def resolve_streams(streams: list[dict], selection: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    grouped: dict[str, list[int]] = {}
    for stream in streams:
        kind, index = stream.get("codec_type"), stream.get("index")
        if isinstance(index, int):
            grouped.setdefault(kind, []).append(index)
    resolved = []
    for ordinal, kind, codec in selection:
        if ordinal >= len(grouped.get(kind, [])):
            raise ValueError(f"{kind}の{ordinal + 1}本目がありません。")
        resolved.append((grouped[kind][ordinal], kind, codec))
    return resolved
