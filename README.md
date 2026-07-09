# Rekordbox → Pacemaker Sync

A PyQt6 desktop app for syncing playlists from **Rekordbox** to the **Tonium Pacemaker** hardware DJ device.

Check playlists in your Rekordbox library, sync them into an Editor library, then push them to a connected Pacemaker — the app copies the audio files and writes the device's `music.db` for you.

```
Rekordbox Library  →  Editor Library  →  Device Library
   (playlist tree)      (staging DB)      (Pacemaker on J:\)
```

## Features

- Tristate checkbox tree of Rekordbox folders/playlists, with expansion state remembered between sessions
- Sync checked playlists into a local Editor library (`%APPDATA%\Tonium\Pacemaker\music.db`)
- Push Editor cases to a connected Pacemaker, copying audio files and writing device-local paths
- Rename, delete, and browse cases in both the Editor and Device libraries
- Waveform previews and click-to-preview playback for tracks
- Dark theme UI
- Repair tool for fixing broken device track paths (e.g. after moving files)
- Import playlists directly from an M3U8 file
- Orphaned device file cleanup and optional debug logging (File → Debug menu)

## Requirements

- Windows (the Pacemaker integration relies on Windows drive letters and paths)
- Python 3.9
- Rekordbox 6+ (for the encrypted `master.db` that this app reads)
- A Tonium Pacemaker connected as a removable drive, for pushing to a device

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# One-time: let pyrekordbox find/derive the decryption key for your Rekordbox master.db
python -m pyrekordbox setup

# Run
python main.py
```

Editor and Device database paths are auto-detected on startup:

- **Editor**: `C:\Users\<you>\AppData\Roaming\Tonium\Pacemaker\music.db`
- **Device**: `<drive>:\.Pacemaker\music.db` (e.g. `J:\.Pacemaker\music.db`)

## Building a standalone Windows installer

See [BUILD_INSTALLER.md](BUILD_INSTALLER.md) for the current PyInstaller + Inno Setup process. This is being streamlined toward a simpler one-step installable build — see the roadmap note below.

## Project structure

```
main.py                    # Entry point
core/
  rekordbox_reader.py       # Reads Rekordbox master.db via pyrekordbox
  pacemaker_writer.py        # Reads/writes Pacemaker music.db (SQLite)
  sync_state.py              # Tracks which Editor cases are on which device
  device_finder.py           # Auto-detects Editor and Device database paths
  m3u8_reader.py              # M3U8 + audio metadata (Mutagen) for manual import
  logger.py                  # Optional debug file logging
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

- Windows installer distribution — packaging the app as a proper installable Windows application (beyond the current manual PyInstaller/Inno Setup steps) so it can be installed and updated without a Python environment.
- Split `disc_number` / `number_of_discs` correctly in `m3u8_reader.py` (currently both read from the same tag)
- Batch DB transactions in `pacemaker_writer.py` for large syncs
- Automated test suite
