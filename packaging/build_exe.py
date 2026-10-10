"""Builds dist/PlaudIO.exe with PyInstaller. Run on Windows: python packaging/build_exe.py
(build_windows.bat and the GitHub workflow both call this)."""
import os
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "plaudio" / "plaudio.ico"
SEP = ";" if os.name == "nt" else ":"

PyInstaller.__main__.run([
    str(ROOT / "packaging" / "plaudio_gui.py"),
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "PlaudIO",
    "--icon", str(ICON),
    "--add-data", f"{ICON}{SEP}.",            # window icon, found via sys._MEIPASS
    "--collect-submodules", "mcp",
    "--collect-data", "googleapiclient",      # bundled Drive discovery document
    "--hidden-import", "googleapiclient.discovery",
    "--hidden-import", "google_auth_oauthlib.flow",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
])
