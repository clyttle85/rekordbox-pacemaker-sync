# -*- mode: python ; coding: utf-8 -*-
# macOS PyInstaller spec. Must be run ON macOS — PyInstaller cannot
# cross-compile. See BUILD_INSTALLER_MAC.md for the full build process.
#
# Mirrors rekordbox_pacemaker_sync.spec (the Windows build) but produces a
# RekordboxPacemakerSync.app bundle instead of an .exe. Kept as a separate
# file rather than branching the Windows spec, so the working Windows build
# is never at risk from Mac-specific changes.
from __future__ import annotations

import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Set by CI from the release tag. Apple only accepts dotted numbers here, so
# "0.0.0" marks a build that didn't come from a tagged release.
APP_VERSION = os.environ.get('APP_VERSION', '0.0.0')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('icon.icns', '.'),
    ],
    hiddenimports=[
        # Covers either supported layout (0.4: pyrekordbox.db6, 0.5: .masterdb)
        *collect_submodules('pyrekordbox'),
        'sqlcipher3',
        'sqlcipher3.dbapi2',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.dialects.sqlite.pysqlcipher',
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
    upx=False,
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
    upx=False,
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
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHumanReadableCopyright': 'Chris Lyttle',
        # Shown in the macOS permission prompts for these locations
        'NSRemovableVolumesUsageDescription':
            'Reads and writes your music library on the connected Pacemaker.',
        'NSDocumentsFolderUsageDescription':
            'Reads audio files in your Documents folder to sync them to the Pacemaker.',
        'NSDownloadsFolderUsageDescription':
            'Reads audio files in your Downloads folder to sync them to the Pacemaker.',
        'NSDesktopFolderUsageDescription':
            'Reads audio files on your Desktop to sync them to the Pacemaker.',
    },
)
