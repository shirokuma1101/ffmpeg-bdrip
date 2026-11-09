"""Mount a Windows ISO temporarily for read-only BDMV analysis."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path


def _powershell(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if result.returncode:
        raise OSError(result.stderr.strip() or "ISOのマウント操作に失敗しました。")
    return result.stdout.strip()


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def mount_iso(path: Path) -> tuple[Path, bool]:
    if sys.platform != "win32":
        raise OSError("ISOの自動マウントはWindowsのみ対応しています。")
    image = path.expanduser().resolve()
    if not image.is_file() or image.suffix.lower() != ".iso":
        raise ValueError("存在するISOファイルを選択してください。")
    literal = _literal(image)
    script = (f"$image=Get-DiskImage -ImagePath {literal} -ErrorAction Stop; "
              "$created=$false; "
              f"if (-not $image.Attached) {{$image=Mount-DiskImage -ImagePath {literal} -PassThru -ErrorAction Stop; $created=$true}}; "
              "for ($i=0; $i -lt 10; $i++) {"
              "$volume=$image | Get-Volume -ErrorAction SilentlyContinue | Where-Object DriveLetter | Select-Object -First 1; "
              "if ($volume) {break}; Start-Sleep -Milliseconds 200}; "
              f"if (-not $volume) {{if ($created) {{Dismount-DiskImage -ImagePath {literal} -ErrorAction SilentlyContinue}}; "
              "throw 'ドライブ文字を取得できませんでした。'}; "
              "@{drive=[string]$volume.DriveLetter; created=$created} | ConvertTo-Json -Compress")
    info = json.loads(_powershell(script))
    drive = info["drive"]
    if len(drive) != 1 or not drive.isalpha():
        raise OSError("ISOのドライブ文字が正しくありません。")
    return Path(f"{drive}:\\"), bool(info["created"])


def unmount_iso(path: Path) -> None:
    if sys.platform == "win32":
        _powershell(f"Dismount-DiskImage -ImagePath {_literal(path.expanduser().resolve())} -ErrorAction Stop")
