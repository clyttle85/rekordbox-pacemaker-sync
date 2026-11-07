# Rekordbox → Pacemaker Sync

A PyQt6 desktop app for syncing playlists from **Rekordbox** to the **Tonium Pacemaker** hardware DJ device.

## Download for Mac

Get the `.dmg` for your Mac from the [Releases page](https://github.com/clyttle85/rekordbox-pacemaker-sync/releases/latest), then follow the step-by-step **[Mac install guide](INSTALL_MAC.md)**. It needs macOS 14.4 or newer, and no technical knowledge.

## What it does

Check playlists in your Rekordbox library, sync them into an Editor library, then push them to a connected Pacemaker — the app copies the audio files and writes the device's `music.db` for you.

```
Rekordbox Library  →  Editor Library  →  Device Library
   (playlist tree)      (staging DB)      (Pacemaker on J:\)
```

## Features

- Tristate checkbox tree of Rekordbox folders/playlists, with expansion state remembered between sessions
- Import checked playlists into a local Editor library
- Sync Editor cases to a connected Pacemaker, copying audio files and writing device-local paths, with warnings before anything is removed, overwritten or won't fit
- Backs up the Pacemaker's database to your computer before changing it
- Drag and drop audio files/folders from Explorer/Finder straight into the library, optionally directly into a case
- Create cases by hand, drag tracks onto a case to add them, drag within an open case to reorder, remove tracks from a case without deleting them from the library
- Rename, delete, and browse cases in both the Editor and Device libraries
- Waveform previews and click-to-preview playback for tracks
- Dark theme UI
- Repair tool for fixing broken device track paths (e.g. after moving files)
- Import playlists directly from an M3U8 file
- Orphaned device file cleanup and optional debug logging (Debug menu)

## Requirements

- Windows or macOS (see the macOS note below)
- Python 3.9 or newer, if running from source
- Rekordbox 6+ (for the encrypted `master.db` that this app reads)
- A Tonium Pacemaker connected as a removable drive, for pushing to a device

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run
python main.py
```

On a Mac, you can double-click `setup_mac.command` instead, then `run_mac.command` for later launches.

Editor and Device database paths are auto-detected on startup, and the
Editor database is created fresh (with the full Pacemaker schema) if none is
found — no need to have ever installed the real Tonium Editor software:

- **Editor**:
  - Windows — `C:\Users\<you>\AppData\Roaming\Tonium\Pacemaker\music.db`
  - macOS — `~/Library/Application Support/RekordboxPacemakerSync/music.db`
    (this app's own location — there's no equivalent legacy Tonium Editor
    install on Mac)
- **Device**: `<device root>/.Pacemaker/music.db` — a drive letter on
  Windows (e.g. `J:\.Pacemaker\music.db`), a mount under `/Volumes` on macOS
  (e.g. `/Volumes/PACEMAKER/.Pacemaker/music.db`)

### macOS note

The Pacemaker Editor software is long dead on modern macOS, which left Mac
users with no way to get music onto the device at all. This app now runs on
macOS too. Automated builds launch and pass self-checks on Apple Silicon and
Intel, but it **hasn't yet been tested on a real Mac with a real Rekordbox
library and Pacemaker**.

## Building a standalone app

- **Windows**: see [BUILD_INSTALLER.md](BUILD_INSTALLER.md) for the
  PyInstaller + Inno Setup process. Produces an installer exe.
- **macOS**: see [BUILD_INSTALLER_MAC.md](BUILD_INSTALLER_MAC.md). GitHub Actions
  builds a drag-to-install DMG for Apple Silicon and for Intel.

## Project structure

```
main.py                    # Entry point
core/
  rekordbox_reader.py       # Reads Rekordbox master.db via pyrekordbox
  pacemaker_writer.py        # Reads/writes Pacemaker music.db (SQLite)
  sync_state.py              # Older record of synced playlists
  sync_map.py                # Tracks which Editor cases are on which device
  device_backup.py           # Backs up a device's database before changes
  audio_formats.py           # Which audio formats can be imported
  device_finder.py           # Auto-detects Editor/Device DB paths (cross-platform);
                              #   device_root()/device_music_root()/is_on_device()
                              #   helpers used throughout for device-relative paths
  db_schema.py                # Bootstraps a fresh Editor music.db if none exists
  m3u8_reader.py                # M3U8 + audio metadata (Mutagen) for manual import
  file_import.py                 # Drag-and-drop file/folder import into the library
  logger.py                       # Optional debug file logging
ui/
  main_window.py             # Main window and background sync/push workers
  playlist_tree.py            # Rekordbox playlist tree widget
  sync_panel.py                # Editor sync queue panel
  pacemaker_panel.py            # Reusable Editor/Device library panel
  player_bar.py                  # Track preview playback bar
  style.py                        # Dark theme stylesheet/palette
```

See [CLAUDE.md](CLAUDE.md) for a full technical deep-dive (database schemas, sync internals, and conventions) intended for AI coding assistants working in this repo.

## Roadmap

- ~~Windows installer distribution~~ — done, see [BUILD_INSTALLER.md](BUILD_INSTALLER.md).
- macOS: test on a real Mac with a real Rekordbox library and Pacemaker. Builds and automated self-checks already pass, see [BUILD_INSTALLER_MAC.md](BUILD_INSTALLER_MAC.md).
- Turn on code signing + notarization for the macOS build, so users skip Gatekeeper's warning. The CI steps are ready, but it needs an Apple Developer ID. See [BUILD_INSTALLER_MAC.md](BUILD_INSTALLER_MAC.md).
- Split `disc_number` / `number_of_discs` correctly in `m3u8_reader.py` (currently both read from the same tag)
- Batch DB transactions in `pacemaker_writer.py` for large syncs
