# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all


repository = Path(SPECPATH).parent.resolve()
datas = []
binaries = []
hiddenimports = [
    "pythoncom",
    "win32api",
    "win32com.client",
    "win32con",
    "win32cred",
    "win32gui",
    "win32process",
    "win32security",
    "win32ui",
]
for package in (
    "faster_whisper",
    "openwakeword",
    "pycaw",
    "pywinauto",
    "send2trash",
    "sounddevice",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(repository / "tools" / "javis_entry.py")],
    pathex=[str(repository / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
    optimize=1,
)
python_archive = PYZ(analysis.pure)
console_executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="JAVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
tray_executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="JAVIS-Tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=True,
)
distribution = COLLECT(
    console_executable,
    tray_executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="JAVIS",
)
