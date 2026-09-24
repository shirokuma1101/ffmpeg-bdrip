"""Lossless remuxer for TS/M2TS and MKV files."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ffmpeg-bdrip",
        description="TS/M2TS/MKVを再エンコードせずMKVへ変換します。",
    )
    parser.add_argument("input", type=Path, help="入力TS/M2TS/MKVファイル")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="出力MKVファイル（省略時は入力と同じ場所・同じ名前）",
    )
    parser.add_argument(
        "--ffmpeg",
        default="ffmpeg",
        help="使用するffmpegの実行ファイルまたはパス（既定: 同梱版、なければPATH）",
    )
    parser.add_argument(
        "-y", "--overwrite", action="store_true", help="既存の出力を上書きする"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="実行するコマンドだけ表示する"
    )
    parser.add_argument(
        "--loglevel",
        choices=("quiet", "error", "warning", "info", "verbose", "debug"),
        default="info",
        help="ffmpegのログレベル（既定: info）",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="解析量とprobeサイズを最大値（2147483647）にする",
    )
    parser.add_argument("--strip-metadata", action="store_true", help="メタデータを削除する")
    parser.add_argument("--no-chapters", action="store_true", help="チャプターを削除する")
    parser.add_argument("--start", type=float, metavar="SECONDS", help="出力開始位置（秒）")
    parser.add_argument("--duration", type=float, metavar="SECONDS", help="出力時間（秒）")
    parser.add_argument(
        "--video-map", metavar="STREAM", help="映像ストリーム（例: 0:0）。指定時は明示マッピング"
    )
    parser.add_argument(
        "--audio-map",
        action="append",
        default=[],
        metavar="STREAM",
        help="音声ストリーム（複数指定可。例: 0:1）",
    )
    parser.add_argument(
        "--audio-codec",
        action="append",
        default=[],
        metavar="CODEC",
        help="音声codec（--audio-mapと同じ順番。例: copy, pcm_s24le）",
    )
    parser.add_argument(
        "--audio-count",
        type=int,
        metavar="N",
        help="入力音声を先頭から最大N本だけ含める（省略時: 全て）",
    )
    parser.add_argument(
        "--subtitle-map",
        action="append",
        default=[],
        metavar="STREAM",
        help="字幕ストリーム（複数指定可。例: 0:s:0）",
    )
    parser.add_argument(
        "--extra-audio",
        action="append",
        default=[],
        type=Path,
        metavar="FILE",
        help="外部音声ファイルを追加する（複数指定可）",
    )
    return parser


def default_output(input_path: Path) -> Path:
    if input_path.suffix.lower() == ".mkv":
        return input_path.with_name(f"{input_path.stem}.remux.mkv")
    return input_path.with_suffix(".mkv")


def find_tool(name: str) -> str | None:
    bundled = Path(__file__).resolve().parent / "vendor" / "ffmpeg" / "bin" / f"{name}.exe"
    if bundled.is_file():
        return os.fspath(bundled)
    return shutil.which(name)


def make_command(ffmpeg: str, args: argparse.Namespace, input_path: Path, output_path: Path) -> list[str]:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        args.loglevel,
        "-y" if args.overwrite else "-n",
    ]
    if args.deep_probe:
        command += ["-analyzeduration", "2147483647", "-probesize", "2147483647"]
    command += ["-i", os.fspath(input_path)]
    for extra_audio in args.extra_audio:
        if args.deep_probe:
            command += ["-analyzeduration", "2147483647", "-probesize", "2147483647"]
        command += ["-i", os.fspath(extra_audio)]

    stream_maps = getattr(args, "stream_maps", None)
    explicit_maps = args.video_map or args.audio_map or args.subtitle_map
    audio_maps = args.audio_map
    audio_codecs = args.audio_codec
    if args.audio_count is not None and explicit_maps:
        audio_maps = audio_maps[: args.audio_count]
        audio_codecs = audio_codecs[: args.audio_count]

    if stream_maps is not None:
        command += ["-c", "copy"]
        audio_index = 0
        for stream_index, kind, codec in stream_maps:
            command += ["-map", f"0:{stream_index}"]
            if kind == "audio":
                command += [f"-c:a:{audio_index}", codec]
                audio_index += 1
    elif explicit_maps:
        command += ["-c", "copy"]
        if args.video_map:
            command += ["-map", args.video_map]
        for index, stream in enumerate(audio_maps):
            codec = audio_codecs[index] if index < len(audio_codecs) else "copy"
            command += ["-map", stream, f"-c:a:{index}", codec]
        for stream in args.subtitle_map:
            command += ["-map", stream]
    else:
        if args.audio_count is None:
            command += ["-map", "0"]
        else:
            command += ["-map", "0", "-map", "-0:a"]
            for index in range(args.audio_count):
                command += ["-map", f"0:a:{index}?"]
        command += ["-c", "copy"]
    for index in range(len(args.extra_audio)):
        command += ["-map", f"{index + 1}:a"]

    if args.strip_metadata:
        command += ["-map_metadata", "-1"]
    else:
        command += ["-map_metadata", "0"]
    command += ["-map_chapters", "-1" if args.no_chapters else "0"]
    if args.start is not None:
        command += ["-ss", str(args.start)]
    if args.duration is not None:
        command += ["-t", str(args.duration)]
    command += ["-avoid_negative_ts", "make_zero", os.fspath(output_path)]
    return command


def run(args: argparse.Namespace) -> int:
    input_path = args.input.expanduser().resolve()
    output_path = (args.output or default_output(input_path)).expanduser().resolve()

    if not input_path.is_file():
        print(f"入力ファイルが見つかりません: {input_path}", file=sys.stderr)
        return 2
    if input_path.suffix.lower() not in {".ts", ".m2ts", ".mts", ".mkv"}:
        print("警告: 入力拡張子はTS/MKV系ではありません。ffmpegで読み込みを試みます。", file=sys.stderr)
    for extra_audio in args.extra_audio:
        if not extra_audio.expanduser().is_file():
            print(f"外部音声ファイルが見つかりません: {extra_audio}", file=sys.stderr)
            return 2
    if args.audio_count is not None and args.audio_count < 0:
        print("--audio-countは0以上で指定してください。", file=sys.stderr)
        return 2
    if len(args.audio_codec) > len(args.audio_map):
        print("--audio-codecの数は--audio-map以下にしてください。", file=sys.stderr)
        return 2
    if input_path == output_path:
        print("入力と出力に同じファイルは指定できません。", file=sys.stderr)
        return 2
    if output_path.exists() and not args.overwrite:
        print(f"出力ファイルが既に存在します（-yで上書き）: {output_path}", file=sys.stderr)
        return 2

    ffmpeg = find_tool("ffmpeg") if args.ffmpeg == "ffmpeg" else (shutil.which(args.ffmpeg) or (args.ffmpeg if Path(args.ffmpeg).is_file() else None))
    if not ffmpeg and not args.dry_run:
        print("ffmpegが見つかりません。vendor/ffmpeg/bin、PATHまたは--ffmpegを確認してください。", file=sys.stderr)
        return 2
    executable = ffmpeg or args.ffmpeg
    command = make_command(executable, args, input_path, output_path)
    print("実行: " + subprocess.list2cmdline(command))
    if args.dry_run:
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(command)
    if completed.returncode == 0:
        print(f"完了: {output_path}")
    else:
        print(f"ffmpegが失敗しました（終了コード: {completed.returncode}）", file=sys.stderr)
    return completed.returncode


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
