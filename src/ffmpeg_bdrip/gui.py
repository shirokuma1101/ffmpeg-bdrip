"""Tkinter GUI for ffmpeg-bdrip."""

from __future__ import annotations

import math
import copy
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .bdmv_analysis import chapter_positions, collect_text_candidates, duration_text, episode_timeline, quick_scan_bdmv, scan_bdmv, title_playlist_links
from .cli import default_output, find_tool, make_command
from .batch import batch_targets, resolve_streams, stream_ordinals
from .drop import files_from_drop
from .iso_mount import mount_iso, unmount_iso
from .presets import load_presets, save_presets
from .probe import describe_stream, format_duration, iter_media_files, probe_media, stream_counts
from .progress import batch_percent, estimate_remaining, format_time, gui_command, output_duration, parse_out_time

bundled_dnd = Path(__file__).resolve().parents[2] / "vendor" / "python"
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
        self.minsize(1180, 650)
        self.process: subprocess.Popen[str] | None = None
        self.process_output: queue.Queue[str | None] = queue.Queue()
        self.progress_total: float | None = None
        self.progress_time = 0.0
        self.progress_speed = ""
        self.cancel_requested = False
        self.batch_paths: list[Path] = []
        self.batch_active = False
        self.batch_position = ""
        self.batch_durations: list[float | None] = []
        self.batch_finished = 0
        self.batch_cancel = threading.Event()
        self.batch_events: queue.Queue[tuple] = queue.Queue()
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
        menu_bar = tk.Menu(self)
        tools_menu = tk.Menu(menu_bar, tearoff=False)
        tools_menu.add_command(label="フォルダ内のファイル一覧・解析結果…", command=self.open_folder_probe)
        tools_menu.add_command(label="BDMVの構造を解析・表示…", command=self.open_bdmv_analysis)
        menu_bar.add_cascade(label="ツール", menu=tools_menu)
        self.configure(menu=menu_bar)

        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1, uniform="panes")
        root.columnconfigure(1, weight=1, uniform="panes")
        root.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(root, text="入力・ファイル", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(1, weight=1)
        right = ttk.LabelFrame(root, text="変換設定", padding=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        right.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(left, text="入力ファイル").grid(row=row, column=0, sticky="w", pady=4)
        self.input_entry = ttk.Entry(left, textvariable=self.var("input"))
        self.input_entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(left, text="選択…", command=self.choose_input).grid(row=row, column=2, padx=(6, 0))
        row += 1
        self.drop_hint = ttk.Label(left, text="入力ファイルをここへドラッグ＆ドロップできます。")
        self.drop_hint.grid(row=row, column=1, columnspan=2, sticky="w")
        self.enable_input_drop()
        row += 1
        batch = ttk.LabelFrame(left, text="バッチ処理（順番に変換）", padding=6)
        batch.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(3, 4))
        batch.columnconfigure(0, weight=1)
        self.batch_list = tk.Listbox(batch, height=3, selectmode="extended")
        self.batch_list.grid(row=0, column=0, sticky="ew")
        batch_buttons = ttk.Frame(batch)
        batch_buttons.grid(row=0, column=1, padx=(6, 0), sticky="n")
        ttk.Button(batch_buttons, text="複数追加…", command=self.add_batch_files).pack(fill="x")
        ttk.Button(batch_buttons, text="現在の入力を追加", command=self.add_current_to_batch).pack(fill="x", pady=3)
        ttk.Button(batch_buttons, text="選択を削除", command=self.remove_batch_files).pack(fill="x")
        ttk.Button(batch_buttons, text="全件削除", command=self.clear_batch_files).pack(fill="x", pady=(3, 0))
        ttk.Label(batch, text="出力フォルダ（空欄なら入力と同じ場所）").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(batch, textvariable=self.var("batch_output_folder")).grid(row=2, column=0, sticky="ew")
        ttk.Button(batch, text="フォルダ…", command=self.choose_batch_output_folder).grid(row=2, column=1, padx=(6, 0), sticky="ew")
        row += 1
        ttk.Label(left, text="出力MKV").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(left, textvariable=self.var("output")).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(left, text="保存先…", command=self.choose_output).grid(row=row, column=2, padx=(6, 0))
        row += 1

        streams = ttk.LabelFrame(left, text="入力ファイルの解析・ストリーム選択", padding=8)
        streams.grid(row=row, column=0, columnspan=3, sticky="nsew", pady=(8, 4))
        left.rowconfigure(row, weight=1)
        streams.columnconfigure(0, weight=1)
        streams.rowconfigure(1, weight=1)
        self.probe_status = ttk.Label(streams, text="入力ファイルを選ぶとffprobeで解析します。")
        self.probe_status.grid(row=0, column=0, sticky="w")
        ttk.Button(streams, text="再解析", command=self.start_probe).grid(row=0, column=1, padx=(8, 0))
        self.stream_canvas = tk.Canvas(streams, height=170, highlightthickness=0)
        self.stream_canvas.grid(row=1, column=0, sticky="nsew", pady=(5, 0))
        scrollbar = ttk.Scrollbar(streams, orient="vertical", command=self.stream_canvas.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.stream_canvas.configure(yscrollcommand=scrollbar.set)
        self.stream_frame = ttk.Frame(self.stream_canvas)
        self.stream_window = self.stream_canvas.create_window((0, 0), window=self.stream_frame, anchor="nw")
        self.stream_frame.bind("<Configure>", lambda _event: self.stream_canvas.configure(scrollregion=self.stream_canvas.bbox("all")))
        self.stream_canvas.bind("<Configure>", lambda event: self.stream_canvas.itemconfigure(self.stream_window, width=event.width))

        row = 0
        presets = ttk.LabelFrame(right, text="プリセット", padding=8)
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

        options = ttk.LabelFrame(right, text="基本設定", padding=8)
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

        maps = ttk.LabelFrame(right, text="詳細設定：出力順とmapを指定", padding=8)
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

        extra = ttk.LabelFrame(right, text="外部音声（1行1ファイル、省略可）", padding=8)
        extra.grid(row=row, column=0, columnspan=3, sticky="ew", pady=5)
        extra.columnconfigure(0, weight=1)
        self.extra_audio = tk.Text(extra, height=2)
        self.extra_audio.grid(row=0, column=0, sticky="ew")
        ttk.Button(extra, text="追加…", command=self.add_audio).grid(row=0, column=1, padx=(8, 0), sticky="n")
        row += 1

        row = 1
        buttons = ttk.Frame(root)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        self.run_button = ttk.Button(buttons, text="変換開始", command=self.start)
        self.run_button.pack(side="left")
        self.cancel_button = ttk.Button(buttons, text="キャンセル", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="コマンド表示", command=self.show_command).pack(side="left")
        row += 1
        progress = ttk.Frame(root)
        progress.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        progress.columnconfigure(0, weight=1)
        self.progress_bar = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_label = ttk.Label(progress, text="待機中", width=72, anchor="e")
        self.progress_label.grid(row=1, column=0, sticky="e", pady=(3, 0))
        row += 1
        log_frame = ttk.LabelFrame(root, text="ログ", padding=4)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=8, state="disabled", wrap="none")
        self.log.grid(row=0, column=0, sticky="nsew")
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
            paths = files_from_drop(event.data, self.tk.splitlist)
        except ValueError as exc:
            messagebox.showerror("ドロップエラー", str(exc), parent=self)
            return
        if len(paths) == 1:
            self.set_input_file(paths[0])
        else:
            self.queue_batch_files(paths)

    def add_batch_files(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("TS/M2TS/MKV", "*.ts *.m2ts *.mts *.mkv"), ("All files", "*.*")])
        self.queue_batch_files([Path(path) for path in paths])

    def add_current_to_batch(self) -> None:
        self.queue_batch_files([Path(self.vars["input"].get().strip()).expanduser()])

    def queue_batch_files(self, paths: list[Path]) -> None:
        if self.process or self.batch_active:
            messagebox.showerror("処理中", "処理中はバッチの入力を変更できません。")
            return
        if any(not path.is_file() for path in paths):
            messagebox.showerror("入力エラー", "存在するファイルを選択してください。")
            return
        for path in paths:
            path = path.resolve()
            if path not in self.batch_paths:
                self.batch_paths.append(path)
                self.batch_list.insert("end", str(path))
        if self.batch_paths and not self.vars["input"].get().strip():
            self.set_input_file(self.batch_paths[0])

    def remove_batch_files(self) -> None:
        if self.batch_active:
            return
        for index in reversed(self.batch_list.curselection()):
            del self.batch_paths[index]
            self.batch_list.delete(index)

    def clear_batch_files(self) -> None:
        if self.batch_active:
            return
        self.batch_paths.clear()
        self.batch_list.delete(0, "end")

    def choose_batch_output_folder(self) -> None:
        path = filedialog.askdirectory(parent=self)
        if path:
            self.vars["batch_output_folder"].set(path)

    def set_input_file(self, path: Path) -> None:
        previous = self.vars["input"].get().strip()
        output = self.vars["output"].get().strip()
        should_update_output = not output or (bool(previous) and Path(output) == default_output(Path(previous)))
        self.vars["input"].set(str(path))
        if should_update_output:
            self.vars["output"].set(str(default_output(path)))

    def open_folder_probe(self) -> None:
        FolderProbeWindow(self)

    def open_bdmv_analysis(self) -> None:
        BdmvAnalysisWindow(self)

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
        if self.batch_paths:
            self.start_batch()
            return
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

    def start_batch(self) -> None:
        try:
            args = self.namespace()
            folder_text = self.vars["batch_output_folder"].get().strip()
            targets = batch_targets(self.batch_paths, Path(folder_text) if folder_text else None)
            ffmpeg = find_tool(args.ffmpeg)
            if not ffmpeg:
                raise ValueError("ffmpegが見つかりません。")
            if args.audio_count is not None and args.audio_count < 0:
                raise ValueError("音声本数は0以上で指定してください。")
            if self.pending_preset is not None:
                raise ValueError("プリセットの解析・適用が完了するまでお待ちください。")
            selection = None
            if args.stream_maps is not None and self.probe_data is not None:
                selection = stream_ordinals(self.probe_data["streams"], args.stream_maps)
            for source, output in targets:
                if not source.is_file():
                    raise ValueError(f"入力ファイルが見つかりません: {source}")
                if output.exists() and not args.overwrite:
                    raise ValueError(f"出力ファイルが既に存在します: {output}")
        except (ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("バッチ入力エラー", str(exc))
            return
        self.batch_cancel.clear()
        self.batch_events = queue.Queue()
        self.batch_active = True
        self.batch_durations = []
        self.batch_finished = 0
        self.cancel_requested = False
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress_bar.configure(mode="determinate", value=0)
        self.progress_label.configure(text=f"バッチ: 全{len(targets)}件の長さを解析中…")
        self.write(f"\nバッチ開始: {len(targets)} 件（出力先は各入力の自動生成名）\n")
        threading.Thread(target=self.batch_worker, args=(targets, args, ffmpeg, selection), daemon=True).start()
        self.after(100, self.poll_batch)

    def batch_worker(self, targets, args, ffmpeg: str, selection) -> None:
        probe_executable = find_tool("ffprobe")
        completed = failed = 0
        prepared = []
        for source, output in targets:
            if self.batch_cancel.is_set():
                break
            try:
                data = probe_media(source, probe_executable) if probe_executable else None
                prepared.append((source, output, data, output_duration(data, args), None))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                prepared.append((source, output, None, None, str(exc)))
        if not self.batch_cancel.is_set():
            self.batch_events.put(("plan", [item[3] for item in prepared]))
        for number, (source, output, data, duration, probe_error) in enumerate(prepared, 1):
            if self.batch_cancel.is_set():
                break
            try:
                job_args = copy.copy(args)
                job_args.input, job_args.output = source, output
                if selection is not None:
                    if data is None:
                        raise ValueError(probe_error or "ストリーム選択の適用にはffprobeが必要です。")
                    job_args.stream_maps = resolve_streams(data["streams"], selection)
                else:
                    job_args.stream_maps = None
                if self.batch_cancel.is_set():
                    break
                command = gui_command(make_command(ffmpeg, job_args, source, output))
                self.batch_events.put(("start", number, len(targets), source, command, duration))
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, encoding="utf-8", errors="replace")
                self.process = process
                if process.stdout is not None:
                    for line in process.stdout:
                        self.batch_events.put(("line", line))
                code = process.wait()
                self.process = None
                if self.batch_cancel.is_set():
                    break
                if code == 0:
                    completed += 1
                else:
                    failed += 1
                self.batch_events.put(("result", number, source, code))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                self.process = None
                failed += 1
                self.batch_events.put(("error", number, source, str(exc)))
        self.batch_events.put(("done", completed, failed, self.batch_cancel.is_set()))

    def poll_batch(self) -> None:
        for _ in range(500):
            try:
                event = self.batch_events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "plan":
                self.batch_durations = event[1]
                self.progress_bar.configure(mode="determinate", value=0)
                self.progress_label.configure(text=f"バッチ: 0/{len(self.batch_durations)} 件 ・ 全体 0.0%")
            elif kind == "start":
                _, number, total, source, command, duration = event
                self.progress_total = duration
                self.progress_time = 0.0
                self.progress_speed = ""
                self.progress_bar.configure(mode="determinate", value=batch_percent(self.batch_durations, self.batch_finished))
                self.write(f"\n[{number}/{total}] {source}\n実行: {subprocess.list2cmdline(command)}\n")
                self.batch_position = f"[{number}/{total}] "
                self.update_progress_label()
            elif kind == "line":
                self.handle_process_line(event[1])
            elif kind == "result":
                _, number, source, code = event
                self.write(f"[{number}] {source.name}: 終了コード {code}\n")
                self.batch_finished += 1
                self.progress_bar.configure(value=batch_percent(self.batch_durations, self.batch_finished))
            elif kind == "error":
                _, number, source, error = event
                self.write(f"[{number}] {source.name}: エラー: {error}\n")
                self.batch_finished += 1
                self.progress_bar.configure(value=batch_percent(self.batch_durations, self.batch_finished))
            elif kind == "done":
                _, completed, failed, cancelled = event
                self.batch_active = False
                self.progress_bar.stop()
                if not cancelled:
                    self.progress_bar.configure(value=100)
                self.run_button.configure(state="normal")
                self.cancel_button.configure(state="disabled")
                status = "中止" if cancelled else "完了"
                self.progress_label.configure(text=f"バッチ{status}: 成功 {completed} 件 / 失敗 {failed} 件")
                self.write(f"バッチ{status}: 成功 {completed} 件 / 失敗 {failed} 件\n")
                messagebox.showinfo("バッチ処理", f"{status}: 成功 {completed} 件 / 失敗 {failed} 件")
                return
        self.after(100, self.poll_batch)

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
                if self.batch_active:
                    self.progress_bar.configure(value=batch_percent(self.batch_durations, self.batch_finished, seconds))
                elif self.progress_total is not None:
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
        prefix = self.batch_position if self.batch_active else ""
        current = format_time(self.progress_time)
        speed = f" ・ {self.progress_speed}" if self.progress_speed and self.progress_speed != "N/A" else ""
        remaining = estimate_remaining(self.progress_total, self.progress_time, self.progress_speed)
        eta = f" ・ ETA {format_time(math.ceil(remaining)) if remaining is not None else '—'}"
        if self.progress_total is None:
            detail = f"処理済み {current}"
        else:
            percent = min(100, self.progress_time / self.progress_total * 100)
            detail = f"{percent:.1f}% ・ {current} / {format_time(self.progress_total)}"
        if self.batch_active and self.batch_durations:
            overall = batch_percent(self.batch_durations, self.batch_finished, self.progress_time)
            detail = f"全体 {overall:.1f}% ・ 現在 {detail}"
        self.progress_label.configure(text=f"{prefix}{detail}{speed}{eta}")

    def cancel(self) -> None:
        if self.batch_active:
            if messagebox.askyesno("確認", "バッチ処理を中止しますか？"):
                self.batch_cancel.set()
                self.cancel_button.configure(state="disabled")
                self.progress_label.configure(text="中止中…")
                if self.process:
                    try:
                        self.process.terminate()
                    except OSError:
                        pass
            return
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


class BdmvAnalysisWindow(tk.Toplevel):
    """Playlist inventory and a proportional clip timeline from bdpl's JSON."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.title("BDMV 構造解析 (bdpl)")
        self.geometry("1050x650")
        self.minsize(800, 450)
        self.folder = tk.StringVar(self)
        current = Path(app.vars["input"].get().strip()).expanduser()
        if current.is_file():
            self.folder.set(str(current.parent))
        self.results: queue.Queue[tuple[dict | Exception, Path | None, bool]] = queue.Queue()
        self.data: dict | None = None
        self.mounted_iso: tuple[Path, bool] | None = None
        self.running = False
        self.close_requested = False
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        ttk.Entry(root, textvariable=self.folder).grid(row=0, column=0, sticky="ew")
        ttk.Button(root, text="フォルダ…", command=self.choose_folder).grid(row=0, column=1, padx=(6, 0))
        ttk.Button(root, text="ISO…", command=self.choose_iso).grid(row=0, column=2, padx=(6, 0))
        self.scan_button = ttk.Button(root, text="簡易解析", command=lambda: self.start_scan(False))
        self.scan_button.grid(row=0, column=3, padx=(6, 0))
        self.deep_button = ttk.Button(root, text="詳細解析 (bdpl)", command=lambda: self.start_scan(True))
        self.deep_button.grid(row=0, column=4, padx=(6, 0))
        self.status = ttk.Label(root, text="BDMVフォルダ、その親、またはISOを指定してください。")
        self.status.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 8))
        self.tree = ttk.Treeview(root, columns=("duration", "kind", "details"), show="tree headings")
        self.tree.heading("#0", text="タイトル / プレイリスト / クリップ")
        self.tree.heading("duration", text="長さ")
        self.tree.heading("kind", text="種類")
        self.tree.heading("details", text="詳細")
        self.tree.column("#0", width=310)
        self.tree.column("duration", width=90, stretch=False)
        self.tree.column("kind", width=120, stretch=False)
        self.tree.column("details", width=420)
        self.tree.grid(row=2, column=0, columnspan=4, sticky="nsew")
        scroll = ttk.Scrollbar(root, orient="vertical", command=self.tree.yview)
        scroll.grid(row=2, column=4, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.draw_selected)
        ttk.Label(root, text="選択したプレイリストのクリップ構成（時間比）").grid(row=3, column=0, sticky="w", pady=(10, 3))
        self.timeline = tk.Canvas(root, height=85, background="white", highlightthickness=1)
        self.timeline.grid(row=4, column=0, columnspan=5, sticky="ew")
        self.timeline.bind("<Configure>", self.draw_selected)
        self.item_playlists: dict[str, dict] = {}
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll_results)

    def choose_folder(self) -> None:
        path = filedialog.askdirectory(parent=self)
        if path:
            self.folder.set(path)

    def choose_iso(self) -> None:
        path = filedialog.askopenfilename(parent=self, filetypes=[("Blu-ray ISO", "*.iso"), ("All files", "*.*")])
        if path:
            self.folder.set(path)

    def start_scan(self, detailed: bool = False) -> None:
        if self.running:
            return
        path = Path(self.folder.get().strip()).expanduser()
        if self.mounted_iso and path.resolve() != self.mounted_iso[0]:
            self.release_iso()
        self.running = True
        self.scan_button.configure(state="disabled")
        self.deep_button.configure(state="disabled")
        self.status.configure(text="詳細解析中…" if detailed else "簡易解析中…")
        threading.Thread(target=self.scan_worker, args=(path, detailed), daemon=True).start()

    def scan_worker(self, path: Path, detailed: bool) -> None:
        iso = None
        created = False
        try:
            if path.suffix.lower() == ".iso":
                iso = path.resolve()
                if self.mounted_iso and self.mounted_iso[0] == iso:
                    drive, _ = mount_iso(iso)
                else:
                    drive, created = mount_iso(iso)
                path = drive
            if detailed:
                try:
                    result = scan_bdmv(path)
                    result["text_candidates"] = collect_text_candidates(path)
                except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                    result = quick_scan_bdmv(path)
                    result["scan_error"] = str(exc)
            else:
                result = quick_scan_bdmv(path)
            self.results.put((result, iso, created))
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            self.results.put((exc, iso, created))

    def poll_results(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                result, iso, created = self.results.get_nowait()
                self.running = False
                if iso:
                    owned = created or (self.mounted_iso is not None and self.mounted_iso[0] == iso and self.mounted_iso[1])
                    self.mounted_iso = (iso, owned)
                self.scan_button.configure(state="normal")
                self.deep_button.configure(state="normal")
                if self.close_requested:
                    self.release_iso()
                    self.destroy()
                    return
                if isinstance(result, Exception):
                    self.status.configure(text=str(result))
                else:
                    self.show_analysis(result)
        except queue.Empty:
            pass
        self.after(100, self.poll_results)

    def release_iso(self) -> None:
        if self.mounted_iso and self.mounted_iso[1]:
            try:
                unmount_iso(self.mounted_iso[0])
            except OSError as exc:
                messagebox.showwarning("ISOのマウント解除", str(exc), parent=self)
        self.mounted_iso = None

    def close(self) -> None:
        if self.running:
            self.close_requested = True
            self.status.configure(text="解析の終了を待ってISOを解除しています…")
            return
        self.release_iso()
        self.destroy()

    def show_analysis(self, data: dict) -> None:
        self.data = data
        existing = self.tree.get_children()
        if existing:
            self.tree.delete(*existing)
        self.item_playlists.clear()
        self.timeline.delete("all")
        playlists = {item.get("mpls"): item for item in data["playlists"]}
        links = title_playlist_links(data)
        if links:
            titles = self.tree.insert("", "end", text=f"ディスクのタイトル ({len(set(title for title, _ in links))})", open=True)
            title_nodes = {}
            for title, name in links:
                if title not in title_nodes:
                    title_nodes[title] = self.tree.insert(titles, "end", text=f"タイトル {title}", open=True,
                                                          values=("", "ナビゲーション", "index.bdmv → MovieObject.bdmv"))
                playlist = playlists.get(name)
                item = self.tree.insert(title_nodes[title], "end", text=name,
                                        values=(duration_text(playlist.get("duration_ms")) if playlist else "—",
                                                "プレイリスト", "参照先" if playlist else "解析結果にない参照先"))
                if playlist:
                    self.item_playlists[item] = playlist
                    for clip in playlist.get("play_items", []):
                        self.tree.insert(item, "end", text=clip.get("m2ts") or clip.get("clip_id", "—"),
                                         values=(duration_text(clip.get("duration_ms")), "クリップ", clip.get("label") or ""))
        episodes = self.tree.insert("", "end", text=f"エピソード ({len(data.get('episodes', []))})", open=True)
        for episode in data.get("episodes", []):
            name = episode.get("playlist", "—")
            item = self.tree.insert(episodes, "end", text=f"第{episode.get('episode', '?')}話  {name}",
                                    values=(duration_text(episode.get("duration_ms")), "エピソード",
                                            f"確信度 {episode.get('confidence', '—')}"))
            view = episode_timeline(episode)
            if view["play_items"]:
                self.item_playlists[item] = view
                for segment in view["play_items"]:
                    self.tree.insert(item, "end", text=segment["m2ts"],
                                     values=(duration_text(segment.get("duration_ms")),
                                             segment.get("label") or "区間", "推定エピソード内の区間"))
        inventory = self.tree.insert("", "end", text=f"プレイリスト ({len(playlists)})", open=True)
        for playlist in data["playlists"]:
            name = playlist.get("mpls", "—")
            item = self.tree.insert(inventory, "end", text=name,
                                    values=(duration_text(playlist.get("duration_ms")), "プレイリスト",
                                            f"{len(playlist.get('play_items', []))}クリップ / {len(playlist.get('chapters', []))}チャプター"))
            self.item_playlists[item] = playlist
            for clip in playlist.get("play_items", []):
                streams = ", ".join(f"{s.get('codec', '?')} {s.get('lang', '')}".strip()
                                     for s in clip.get("streams", []))
                self.tree.insert(item, "end", text=clip.get("m2ts") or clip.get("clip_id", "—"),
                                 values=(duration_text(clip.get("duration_ms")), clip.get("label") or "クリップ", streams))
            for chapter_number, position in chapter_positions(playlist):
                self.tree.insert(item, "end", text=f"チャプター {chapter_number}",
                                 values=(duration_text(position), "チャプター", "プレイリスト先頭からの位置"))
        warnings = data.get("warnings", [])
        if warnings:
            parent = self.tree.insert("", "end", text=f"警告 ({len(warnings)})", open=True)
            for warning in warnings:
                self.tree.insert(parent, "end", text=warning.get("code", "警告"),
                                 values=("", "警告", warning.get("message", "")))
        texts = data.get("text_candidates", [])
        if texts:
            parent = self.tree.insert("", "end", text=f"文字候補 ({len(texts)})", open=True,
                                      values=("", "静的抽出", "話数・ボタンとの対応は未確定"))
            for entry in texts:
                self.tree.insert(parent, "end", text=entry["text"],
                                 values=("", "文字候補", entry["source"]))
        title = (data.get("disc") or {}).get("title") or "タイトル不明"
        summary = f"{title} ・ {len(playlists)}プレイリスト ・ {len(data.get('episodes', []))}エピソード ・ 文字候補 {len(texts)}件"
        if data.get("scan_mode") == "quick":
            summary = f"簡易解析 ・ {title} ・ {len(playlists)}プレイリスト ・ 文字候補 {len(texts)}件（話数・長さは詳細解析で表示）"
        if data.get("scan_error"):
            summary += f"（bdpl解析不可: {data['scan_error']}）"
        self.status.configure(text=summary)

    def draw_selected(self, _event=None) -> None:
        self.timeline.delete("all")
        selection = self.tree.selection()
        playlist = self.item_playlists.get(selection[0]) if selection else None
        if playlist is None and selection:
            playlist = self.item_playlists.get(self.tree.parent(selection[0]))
        if not playlist:
            return
        clips = playlist.get("play_items", [])
        total = sum(max(0, clip.get("duration_ms") or 0) for clip in clips)
        if total <= 0:
            return
        width = max(1, self.timeline.winfo_width() - 20)
        offset = 10.0
        colors = {"OP": "#72b7b2", "BODY": "#4c78a8", "ED": "#f2cf5b", "PREVIEW": "#f28e2b"}
        for clip in clips:
            span = width * max(0, clip.get("duration_ms") or 0) / total
            label = str(clip.get("label") or "クリップ").upper()
            color = colors.get(label, "#9d9da3")
            self.timeline.create_rectangle(offset, 8, offset + span, 46, fill=color, outline="white")
            if span > 65:
                self.timeline.create_text(offset + span / 2, 27, text=label, fill="white")
            offset += span
        for chapter_number, position in chapter_positions(playlist):
            x = 10 + width * min(1.0, position / total)
            self.timeline.create_line(x, 5, x, 51, fill="#b22222", width=2)
            self.timeline.create_text(x + 2, 53, text=str(chapter_number), anchor="nw", fill="#b22222")
        self.timeline.create_text(10, 65, text="0:00", anchor="w")
        self.timeline.create_text(width + 10, 65, text=duration_text(total), anchor="e")


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
