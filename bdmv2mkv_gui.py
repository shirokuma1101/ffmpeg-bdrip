"""Tkinter GUI for ffmpeg-bdrip."""

from __future__ import annotations

import math
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from bdmv2mkv import default_output, find_tool, make_command
from conversion_progress import estimate_remaining, format_time, gui_command, output_duration, parse_out_time
from drop_input import file_from_drop
from media_probe import describe_stream, format_duration, iter_media_files, probe_media, stream_counts
from presets import load_presets, save_presets

bundled_dnd = Path(__file__).resolve().parent / "vendor" / "python"
if sys.platform == "win32" and (bundled_dnd / "tkinterdnd2").is_dir():
    sys.path.insert(0, str(bundled_dnd))

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    RootWindow = tk.Tk
else:
    RootWindow = TkinterDnD.Tk


class App(RootWindow):
    def __init__(self) -> None:
        super().__init__()
        self.title("ffmpeg-bdrip")
        self.minsize(760, 710)
        self.process: subprocess.Popen[str] | None = None
        self.process_output: queue.Queue[str | None] = queue.Queue()
        self.progress_total: float | None = None
        self.progress_time = 0.0
        self.progress_speed = ""
        self.cancel_requested = False
        self.vars: dict[str, tk.Variable] = {}
        self.probe_results: queue.Queue[tuple[int, Path, dict | Exception]] = queue.Queue()
        self.probe_generation = 0
        self.probe_job: str | None = None
        self.probed_path: Path | None = None
        self.probe_data: dict | None = None
        self.stream_controls: list[tuple[int, str, tk.BooleanVar, tk.StringVar | None]] = []
        self.map_options: dict[str, list[tuple[str, int]]] = {"video": [], "audio": [], "subtitle": []}
        self.map_rows: dict[str, list[tuple[ttk.Frame, tk.StringVar, tk.StringVar | None]]] = {"audio": [], "subtitle": []}
        self.pending_preset: dict | None = None
        try:
            self.presets = load_presets()
            preset_error = None
        except (OSError, ValueError) as exc:
            self.presets = []
            preset_error = str(exc)
        self._build()
        if preset_error:
            self.after(0, lambda: messagebox.showerror("プリセット読み込みエラー", preset_error))
        self.vars["input"].trace_add("write", self.schedule_probe)
        self.after(100, self.poll_probe)

    def var(self, name: str, value: str | bool = "") -> tk.Variable:
        self.vars[name] = tk.BooleanVar(self, value=value) if isinstance(value, bool) else tk.StringVar(self, value=value)
        return self.vars[name]

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(root, text="入力ファイル").grid(row=row, column=0, sticky="w", pady=4)
        self.input_entry = ttk.Entry(root, textvariable=self.var("input"))
        self.input_entry.grid(row=row, column=1, sticky="ew", pady=4)
        input_buttons = ttk.Frame(root)
        input_buttons.grid(row=row, column=2, padx=(6, 0))
        ttk.Button(input_buttons, text="ファイル…", command=self.choose_input).pack(side="left")
        ttk.Button(input_buttons, text="フォルダ一覧…", command=self.open_folder_probe).pack(side="left", padx=(6, 0))
        row += 1
        self.drop_hint = ttk.Label(root, text="入力ファイルをここへドラッグ＆ドロップできます。")
        self.drop_hint.grid(row=row, column=1, columnspan=2, sticky="w")
        self.enable_input_drop()
        row += 1
        ttk.Label(root, text="出力MKV").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(root, textvariable=self.var("output")).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(root, text="保存先…", command=self.choose_output).grid(row=row, column=2, padx=(6, 0))
        row += 1

        presets = ttk.LabelFrame(root, text="プリセット", padding=8)
        presets.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        presets.columnconfigure(0, weight=1)
        self.preset_name = tk.StringVar(self)
        self.preset_combo = ttk.Combobox(
            presets, textvariable=self.preset_name, values=[preset["name"] for preset in self.presets], state="readonly",
        )
        self.preset_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(presets, text="適用", command=self.apply_preset).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(presets, text="現在の設定を保存", command=self.save_preset).grid(row=0, column=2, padx=(6, 0))
        ttk.Button(presets, text="削除", command=self.delete_preset).grid(row=0, column=3, padx=(6, 0))
        row += 1

        streams = ttk.LabelFrame(root, text="入力ファイルの解析・ストリーム選択", padding=8)
        streams.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        streams.columnconfigure(0, weight=1)
        self.probe_status = ttk.Label(streams, text="入力ファイルを選ぶとffprobeで解析します。")
        self.probe_status.grid(row=0, column=0, sticky="w")
        ttk.Button(streams, text="再解析", command=self.start_probe).grid(row=0, column=1, padx=(8, 0))
        self.stream_canvas = tk.Canvas(streams, height=170, highlightthickness=0)
        self.stream_canvas.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        scrollbar = ttk.Scrollbar(streams, orient="vertical", command=self.stream_canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.stream_canvas.configure(yscrollcommand=scrollbar.set)
        self.stream_frame = ttk.Frame(self.stream_canvas)
        self.stream_window = self.stream_canvas.create_window((0, 0), window=self.stream_frame, anchor="nw")
        self.stream_frame.bind("<Configure>", lambda _event: self.stream_canvas.configure(scrollregion=self.stream_canvas.bbox("all")))
        self.stream_canvas.bind("<Configure>", lambda event: self.stream_canvas.itemconfigure(self.stream_window, width=event.width))
        row += 1

        options = ttk.LabelFrame(root, text="基本設定", padding=8)
        options.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 5))
        for column in range(6):
            options.columnconfigure(column, weight=1 if column in (1, 3, 5) else 0)
        self.check(options, "deep", "深い解析", 0, 0)
        self.check(options, "strip", "メタデータ削除", 0, 2)
        self.check(options, "chapters", "チャプター削除", 0, 4)
        self.check(options, "overwrite", "既存ファイルを上書き", 1, 0)
        ttk.Label(options, text="音声本数（空欄=全て）").grid(row=1, column=2, sticky="w")
        ttk.Entry(options, textvariable=self.var("audio_count"), width=8).grid(row=1, column=3, sticky="w")
        ttk.Label(options, text="開始秒").grid(row=1, column=4, sticky="e")
        ttk.Entry(options, textvariable=self.var("start"), width=10).grid(row=1, column=5, sticky="w")
        ttk.Label(options, text="時間秒").grid(row=2, column=4, sticky="e")
        ttk.Entry(options, textvariable=self.var("duration"), width=10).grid(row=2, column=5, sticky="w")
        row += 1

        maps = ttk.LabelFrame(root, text="詳細設定：出力順とmapを指定", padding=8)
        maps.grid(row=row, column=0, columnspan=3, sticky="ew", pady=5)
        maps.columnconfigure(1, weight=1)
        self.custom_map_checkbox = ttk.Checkbutton(
            maps, text="詳細マッピングを使う（上のチェック欄より優先）",
            variable=self.var("custom_maps", False), command=self.toggle_custom_maps, state="disabled",
        )
        self.custom_map_checkbox.grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(maps, text="映像").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.video_map_var = tk.StringVar(self, value="なし")
        self.video_map_combo = ttk.Combobox(maps, textvariable=self.video_map_var, state="disabled")
        self.video_map_combo.grid(row=1, column=1, sticky="ew")
        ttk.Label(maps, text="音声（上から出力順）").grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=(6, 0))
        self.audio_map_frame = ttk.Frame(maps)
        self.audio_map_frame.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        self.add_audio_map_button = ttk.Button(maps, text="音声を追加", command=lambda: self.add_map_row("audio"), state="disabled")
        self.add_audio_map_button.grid(row=2, column=2, sticky="n", padx=(8, 0), pady=(6, 0))
        ttk.Label(maps, text="字幕（上から出力順）").grid(row=3, column=0, sticky="nw", padx=(0, 8), pady=(6, 0))
        self.subtitle_map_frame = ttk.Frame(maps)
        self.subtitle_map_frame.grid(row=3, column=1, sticky="ew", pady=(6, 0))
        self.add_subtitle_map_button = ttk.Button(maps, text="字幕を追加", command=lambda: self.add_map_row("subtitle"), state="disabled")
        self.add_subtitle_map_button.grid(row=3, column=2, sticky="n", padx=(8, 0), pady=(6, 0))
        row += 1

        extra = ttk.LabelFrame(root, text="外部音声（1行1ファイル、省略可）", padding=8)
        extra.grid(row=row, column=0, columnspan=3, sticky="ew", pady=5)
        extra.columnconfigure(0, weight=1)
        self.extra_audio = tk.Text(extra, height=2)
        self.extra_audio.grid(row=0, column=0, sticky="ew")
        ttk.Button(extra, text="追加…", command=self.add_audio).grid(row=0, column=1, padx=(8, 0), sticky="n")
        row += 1

        buttons = ttk.Frame(root)
        buttons.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        self.run_button = ttk.Button(buttons, text="変換開始", command=self.start)
        self.run_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="キャンセル", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="コマンド表示", command=self.show_command).pack(side="left")
        row += 1
        progress = ttk.Frame(root)
        progress.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(2, 8))
        progress.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(progress, text="待機中", width=72, anchor="e")
        self.progress_label.grid(row=1, column=0, sticky="e", pady=(3, 0))
        row += 1
        ttk.Label(root, text="ログ").grid(row=row, column=0, sticky="nw")
        self.log = tk.Text(root, height=12, state="disabled", wrap="none")
        self.log.grid(row=row, column=1, columnspan=2, sticky="nsew")
        root.rowconfigure(row, weight=1)

    def check(self, parent: ttk.Widget, name: str, text: str, row: int, column: int) -> None:
        ttk.Checkbutton(parent, text=text, variable=self.var(name, False)).grid(row=row, column=column, sticky="w")

    def selected_preset(self) -> dict | None:
        return next((preset for preset in self.presets if preset["name"] == self.preset_name.get()), None)

    def refresh_presets(self, selected_name: str = "") -> None:
        self.preset_combo.configure(values=[preset["name"] for preset in self.presets])
        self.preset_name.set(selected_name)

    def current_preset(self, name: str) -> dict:
        args = self.namespace()
        settings = {
            "deep_probe": self.vars["deep"].get(),
            "strip_metadata": self.vars["strip"].get(),
            "no_chapters": self.vars["chapters"].get(),
            "audio_count": self.vars["audio_count"].get().strip(),
            "start": self.vars["start"].get().strip(),
            "duration": self.vars["duration"].get().strip(),
        }
        streams = None
        if args.stream_maps is not None:
            ordinals = {}
            positions = {}
            for index, kind, _, _ in self.stream_controls:
                position = positions.get(kind, 0)
                ordinals[(kind, index)] = position
                positions[kind] = position + 1
            streams = [{"type": kind, "ordinal": ordinals[(kind, index)], "codec": codec}
                       for index, kind, codec in args.stream_maps]
        return {"name": name, "settings": settings, "streams": streams}

    def save_preset(self) -> None:
        name = simpledialog.askstring("プリセットを保存", "プリセット名", initialvalue=self.preset_name.get(), parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror("入力エラー", "プリセット名を入力してください。")
            return
        try:
            preset = self.current_preset(name)
        except (ValueError, KeyError) as exc:
            messagebox.showerror("入力エラー", str(exc))
            return
        existing = next((index for index, item in enumerate(self.presets) if item["name"] == name), None)
        if existing is not None and not messagebox.askyesno("上書き確認", f"プリセット「{name}」を上書きしますか？"):
            return
        updated = self.presets.copy()
        if existing is None:
            updated.append(preset)
        else:
            updated[existing] = preset
        try:
            save_presets(updated)
        except OSError as exc:
            messagebox.showerror("保存エラー", str(exc))
            return
        self.presets = updated
        self.refresh_presets(name)

    def delete_preset(self) -> None:
        preset = self.selected_preset()
        if preset is None:
            messagebox.showerror("入力エラー", "削除するプリセットを選択してください。")
            return
        name = preset["name"]
        if not messagebox.askyesno("削除確認", f"プリセット「{name}」を削除しますか？"):
            return
        updated = [item for item in self.presets if item["name"] != name]
        try:
            save_presets(updated)
        except OSError as exc:
            messagebox.showerror("保存エラー", str(exc))
            return
        self.presets = updated
        self.refresh_presets()
        if self.pending_preset is preset:
            self.pending_preset = None

    def apply_preset(self) -> None:
        preset = self.selected_preset()
        if preset is None:
            messagebox.showerror("入力エラー", "適用するプリセットを選択してください。")
            return
        input_path = Path(self.vars["input"].get()).expanduser().resolve()
        if self.probed_path == input_path:
            self.pending_preset = None
            try:
                self.apply_preset_streams(preset)
            except (ValueError, KeyError, TypeError) as exc:
                messagebox.showerror("プリセット適用エラー", str(exc))
                return
            self.apply_preset_settings(preset)
        else:
            self.pending_preset = preset
            self.probe_status.configure(text=f"「{preset['name']}」は入力解析後に適用します。")

    def apply_preset_settings(self, preset: dict) -> None:
        settings = preset.get("settings", {})
        for setting, variable in (("deep_probe", "deep"), ("strip_metadata", "strip"), ("no_chapters", "chapters")):
            self.vars[variable].set(bool(settings.get(setting, False)))
        for setting in ("audio_count", "start", "duration"):
            self.vars[setting].set(str(settings.get(setting, "")))

    def apply_preset_streams(self, preset: dict) -> None:
        requested = preset.get("streams")
        resolved = []
        if requested is not None:
            grouped = {}
            for index, kind, _, _ in self.stream_controls:
                grouped.setdefault(kind, []).append(index)
            for item in requested:
                kind, ordinal, codec = item["type"], item["ordinal"], item.get("codec", "copy")
                if not isinstance(ordinal, int) or ordinal < 0:
                    raise ValueError("プリセットのストリーム番号が正しくありません。")
                if kind not in grouped or ordinal >= len(grouped[kind]):
                    raise ValueError(f"「{preset['name']}」に必要な{kind}ストリーム {ordinal + 1} 本目が見つかりません。")
                if kind == "audio" and codec not in {"copy", "pcm_s24le"}:
                    raise ValueError(f"音声コーデック「{codec}」には対応していません。")
                resolved.append((grouped[kind][ordinal], kind, codec))

        self.vars["custom_maps"].set(False)
        self.toggle_custom_maps()
        for kind in ("audio", "subtitle"):
            for frame, _, _ in self.map_rows[kind]:
                frame.destroy()
            self.map_rows[kind].clear()
        self.video_map_var.set("なし")
        for index, kind, checked, codec_var in self.stream_controls:
            checked.set(requested is None and kind in {"video", "audio", "subtitle", "attachment"})
            if codec_var is not None:
                codec_var.set("copy")
        if requested is not None:
            controls = {index: (checked, codec_var) for index, _, checked, codec_var in self.stream_controls}
            labels = {kind: {index: label for label, index in options} for kind, options in self.map_options.items()}
            for index, kind, codec in resolved:
                checked, codec_var = controls[index]
                checked.set(True)
                if codec_var is not None:
                    codec_var.set(codec)
                if kind == "video":
                    self.video_map_var.set(labels[kind][index])
                elif kind in self.map_rows:
                    self.add_map_row(kind, labels[kind][index])
                    if kind == "audio":
                        self.map_rows[kind][-1][2].set(codec)
            self.vars["custom_maps"].set(True)
            self.toggle_custom_maps()
        self.probe_status.configure(text=f"プリセット「{preset['name']}」を適用しました。")

    def choose_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("TS/M2TS/MKV", "*.ts *.m2ts *.mts *.mkv"), ("All files", "*.*")])
        if path:
            self.set_input_file(Path(path))

    def enable_input_drop(self) -> None:
        if DND_FILES is None:
            self.drop_hint.configure(text="D&Dを使うには python -m pip install -e . を実行してください。")
            return
        for widget in (self.input_entry, self.drop_hint):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self.on_input_drop)

    def on_input_drop(self, event) -> None:
        try:
            path = file_from_drop(event.data, self.tk.splitlist)
        except ValueError as exc:
            messagebox.showerror("ドロップエラー", str(exc), parent=self)
            return
        self.set_input_file(path)

    def set_input_file(self, path: Path) -> None:
        previous = self.vars["input"].get().strip()
        output = self.vars["output"].get().strip()
        should_update_output = not output or (bool(previous) and Path(output) == default_output(Path(previous)))
        self.vars["input"].set(str(path))
        if should_update_output:
            self.vars["output"].set(str(default_output(path)))

    def open_folder_probe(self) -> None:
        FolderProbeWindow(self)

    def use_folder_file(self, path: Path) -> None:
        self.set_input_file(path)

    def schedule_probe(self, *_args) -> None:
        self.probe_generation += 1
        self.probed_path = None
        self.probe_data = None
        self.clear_streams()
        if self.probe_job:
            self.after_cancel(self.probe_job)
        self.probe_job = self.after(450, self.start_probe)

    def start_probe(self) -> None:
        if self.probe_job:
            self.after_cancel(self.probe_job)
        self.probe_job = None
        self.probe_generation += 1
        generation = self.probe_generation
        path = Path(self.vars["input"].get().strip()).expanduser()
        self.probed_path = None
        self.probe_data = None
        self.clear_streams()
        if not path.is_file():
            self.probe_status.configure(text="入力ファイルを選択してください。")
            return
        executable = find_tool("ffprobe")
        if not executable:
            self.probe_status.configure(text="ffprobeが見つかりません。全ストリームの自動コピーは使用できます。")
            self.pending_preset = None
            return
        path = path.resolve()
        self.probe_status.configure(text="解析中…")
        threading.Thread(target=self.probe_worker, args=(generation, path, executable), daemon=True).start()

    def probe_worker(self, generation: int, path: Path, executable: str) -> None:
        try:
            self.probe_results.put((generation, path, probe_media(path, executable)))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.probe_results.put((generation, path, exc))

    def poll_probe(self) -> None:
        try:
            while True:
                generation, path, result = self.probe_results.get_nowait()
                if generation != self.probe_generation:
                    continue
                if isinstance(result, Exception):
                    self.probe_status.configure(text=f"解析できませんでした: {result}")
                    if self.pending_preset is not None:
                        self.pending_preset = None
                        messagebox.showerror("プリセット適用エラー", "入力を解析できなかったため、プリセットを適用できませんでした。")
                    continue
                self.probed_path = path
                self.probe_data = result
                self.show_streams(result)
                if self.pending_preset is not None:
                    preset = self.pending_preset
                    self.pending_preset = None
                    try:
                        self.apply_preset_streams(preset)
                        self.apply_preset_settings(preset)
                    except (ValueError, KeyError, TypeError) as exc:
                        messagebox.showerror("プリセット適用エラー", str(exc))
        except queue.Empty:
            pass
        self.after(100, self.poll_probe)

    def clear_streams(self) -> None:
        for child in self.stream_frame.winfo_children():
            child.destroy()
        self.stream_controls.clear()
        self.map_options = {"video": [], "audio": [], "subtitle": []}
        self.vars["custom_maps"].set(False)
        self.custom_map_checkbox.configure(state="disabled")
        self.video_map_var.set("なし")
        self.video_map_combo.configure(values=("なし",), state="disabled")
        for kind in ("audio", "subtitle"):
            for frame, _, _ in self.map_rows[kind]:
                frame.destroy()
            self.map_rows[kind].clear()
        self.add_audio_map_button.configure(state="disabled")
        self.add_subtitle_map_button.configure(state="disabled")

    def show_streams(self, result: dict) -> None:
        streams = result["streams"]
        duration = format_duration(result)
        duration_text = f"・長さ {duration}" if duration != "—" else ""
        self.probe_status.configure(text=f"{len(streams)}ストリーム{duration_text}。チェックしたものを出力します。")
        for row, stream in enumerate(streams):
            kind = stream.get("codec_type", "unknown")
            index = stream.get("index")
            if not isinstance(index, int):
                continue
            details = describe_stream(stream)
            if kind in self.map_options:
                self.map_options[kind].append((details, index))
            selected = tk.BooleanVar(self, value=kind in {"video", "audio", "subtitle", "attachment"})
            ttk.Checkbutton(self.stream_frame, text=details, variable=selected).grid(row=row, column=0, sticky="w", pady=2)
            codec_var = None
            if kind == "audio":
                codec_var = tk.StringVar(self, value="copy")
                ttk.Label(self.stream_frame, text="出力音声").grid(row=row, column=1, padx=(12, 3))
                ttk.Combobox(self.stream_frame, textvariable=codec_var, values=("copy", "pcm_s24le"), width=12).grid(row=row, column=2)
            self.stream_controls.append((index, kind, selected, codec_var))
        self.custom_map_checkbox.configure(state="normal")
        self.video_map_combo.configure(values=("なし", *(label for label, _ in self.map_options["video"])))

    def toggle_custom_maps(self) -> None:
        enabled = self.vars["custom_maps"].get()
        self.video_map_combo.configure(state="readonly" if enabled else "disabled")
        self.add_audio_map_button.configure(state="normal" if enabled and self.map_options["audio"] else "disabled")
        self.add_subtitle_map_button.configure(state="normal" if enabled and self.map_options["subtitle"] else "disabled")
        if enabled and not any(self.map_rows.values()):
            selected = {index for index, _, checked, _ in self.stream_controls if checked.get()}
            audio_codecs = {index: codec.get() for index, kind, checked, codec in self.stream_controls
                            if kind == "audio" and checked.get() and codec is not None}
            videos = [(label, index) for label, index in self.map_options["video"] if index in selected]
            self.video_map_var.set(videos[0][0] if videos else "なし")
            for kind in ("audio", "subtitle"):
                for label, index in self.map_options[kind]:
                    if index in selected:
                        self.add_map_row(kind, label)
                        if kind == "audio":
                            self.map_rows[kind][-1][2].set(audio_codecs[index])
        for kind in ("audio", "subtitle"):
            for frame, _, _ in self.map_rows[kind]:
                for child in frame.winfo_children():
                    child.configure(state=("readonly" if isinstance(child, ttk.Combobox) else "normal") if enabled else "disabled")

    def add_map_row(self, kind: str, label: str | None = None) -> None:
        options = self.map_options[kind]
        used = {row_var.get() for _, row_var, _ in self.map_rows[kind]}
        if label is None:
            label = next((name for name, _ in options if name not in used), None)
        if label is None:
            return
        parent = self.audio_map_frame if kind == "audio" else self.subtitle_map_frame
        frame = ttk.Frame(parent)
        value = tk.StringVar(self, value=label)
        ttk.Combobox(frame, textvariable=value, values=[name for name, _ in options], state="readonly", width=48).pack(side="left", fill="x", expand=True)
        codec = None
        if kind == "audio":
            codec = tk.StringVar(self, value="copy")
            ttk.Combobox(frame, textvariable=codec, values=("copy", "pcm_s24le"), state="readonly", width=12).pack(side="left", padx=(5, 0))
        ttk.Button(frame, text="↑", width=3, command=lambda: self.move_map_row(kind, frame, -1)).pack(side="left", padx=(5, 0))
        ttk.Button(frame, text="↓", width=3, command=lambda: self.move_map_row(kind, frame, 1)).pack(side="left")
        ttk.Button(frame, text="削除", command=lambda: self.remove_map_row(kind, frame)).pack(side="left", padx=(5, 0))
        self.map_rows[kind].append((frame, value, codec))
        self.layout_map_rows(kind)

    def remove_map_row(self, kind: str, frame: ttk.Frame) -> None:
        self.map_rows[kind] = [row for row in self.map_rows[kind] if row[0] is not frame]
        frame.destroy()
        self.layout_map_rows(kind)

    def move_map_row(self, kind: str, frame: ttk.Frame, direction: int) -> None:
        rows = self.map_rows[kind]
        position = next(index for index, row in enumerate(rows) if row[0] is frame)
        destination = position + direction
        if 0 <= destination < len(rows):
            rows[position], rows[destination] = rows[destination], rows[position]
            self.layout_map_rows(kind)

    def layout_map_rows(self, kind: str) -> None:
        for frame, _, _ in self.map_rows[kind]:
            frame.pack_forget()
            frame.pack(fill="x", pady=2)

    def selected_custom_maps(self) -> list[tuple[int, str, str]]:
        selected = []
        video = self.video_map_var.get()
        if video != "なし":
            selected.append((dict(self.map_options["video"])[video], "video", "copy"))
        for kind in ("audio", "subtitle"):
            used = set()
            options = dict(self.map_options[kind])
            for _, value, codec in self.map_rows[kind]:
                label = value.get()
                if label in used:
                    raise ValueError(f"同じ{('音声' if kind == 'audio' else '字幕')}ストリームが複数回選択されています。")
                used.add(label)
                selected.append((options[label], kind, codec.get() if codec else "copy"))
        selected.extend((index, kind, "copy") for index, kind, checked, _ in self.stream_controls
                        if kind == "attachment" and checked.get())
        return selected

    def choose_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".mkv", filetypes=[("MKV", "*.mkv"), ("All files", "*.*")])
        if path:
            self.vars["output"].set(path)

    def add_audio(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("Audio", "*.mka *.m4a *.flac *.wav *.ac3 *.dts"), ("All files", "*.*")])
        if paths:
            self.extra_audio.insert("end", "\n".join(paths) + "\n")

    def namespace(self):
        from argparse import Namespace
        input_path = Path(self.vars["input"].get())
        output_text = self.vars["output"].get().strip()
        def number(name: str):
            value = self.vars[name].get().strip()
            return float(value) if value else None
        count = self.vars["audio_count"].get().strip()
        input_resolved = input_path.expanduser().resolve()
        use_probe = self.probed_path == input_resolved
        stream_maps = None
        if use_probe:
            if self.vars["custom_maps"].get():
                stream_maps = self.selected_custom_maps()
            else:
                stream_maps = [(index, kind, codec.get().strip() if codec else "copy")
                               for index, kind, selected, codec in self.stream_controls if selected.get()]
            if count:
                limit = int(count)
                audio_seen = 0
                filtered = []
                for stream in stream_maps:
                    if stream[1] == "audio":
                        audio_seen += 1
                        if audio_seen > limit:
                            continue
                    filtered.append(stream)
                stream_maps = filtered
            if not stream_maps and not self.extra_audio.get("1.0", "end").strip():
                raise ValueError("出力するストリームを1つ以上選択してください。")
        return Namespace(
            input=input_path, output=Path(output_text) if output_text else None, ffmpeg="ffmpeg",
            overwrite=self.vars["overwrite"].get(), dry_run=False, loglevel="info",
            deep_probe=self.vars["deep"].get(), strip_metadata=self.vars["strip"].get(),
            no_chapters=self.vars["chapters"].get(), start=number("start"), duration=number("duration"),
            video_map=None, audio_map=[], audio_codec=[], audio_count=int(count) if count else None,
            subtitle_map=[],
            extra_audio=[Path(line) for line in self.extra_audio.get("1.0", "end").splitlines() if line.strip()],
            stream_maps=stream_maps,
        )

    def command(self):
        if self.pending_preset is not None:
            raise ValueError("プリセットの適用には入力ファイルの解析完了が必要です。")
        args = self.namespace()
        output = (args.output or default_output(args.input)).expanduser().resolve()
        executable = find_tool(args.ffmpeg) or args.ffmpeg
        return make_command(executable, args, args.input.expanduser().resolve(), output)

    def show_command(self) -> None:
        try:
            messagebox.showinfo("生成コマンド", subprocess.list2cmdline(gui_command(self.command())))
        except (ValueError, TypeError) as exc:
            messagebox.showerror("入力エラー", str(exc))

    def start(self) -> None:
        try:
            command = self.command()
            args = self.namespace()
            output = (args.output or default_output(args.input)).expanduser().resolve()
            if not args.input.expanduser().is_file():
                raise ValueError("入力ファイルが見つかりません。")
            if args.audio_count is not None and args.audio_count < 0:
                raise ValueError("音声本数は0以上で指定してください。")
            if args.input.expanduser().resolve() == output:
                raise ValueError("入力と出力に同じファイルは指定できません。")
            if output.exists() and not args.overwrite:
                raise ValueError("出力ファイルが既に存在します。「既存ファイルを上書き」を有効にしてください。")
            if not find_tool(args.ffmpeg):
                raise ValueError("ffmpegが見つかりません。vendor/ffmpeg/binまたはPATHを確認してください。")
        except (ValueError, TypeError) as exc:
            messagebox.showerror("入力エラー", str(exc))
            return
        command = gui_command(command)
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("起動エラー", str(exc))
            return
        self.process = process
        self.process_output = queue.Queue()
        self.progress_total = output_duration(self.probe_data if self.probed_path == args.input.expanduser().resolve() else None, args)
        self.progress_time = 0.0
        self.progress_speed = ""
        self.cancel_requested = False
        self.progress_bar.stop()
        if self.progress_total is None:
            self.progress_bar.configure(mode="indeterminate")
            self.progress_bar.start(10)
        else:
            self.progress_bar.configure(mode="determinate", value=0)
        self.update_progress_label()
        self.write("実行: " + subprocess.list2cmdline(command) + "\n")
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        threading.Thread(target=self.read_process_output, args=(process, self.process_output), daemon=True).start()
        self.after(100, self.poll)

    @staticmethod
    def read_process_output(process: subprocess.Popen[str], output: queue.Queue[str | None]) -> None:
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    output.put(line)
        finally:
            process.wait()
            output.put(None)

    def poll(self) -> None:
        if not self.process:
            return
        finished = False
        for _ in range(500):
            try:
                line = self.process_output.get_nowait()
            except queue.Empty:
                break
            if line is None:
                finished = True
                break
            self.handle_process_line(line)
        if not finished:
            self.after(100, self.poll)
            return
        code = self.process.returncode
        self.process = None
        self.progress_bar.stop()
        if code == 0:
            self.progress_bar.configure(mode="determinate", value=100)
            self.progress_label.configure(text="完了 (100%)")
        elif self.cancel_requested:
            self.progress_label.configure(text="中止しました")
        else:
            self.progress_label.configure(text=f"失敗（終了コード: {code}）")
        self.run_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.write(f"\n終了コード: {code}\n")
        if code == 0:
            messagebox.showinfo("完了", "変換が完了しました。")

    def handle_process_line(self, line: str) -> None:
        key, separator, value = line.strip().partition("=")
        if separator and key == "out_time":
            seconds = parse_out_time(value)
            if seconds is not None:
                self.progress_time = seconds
                if self.progress_total is not None:
                    self.progress_bar.configure(value=min(100, seconds / self.progress_total * 100))
                self.update_progress_label()
        elif separator and key == "speed":
            self.progress_speed = value.strip()
            self.update_progress_label()
        elif separator and (key.startswith("stream_") or key in {"frame", "fps", "bitrate", "total_size",
                                                              "out_time_us", "out_time_ms", "dup_frames",
                                                              "drop_frames", "progress"}):
            return
        else:
            self.write(line)

    def update_progress_label(self) -> None:
        current = format_time(self.progress_time)
        speed = f" ・ {self.progress_speed}" if self.progress_speed and self.progress_speed != "N/A" else ""
        remaining = estimate_remaining(self.progress_total, self.progress_time, self.progress_speed)
        eta = f" ・ ETA {format_time(math.ceil(remaining)) if remaining is not None else '—'}"
        if self.progress_total is None:
            self.progress_label.configure(text=f"処理済み {current}{speed}{eta}")
        else:
            percent = min(100, self.progress_time / self.progress_total * 100)
            self.progress_label.configure(text=f"{percent:.1f}% ・ {current} / {format_time(self.progress_total)}{speed}{eta}")

    def cancel(self) -> None:
        if self.process and messagebox.askyesno("確認", "変換を中止しますか？"):
            self.cancel_requested = True
            self.cancel_button.configure(state="disabled")
            self.progress_label.configure(text="中止中…")
            try:
                self.process.terminate()
            except OSError:
                pass

    def write(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")


class FolderProbeWindow(tk.Toplevel):
    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title("ffmpeg-bdrip - フォルダ内のメディア一覧")
        self.minsize(860, 500)
        self.folder = tk.StringVar(self)
        self.recursive = tk.BooleanVar(self, value=False)
        self.results: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()
        self.paths: dict[str, Path] = {}
        self.scan_folder: Path | None = None
        self.running = False
        self.total = 0
        self.completed = 0
        self._build()
        current_input = Path(app.vars["input"].get().strip()).expanduser()
        if current_input.is_file():
            self.folder.set(str(current_input.parent))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll_results)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        self.folder_entry = ttk.Entry(root, textvariable=self.folder)
        self.folder_entry.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(root, text="フォルダを選択…", command=self.choose_folder)
        self.browse_button.grid(row=0, column=1, padx=(6, 0))

        controls = ttk.Frame(root)
        controls.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        ttk.Checkbutton(controls, text="サブフォルダも含める", variable=self.recursive).pack(side="left")
        self.scan_button = ttk.Button(controls, text="解析開始", command=self.start_scan)
        self.scan_button.pack(side="left", padx=(12, 0))
        self.cancel_button = ttk.Button(controls, text="停止", command=self.cancel_scan, state="disabled")
        self.cancel_button.pack(side="left", padx=(6, 0))
        self.status = ttk.Label(controls, text="フォルダを選択して解析を開始してください。")
        self.status.pack(side="left", padx=(12, 0))

        table = ttk.Frame(root)
        table.grid(row=2, column=0, columnspan=2, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("duration", "video", "audio", "subtitle", "status"))
        self.tree.heading("#0", text="ファイル / ストリーム")
        self.tree.column("#0", width=430, minwidth=250)
        for name, title, width in (("duration", "長さ", 90), ("video", "映像", 55),
                                   ("audio", "音声", 55), ("subtitle", "字幕", 55), ("status", "状態", 170)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=width, stretch=name == "status")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", self.open_selected)
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vertical.set)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)

        bottom = ttk.Frame(root)
        bottom.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(bottom, text="選択したファイルを入力に設定", command=self.open_selected).pack(side="left")
        ttk.Button(bottom, text="閉じる", command=self.close).pack(side="right")

    def choose_folder(self) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=self.folder.get() or None)
        if path:
            self.folder.set(path)
            self.start_scan()

    def start_scan(self) -> None:
        if self.running:
            return
        folder = Path(self.folder.get().strip()).expanduser()
        if not folder.is_dir():
            messagebox.showerror("入力エラー", "存在するフォルダを選択してください。", parent=self)
            return
        executable = find_tool("ffprobe")
        if not executable:
            messagebox.showerror("解析エラー", "ffprobeが見つかりません。", parent=self)
            return
        folder = folder.resolve()
        self.folder.set(str(folder))
        self.scan_folder = folder
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.paths.clear()
        self.total = 0
        self.completed = 0
        self.stop_event = threading.Event()
        self.running = True
        self.folder_entry.configure(state="disabled")
        self.browse_button.configure(state="disabled")
        self.scan_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.status.configure(text="ファイルを検索中…")
        threading.Thread(target=self.scan_worker, args=(folder, self.recursive.get(), executable, self.stop_event), daemon=True).start()

    def scan_worker(self, folder: Path, recursive: bool, executable: str, stop_event: threading.Event) -> None:
        try:
            files = list(iter_media_files(folder, recursive))
            self.results.put(("total", len(files)))
            for path in files:
                if stop_event.is_set():
                    break
                try:
                    result = probe_media(path, executable)
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    result = exc
                self.results.put(("file", path, result))
            self.results.put(("done", stop_event.is_set()))
        except OSError as exc:
            self.results.put(("error", exc))

    def poll_results(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                message = self.results.get_nowait()
                if message[0] == "total":
                    self.total = message[1]
                    self.status.configure(text=f"対象 {self.total} 件。解析中…" if self.total else "対象ファイルがありません。")
                elif message[0] == "file":
                    self.add_result(message[1], message[2])
                    self.completed += 1
                    self.status.configure(text=f"{self.completed}/{self.total} 件を解析")
                elif message[0] == "done":
                    self.running = False
                    self.folder_entry.configure(state="normal")
                    self.browse_button.configure(state="normal")
                    self.scan_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status.configure(text=f"{self.completed}/{self.total} 件で停止" if message[1]
                                          else f"解析完了: {self.completed} 件")
                elif message[0] == "error":
                    self.running = False
                    self.folder_entry.configure(state="normal")
                    self.browse_button.configure(state="normal")
                    self.scan_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    self.status.configure(text=f"検索エラー: {message[1]}")
        except queue.Empty:
            pass
        self.after(100, self.poll_results)

    def add_result(self, path: Path, result: dict | Exception) -> None:
        folder = self.scan_folder
        name = str(path.relative_to(folder))
        if isinstance(result, Exception):
            item = self.tree.insert("", "end", text=name, values=("—", "—", "—", "—", "解析失敗"))
            self.tree.insert(item, "end", text=str(result))
        else:
            counts = stream_counts(result)
            item = self.tree.insert("", "end", text=name, values=(format_duration(result), *counts, "完了"))
            for stream in result["streams"]:
                self.tree.insert(item, "end", text=describe_stream(stream))
        self.paths[item] = path

    def open_selected(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        parent = self.tree.parent(item)
        if parent:
            item = parent
        path = self.paths.get(item)
        if path is not None:
            self.app.use_folder_file(path)
            self.close()

    def cancel_scan(self) -> None:
        self.stop_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.configure(text="停止中…")

    def close(self) -> None:
        self.stop_event.set()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
