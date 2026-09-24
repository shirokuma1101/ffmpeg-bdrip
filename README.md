# ffmpeg-bdrip

BDMVから抽出した `.ts` / `.m2ts` や既存の `.mkv` を、再エンコードせず `.mkv` に変換するCLI・GUIです。

## 前提

- Python 3.10以上
- このプロジェクトに同梱した Windows 64-bit 版 FFmpeg 9.0.2 を使用できます（`vendor/ffmpeg/bin`）。別のFFmpegを使う場合はCLIで `--ffmpeg` を指定してください。
- GUIのストリーム解析には同梱の `ffprobe` を使用します。
- Windows 64-bit版のGUI用ドラッグ＆ドロップ拡張を同梱しています。パッケージとしてインストールする場合も依存関係から導入されます。

```powershell
python -m pip install -e .
```

## 使い方

GUIを使う場合：

```powershell
python .\bdmv2mkv_gui.py
```

インストール後は次でも起動できます。

```powershell
ffmpeg-bdrip-gui
```

GUIで入力ファイルを選ぶと、`ffprobe` の解析結果を表示します。映像・音声・字幕などをチェックで選び、音声ごとに `copy`（そのまま）または `pcm_s24le` を指定できます。音声や字幕の出力順を変えたい場合は「詳細マッピングを使う」を有効にし、ドロップダウン、追加・削除、上下移動ボタンで指定します。解析できない場合は全ストリームの自動コピーを使用できます。

入力欄またはその下の案内にWindows Explorerからファイルをドラッグ＆ドロップできます。1回につき1ファイルを受け付けます。出力先が自動設定のままなら、入力に合わせて出力名も更新します。出力先を手動指定している場合は維持します。

変換中は処理済み時刻と速度を表示します。入力の長さを解析できた場合は進捗率、プログレスバー、速度に基づく残り時間（ETA）も表示します。長さや速度が不明な場合、ETAは「—」と表示し、長さが不明な場合は動作中のバーを表示します。数値表示はバーの下に配置され、バーの幅は数値の桁数で変わりません。

「フォルダ一覧…」では、指定フォルダ内のメディアファイルを `ffprobe` で順次解析し、長さと映像・音声・字幕の本数を一覧表示します。行を展開するとストリームごとの情報を確認できます。必要に応じて「サブフォルダも含める」を有効にでき、解析は途中で停止できます。ファイルを選んで「選択したファイルを入力に設定」を押すと、変換画面に戻ります。ダブルクリックでも設定できます。

### プリセット

GUI上部の「プリセット」から選んで「適用」を押すと、解析設定・メタデータ／チャプター設定・対象ストリームと音声コーデックをまとめて設定できます。現在の設定は名前を付けて保存でき、不要なプリセットは削除できます。保存対象に入力・出力ファイル名、外部音声ファイル、既存ファイルの上書き設定は含まれません。

初期プリセットは `C:\Users\shiro1103-remotedev\OneDrive\Tools\Code\Windows\Batch` の `bdrip_normal.bat`、`bdrip_normal_nosub.bat`、`bdrip_multiaudio_nosub.bat`、`bdrip_multiaudio3.bat`、`conv.bat` などをもとにしています。「音声2本（字幕あり・PCM）」では音声の先頭2本をPCM化し、字幕の先頭1本を含めます。ストリームは種類ごとの「1本目」「2本目」として記録するため、ファイルごとに実際のストリーム番号が異なっても適用できます。必要な本数がない場合はエラーを表示します。結合処理やWAV出力は現在の単一ファイルからMKVへの変換対象外です。

追加・変更・削除したプリセットは `%LOCALAPPDATA%\ffmpeg-bdrip\presets.json` に保存されます。初期プリセットも削除でき、削除結果は次回起動時も維持されます。

```powershell
python .\bdmv2mkv.py .\00000.m2ts
python .\bdmv2mkv.py .\00000.m2ts .\movie.mkv
python .\bdmv2mkv.py .\00000.m2ts .\movie.mkv -y
python .\bdmv2mkv.py .\movie.mkv
python .\bdmv2mkv.py .\movie.mkv .\movie-edited.mkv

# 既存コマンド相当: 全ストリーム、メタデータ／チャプター削除、深い解析
python .\bdmv2mkv.py .\00000.m2ts .\movie.mkv --deep-probe --strip-metadata --no-chapters

# 映像1本＋音声2本（1本目をPCM化）＋字幕1本
python .\bdmv2mkv.py .\input.ts .\out.mkv `
  --deep-probe --strip-metadata --no-chapters `
  --video-map 0:0 `
  --audio-map 0:1 --audio-codec pcm_s24le `
  --audio-map 0:2 --audio-codec copy `
  --subtitle-map 0:s:0

# 1200秒から9690秒間だけ切り出す
python .\bdmv2mkv.py .\input.ts .\out.mkv --start 1200 --duration 9690

# 入力音声を先頭から最大2本だけ含める（0なら音声なし）
python .\bdmv2mkv.py .\input.ts .\out.mkv --audio-count 2

# MP4の映像・音声に外部MKA音声を追加
python .\bdmv2mkv.py .\input.mp4 .\out.mkv --extra-audio .\audio.mka
```

出力先を省略すると、TS/M2TS入力では同じベース名の `.mkv`、MKV入力では `<元の名前>.remux.mkv` を入力ファイルと同じ場所に作成します。入力と出力に同じファイルは指定できません。

変換前にコマンドを確認するには次を使います。

```powershell
python .\bdmv2mkv.py .\00000.m2ts --dry-run
```

`-c copy`（ストリームコピー）を使うため、デフォルトでは映像・音声・字幕を再エンコードしません。`--audio-codec pcm_s24le`を指定した音声だけは、PCMへのデコード処理が行われます。入力TSが複数の分割ファイルに分かれている場合は、プレイリスト単位での結合処理が別途必要です。

入力ファイルのタイムスタンプが途中から始まる場合も、出力MKVの先頭時刻は0へ揃えます。過去に作成したMKVが途中の時刻から再生される場合は、そのMKVを入力にして別名のMKVへ再変換してください（映像・音声をコピーする設定なら再エンコードしません）。

`--audio-count N`を指定すると、入力側の音声ストリームを先頭から最大N本に制限できます。指定しない場合は全音声を含めます。`--audio-map`を使う場合は、指定した音声マップの先頭N本が対象になります。
