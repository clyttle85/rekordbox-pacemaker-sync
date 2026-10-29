# -*- mode: python ; coding: utf-8 -*-
# macOS PyInstaller spec. Must be run ON macOS — PyInstaller cannot
# cross-compile. See BUILD_INSTALLER_MAC.md for the full build process.
#
# Mirrors rekordbox_pacemaker_sync.spec (the Windows build) but produces a
# RekordboxPacemakerSync.app bundle instead of an .exe. Kept as a separate
# file rather than branching the Windows spec, so the working Windows build
# is never at risk from Mac-specific changes.
from __future__ import annotations

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.icns', '.'),
    ],
    hiddenimports=[
        'pyrekordbox',
        'pyrekordbox.db6',
        'pyrekordbox.db6.tables',
        'pyrekordbox.db6.database',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.pool',
        'mutagen',
        'mutagen.mp3',
        'mutagen.flac',
        'mutagen.mp4',
        'mutagen.asf',
        'mutagen.aiff',
        'mutagen.wave',
        'mutagen.id3',
        'mutagen.easyid3',
        'PyQt6.QtMultimedia',
        'PyQt6.QtMultimediaWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RekordboxPacemakerSync',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,   # set to your Developer ID to sign at build time
    entitlements_file=None,
    icon='icon.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='RekordboxPacemakerSync',
)

app = BUNDLE(
    coll,
    name='RekordboxPacemakerSync.app',
    icon='icon.icns',
    bundle_identifier='com.chrislyttle.rekordboxpacemakersync',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': '1.0.0',
        'CFBundleVersion': '1.0.0',
        'NSHumanReadableCopyright': 'Chris Lyttle',
    },
)
