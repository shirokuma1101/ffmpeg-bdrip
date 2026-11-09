"""Windows ISO mount command construction (no real drive changes in tests)."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ffmpeg_bdrip.iso_mount import mount_iso, unmount_iso


class IsoMountTests(unittest.TestCase):
    def test_mount_uses_encoded_powershell_and_reports_ownership(self):
        with tempfile.TemporaryDirectory() as folder:
            iso = Path(folder) / "disc's image.iso"
            iso.touch()
            with patch("ffmpeg_bdrip.iso_mount.sys.platform", "win32"), \
                 patch("ffmpeg_bdrip.iso_mount.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = json.dumps({"drive": "Z", "created": True})
                self.assertEqual(mount_iso(iso), (Path("Z:\\"), True))
                command = run.call_args.args[0]
                script = base64.b64decode(command[-1]).decode("utf-16le")
                self.assertIn("disc''s image.iso", script)
                unmount_iso(iso)
                self.assertIn("Dismount-DiskImage", base64.b64decode(run.call_args.args[0][-1]).decode("utf-16le"))
