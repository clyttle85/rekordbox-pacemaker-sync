# rekordbox-pacemaker-sync — LLM Handoff Document

This file gives any AI assistant (Claude, Gemini, Cursor, ChatGPT, etc.) full context to continue development immediately. Read this before touching any code.

---

## What This Project Is

A cross-platform (Windows and macOS) PyQt6 desktop GUI application that syncs playlists from **Rekordbox** (DJ software) to the **Tonium Pacemaker** (a hardware DJ device). The user sees their Rekordbox playlists as a checkbox tree — checking a playlist and clicking Sync adds it to the Editor Library. Cases can then be pushed to the physical device, with the app copying the audio files.

The Pacemaker calls playlists **"cases"**. This app creates/deletes cases in the Pacemaker's SQLite database (`music.db`) and manages the tracks within them.

---

## Three Reference Projects (also open in workspace)

| Project | Path | Purpose |
|---|---|---|
| Pacemaker | `d:\Documents\Code Projects\Python Projects\Pacemaker` | Original m3u8 → Pacemaker injector. Defines the `music.db` schema and insertion logic. |
| pyrekordbox | `d:\Documents\Code Projects\pyrekordbox` | Open-source library for reading Rekordbox's encrypted `master.db` (v6+). Note: the app supports 0.4.4+ and 0.5.x, which may differ from this local copy — see Import Notes below. |
| SSDReconstruction | `d:\Documents\Code Projects\SSDReconstruction` | Reference for Pacemaker storage/path handling. |

---

## Project Structure

```
rekordbox-pacemaker-sync/
├── main.py                    # Entry point — creates QApplication, launches MainWindow; hidden --selftest flag
├── requirements.txt
├── CLAUDE.md                  # This file
├── INSTALL_MAC.md             # End-user Mac install guide
├── BUILD_INSTALLER.md         # Windows build (PyInstaller + Inno Setup)
├── BUILD_INSTALLER_MAC.md     # Mac build (CI), signing and notarization setup
├── rekordbox_pacemaker_sync.spec      # Windows PyInstaller spec
├── rekordbox_pacemaker_sync_mac.spec  # macOS PyInstaller spec
├── installer.iss              # Windows Inno Setup script
├── setup_mac.command          # Mac run-from-source: first-time setup
├── run_mac.command            # Mac run-from-source: later launches
├── packaging/mac/entitlements.plist   # Hardened-runtime exceptions for signed builds
├── .github/
│   ├── workflows/build-mac.yml        # Builds the macOS DMGs
│   └── scripts/               # Helpers for the CI smoke tests
├── core/
│   ├── rekordbox_reader.py    # Reads Rekordbox master.db via pyrekordbox; RekordboxError types
│   ├── pacemaker_writer.py    # Reads/writes Pacemaker music.db (plain SQLite)
│   ├── sync_state.py          # Persists sync state as JSON alongside music.db
│   ├── device_finder.py       # Per-OS Editor/Device paths, device_root()/device_music_root()
│   ├── db_schema.py           # Creates a fresh Editor music.db if none exists
│   ├── file_import.py         # Drag-and-drop audio file/folder import
│   ├── logger.py              # Optional debug file logging
│   ├── selftest.py            # Checks behind main.py --selftest
│   └── m3u8_reader.py         # Parses M3U8 files + reads audio metadata via Mutagen
└── ui/
    ├── main_window.py         # Main window — see detailed breakdown below
    ├── playlist_tree.py       # Left panel: Rekordbox playlist tree with checkboxes
    ├── sync_panel.py          # Hidden state manager: Editor DB path, sync queue
    ├── player_bar.py          # Track preview playback bar
    ├── style.py               # Dark theme
    └── pacemaker_panel.py     # Reusable panel for Editor Library and Device Library
```

---

## UI Layout

Five-column horizontal layout with narrow connector strips between panels:

```
┌──────────────────┬──────┬──────────────────┬──────┬──────────────────────────┐
│ Rekordbox Library│ Sync │  Editor Library   │ Push │  Device Library          │
│ (playlist tree)  │  to  │  (Editor          │  to  │  (<device root>/         │
│                  │Editor│   music.db)       │Device│   .Pacemaker/music.db)   │
│ □ Folder         │  ↓   │ ☑ Case A (12 trk) │  →   │ Case A (12 tracks)       │
│ ├─ □ Playlist A  │      │ □ Case B (8 trk)  │      │ Case B (8 tracks)        │
│ └─ □ Playlist B  │      │                  │      │                          │
│                  │      │ [All][None]       │      │ [Refresh][Browse][⏏Eject]│
│                  │      │ [Refresh][Delete] │      │ [Delete]                 │
└──────────────────┴──────┴──────────────────┴──────┴──────────────────────────┘
```

- **Left (Rekordbox Library)**: Checkbox tree. Folders are tristate (checking a folder checks all children). Tree starts **collapsed**; expansion state is **persisted** via `QSettings`.
- **Connector strip 1**: "Sync to / Editor Library" button — triggers sync of checked playlists to Editor DB.
- **Editor Library**: Cases in the Editor `music.db` (see Two-Database Architecture). Checkboxes to select which cases to push. "All/None" toggle. Double-click or Rename button to rename. Multi-select delete.
- **Connector strip 2**: "Push (N) to / Device →" button — copies checked cases + their audio files to device.
- **Device Library**: Cases on the connected Pacemaker. Browse button for manual path. Eject button. Multi-select delete.

File menu: **Import from M3U8…** | **Repair Device Database…** | **Clean Up Device Files…** | Exit

Debug menu (top level, not under File): **Enable Debug Logging** | **Open Log File…**. The log, `rb_pm_sync.log`, sits next to the Editor `music.db`.

---

## Database Details

### Rekordbox `master.db` (encrypted, read-only)

Accessed via **pyrekordbox 0.4.4+ or 0.5.x**. SQLCipher-encrypted. pyrekordbox decrypts it with a key built into the package, so there is **no setup step**: `python -m pyrekordbox setup` doesn't exist in these versions. `_open_master_db` raises `RekordboxError` subclasses with user-facing messages for a missing library, one that can't be unlocked or read, or a failed SQLCipher load.

Key pyrekordbox API (the same in 0.4.4 and 0.5.0 apart from the import paths below):
```python
# See Import Notes: rekordbox_reader.py imports MasterDatabase for either version
db = MasterDatabase()                        # auto-finds master.db
db.get_playlist().all()                      # returns all DjmdPlaylist rows
# When called with a filter arg, returns the object directly (not a query):
pl = db.get_playlist(ID=playlist_id)         # returns DjmdPlaylist directly
contents = db.get_playlist_contents(pl)      # returns list directly (not query)
# Fields on DjmdContent:
#   .FolderPath, .Title, .ArtistName, .AlbumName, .AlbumArtistName,
#   .ComposerName, .GenreName, .LabelName, .KeyName, .BPM, .Rating,
#   .Length (duration secs), .BitRate, .SampleRate, .FileSize,
#   .ReleaseYear, .Commnt  ← NOTE: "Commnt" not "Comment"
```

**Known pyrekordbox gotchas (checked on 0.4.4 and 0.5.0):**
- `get_playlist(ID=x)` returns the object directly, not a query object. Guard with `hasattr(result, "one_or_none")`.
- `get_playlist_contents(pl)` similarly may return a list directly.
- The comment field is `.Commnt` (truncated), not `.Comment`.

### Pacemaker `music.db` (plain SQLite, read/write)

Key tables:

**`cases`** — playlists:
- `case_id` (PK), `name`, `date_created` (unix), `genre`, `year`, `creator_id`, `times_played`, `image_id`
- `creator_id` is always: `Tonium;Editor;2.0.2.14170;1117277940118978560`

**`tracks`** — audio files (44 columns):
- `track_id` (PK), `title`, `artist`, `location` (full file path), `format`, `play_time_secs`
- `bit_rate`, `sample_rate`, `file_size`, `album`, `album_artist`, `composer`
- `bpm`, `rating`, `key`, `genre`, `label`, `producer`, `remixer`
- `track_number`, `disc_number`, `number_of_discs`, `number_of_tracks`, `year`
- `date_added`, `date_modified`, `last_played`, `times_played`
- `is_part_of_c`, `rc_mixes`, `track_flags` (always 2), `global_id`, `structured_ct`, `discid`
- `ind_title`, `ind_artist`, `ind_album`, `ind_genre`, `ind_bpm` (indexed copies)
- `modified_by_ed`, `analyzed_by_ed` (always `"2.0.2.14170"`), `analysis_ver` (always 1)
- `cue_point`, `loop_in`, `loop_out` (always -1), `comments`

**`casetracks`** — junction table:
- `case_id`, `track_id` — insertion order (rowid) preserves track order within a case

Track deduplication is by `location` (file path). Tracks shared across cases are only deleted when they have zero casetracks references.

---

## Two-Database Architecture

**Editor DB** (the "working copy": sync Rekordbox playlists here first):
- Windows: `%APPDATA%\Tonium\Pacemaker\music.db`, shared with the Tonium Editor
- macOS: `~/Library/Application Support/RekordboxPacemakerSync/music.db`. This app owns it, as there's no Tonium Editor on modern macOS
- Created from `core/db_schema.py` if it doesn't exist
- Stores tracks with **computer file paths** (e.g. `C:\Users\me\Music\track.mp3` or `/Users/me/Music/track.mp3`)

**Device DB** (`<device root>/.Pacemaker/music.db`):
- `<device root>` is a drive letter on Windows (`J:\`) or a mount point on macOS (`/Volumes/PACEMAKER`)
- Stores tracks with **firmware paths**: `/pmdb_tracks/X/Y/HHHHHHHH.ext`
- Audio files live in `<device root>/.Pacemaker/Music/X/Y/HHHHHHHH.ext` (`device_finder.device_music_root`), each with a 0-byte `.str` companion file the firmware requires
- Never write computer paths into the device DB, which causes tracks to fail to load

`HHHHHHHH` is the CRC32 of the source file's full path, and `X`/`Y` are its first two hex characters. Because the source path differs between computers, the same file pushed from two machines is copied twice.

**Ticks mean "should be on the device"**, and `rb_sync_map.json` on the device maps Editor case IDs to device case IDs. `_confirm_and_sync_to_device` compares the ticks and the actual device contents, then makes three lists: add, remove, and update (a ticked case whose name or ordered tracks differ). It asks for confirmation before any remove or update, but not for adds alone.

**Push process** (`DeviceSyncWorker`):
1. For each checked Editor case, reads tracks via `get_case_tracks_as_trackinfo()`
2. `_copy_track` copies each file to the path above (skipped if it exists with the same size) and creates the `.str` file
3. Uses `dataclasses.replace(track, location=pmdb_location)` to create the device TrackInfo
4. Calls `device.insert_or_get_track(device_track)` with that location
5. Links track to case via `casetracks`

---

## Case Naming (Folder-Aware)

Rekordbox has folders; Pacemaker has no folder concept. Case names are auto-generated from the full folder path:

```
["2025", "08 - Aug", "IYKYK", "Deep or Heavy"]
→  "2025_Aug_IYKY_DeeporHeavy"
```

Rules (`_make_case_name` in `main_window.py`):
- Folder segments: strip leading ordinal prefix (`08 - ` → `Aug`), take first 4 chars
- Playlist name: strip ordinal prefix, remove spaces, take first 12 chars
- Join all with `_`
- Root-level playlists (no folders): kept as-is, truncated to 24 chars with `…`
- If the name is already taken (ignoring upper/lower case), `_2`, `_3`… is added. New Case and rename refuse a duplicate instead.

Cases can be renamed manually in the Editor Library panel (double-click or Rename button).

---

## Sync Logic (Current — Append-Only)

1. User checks playlists → `_recompute_queue()` generates ADD items only (no remove/update)
2. User clicks "Sync to Editor Library" → confirmation dialog
3. `SyncWorker` (QThread) runs `writer.add_playlist(name, tracks)` for each
4. On success: **tree unchecks all** (`_tree.uncheck_all()`), Editor Library refreshes
5. No sync state is recorded — the queue always starts empty next session

**Deleting cases** is a manual action via the Delete button in either library panel. Multi-select supported (Ctrl/Shift+Click). Single confirmation dialog lists all names.

---

## Sync State

Still stored as `rb_pacemaker_sync_state.json` alongside `music.db`, but now only used for:
- Determining whether a deleted Editor case should have its sync state entry cleaned up
- The green dot indicator (managed cases) in the Editor Library panel

The tree no longer pre-checks previously synced playlists. `_refresh_tree()` passes `set()` for `synced_ids`.

```json
{
  "12345": {
    "pacemaker_case_id": 7,
    "playlist_name": "My Playlist",
    "last_synced": "2026-03-26T15:00:00",
    "track_locations": ["/path/to/track1.mp3"]
  }
}
```

---

## File: `ui/main_window.py` — Detailed Breakdown

### Classes

**`SyncWorker(QObject)`** — Rekordbox → Editor sync thread
- Signals: `progress(int, int, str)`, `finished(bool, str)`
- `run()`: iterates operations, calls `writer.add_playlist(name, tracks, cb)` for each

**`DeviceSyncWorker(QObject)`** — Editor → Device push thread
- Signals: `progress(int, int, str)`, `finished(bool, str)`
- `__init__(editor_db, device_db, cases)`: `cases` is list of `{"case_id", "name", "track_count", ...}`
- `expected_locations(tracks, device_db)`: what a case should hold on the device. Tracks whose source is missing stay if their copy is already there, so an unplugged music drive doesn't strip them
- `_copy_track(src_path, device_db) -> (local_dest, pmdb_location)` (static): copies file, skips if same size exists, creates the `.str` companion
- `run()`: copies files, writes device-path tracks to device DB, per-track progress

**`RepairDeviceDbDialog(QDialog)`** — File → Repair Device Database…
- Scans device DB for tracks with non-existent `location` paths
- Walks a chosen folder for audio files, builds `{lowercase_filename: full_path}` index
- Matches broken tracks by filename, shows count of fixable vs unmatched
- Applies fixes via `UPDATE tracks SET location = ? WHERE track_id = ?`
- Auto-populates device DB path and `<device root>/.Pacemaker/Music` scan folder if device panel has a path

**`M3U8ImportDialog(QDialog)`** — File → Import from M3U8…
- Reads M3U8 file, reads audio metadata via Mutagen, imports as a new case

**`MainWindow(QMainWindow)`**
- `_build_ui()`: 5-column layout — `left_splitter` | connector(sync_btn) | `_editor_panel` | connector(push_btn) | `_device_panel`
- `_make_connector(button)`: static method, 115px wide strip, button centred vertically
- `_recompute_queue()`: ADD-only, no state lookup
- `_confirm_and_sync()`: all ops are ADD; after success unchecks tree
- `_confirm_and_push_to_device()`: gets `_editor_panel.get_checked_cases()`, warns if none
- `_run_push()`: sets `QProgressDialog` max to `sum(c["track_count"] for c in cases)`
- `_on_rename_editor_case(cases)`: sequential `QInputDialog` loop; Cancel stops sequence
- `_delete_cases()`: single confirmation dialog, loops through deletions
- `_eject_device()`: Windows uses `DeviceIoControl` lock/dismount/eject (`_do_eject_windows`), macOS uses `diskutil eject` (`_do_eject_mac`)
- `_build_playlist_path_map()`: `{playlist_id: [folder, ..., name]}`
- `_shorten_segment(segment, max_chars)`: strips `NN - ` prefix, truncates
- `_make_case_name(parts)`: builds compact underscore-joined case name

### Editor Library panel (`_editor_panel`)
```python
PacemakerLibraryPanel(
    title="Editor Library",
    show_browse=False,
    show_push_button=True,   # push_button placed in connector strip externally
    show_rename=True,        # Rename button + double-click to rename
    show_checkboxes=True,    # Per-case checkboxes for selective push
)
```

### Device Library panel (`_device_panel`)
```python
PacemakerLibraryPanel(
    title="Device Library",
    show_browse=True,        # Browse button for manual DB path selection
    show_push_button=False,
    show_eject=True,         # ⏏ Eject button
)
```

---

## File: `ui/pacemaker_panel.py` — Detailed Breakdown

**`PacemakerLibraryPanel(QWidget)`**

Constructor params (all optional `bool`):
- `show_browse`: Browse… button + `db_path_changed` signal
- `show_push_button`: creates `_push_btn`; must be retrieved via `.push_button` property and placed externally
- `show_rename`: Rename button + `itemDoubleClicked` → `rename_requested` signal
- `show_checkboxes`: "Check: All / None" row; items get checkboxes; `_update_push_button()` called on change
- `show_eject`: ⏏ Eject button → `eject_requested` signal

Signals:
- `delete_requested = pyqtSignal(list)` — list of `{"case_id", "name"}` dicts
- `rename_requested = pyqtSignal(list)` — same format
- `eject_requested = pyqtSignal()`
- `refresh_requested = pyqtSignal()`
- `push_requested = pyqtSignal()`
- `db_path_changed = pyqtSignal(str)`

Key methods:
- `load_cases(cases, managed_case_ids)`: populates list; managed IDs shown in green `#4caf50`
- `get_checked_cases()`: returns cases whose checkbox is ticked
- `selected_cases()`: returns highlighted (selected) cases
- `_update_push_button()`: updates push button text to `"Push (N) to\nDevice →"`
- `_on_selection_changed()`: enables Delete/Rename buttons; shows `"Delete (N)"` for N>1

---

## File: `ui/playlist_tree.py` — Detailed Breakdown

**`PlaylistTreeWidget(QTreeWidget)`**
- `load_tree(nodes, synced_ids)`: populates tree; calls `_restore_expansion()` (not `expandAll`)
- `uncheck_all()`: unchecks all items, emits `selection_changed`
- Expansion state persisted via `QSettings("rekordbox-pacemaker-sync", "PlaylistTree")`
- `_on_item_expanded/collapsed`: saves/restores set of expanded folder IDs as JSON in QSettings

---

## File: `core/pacemaker_writer.py` — Detailed Breakdown

Key methods:
- `create_case(name) -> int`
- `rename_case(case_id, new_name)`
- `delete_case(case_id)` — deletes case + casetracks, NOT tracks
- `get_case_track_locations(case_id) -> set[str]`
- `clear_case_tracks(case_id)`
- `insert_or_get_track(track: TrackInfo) -> int` — dedup by `location`
- `link_track_to_case(case_id, track_id)`
- `delete_orphan_tracks(locations)` — only deletes if no remaining casetracks refs
- `sync_playlist(case_id, tracks, cb) -> list[str]` — replace case contents
- `add_playlist(name, tracks, cb) -> (case_id, locations)`
- `remove_playlist(case_id, locations)` — delete case + orphan tracks
- `get_case_tracks_as_trackinfo(case_id) -> list[TrackInfo]` — ordered by casetracks rowid
- `find_track_id(track) -> Optional[int]` — match by title+artist+ABS(play_time_secs-?)<=1
- `get_all_cases() -> list[dict]` — `[{"case_id", "name", "track_count"}]`, sorted by name

---

## Important: Python Compatibility

**Python 3.9+ is supported.** The Windows build uses 3.9, the Mac CI build uses 3.12. All files include `from __future__ import annotations` as the first import after the module docstring. Without it, `X | Y`, `list[str]`, `dict[str, int]` etc. raise `TypeError` at runtime on 3.9.

**Any new file must include this line.**

---

## Import Notes (supported pyrekordbox versions)

`requirements.txt` allows `>=0.4.4,<0.6`. pip picks by Python version, because 0.5 needs Python 3.11+.

| | 0.4.x | 0.5.x |
|---|---|---|
| Python | 3.9+ | 3.11+ |
| Database class | `pyrekordbox.Rekordbox6Database` | `pyrekordbox.masterdb.MasterDatabase` |
| Table models | `pyrekordbox.db6.tables` | `pyrekordbox.masterdb.models` |

`rekordbox_reader.py` tries the 0.5 paths first and falls back to 0.4, exposing `MasterDatabase` and `DjmdContent`. Import from there rather than from pyrekordbox directly. `Rekordbox6Database` still exists in 0.5 but warns, and is due for removal in 0.6. If the import fails, `PYREKORDBOX_AVAILABLE` is False and the real error text reaches the user's dialog.

---

## Setup & Running

```bash
# Windows, or any OS from a terminal
pip install -r requirements.txt
python main.py
```

On a Mac, double-click `setup_mac.command` the first time and `run_mac.command` after that. No Homebrew packages are needed.

Database paths are auto-detected on startup and the device is polled every 2 seconds (see Two-Database Architecture).

`python main.py --selftest` checks the Rekordbox stack and Qt Multimedia, opens the main window for a few seconds, then exits 0. Set `QT_QPA_PLATFORM=offscreen` to run it headless.

## Building

- **Windows:** see `BUILD_INSTALLER.md`. Built locally with PyInstaller + Inno Setup.
- **macOS:** `.github/workflows/build-mac.yml` builds an arm64 and an Intel DMG on GitHub Actions, on manual dispatch or a `v*` tag (tags also publish a GitHub Release). PyInstaller can't cross-compile, so CI is the only way to build for Mac from Windows. Each build runs the smoke tests. Signing and notarization steps run only once the `APPLE_*` repo secrets are set. See `BUILD_INSTALLER_MAC.md`.
- A new module imported only dynamically must be added to the Mac spec's `hiddenimports`. The smoke tests are what catch a miss.

---

## Dependencies

```
PyQt6>=6.4.0              # GUI
pyrekordbox>=0.4.4,<0.6   # Rekordbox DB reader
sqlcipher3-wheels         # SQLCipher for the Rekordbox DB, prebuilt for Windows and macOS
mutagen>=1.47.0           # Audio metadata (used by M3U8 import)
sqlalchemy>=2.0.0         # ORM used internally by pyrekordbox
```

---

## Current Status

All core features are implemented and working:

| Feature | Status |
|---|---|
| Rekordbox playlist tree (tristate folders, collapse/expand persist) | ✅ |
| Sync checked playlists → Editor Library (append-only, unchecks after) | ✅ |
| Sync queue preview (ADD items only, colour-coded) | ✅ |
| Background sync thread with per-track progress | ✅ |
| Editor Library: checkboxes, rename (single + bulk), multi-select delete | ✅ |
| Push Editor cases to Device: copies audio files, writes device paths | ✅ |
| Push: per-track progress dialog, filename collision handling | ✅ |
| Device Library: browse, eject, multi-select delete | ✅ |
| File → Repair Device Database: fixes broken paths by filename match | ✅ |
| File → Import from M3U8 | ✅ |
| Folder-aware case naming (compact, underscore-joined) | ✅ |
| macOS DMG builds on GitHub Actions, smoke tested in the bundle | ✅ |
| macOS on real hardware with a real Rekordbox library and Pacemaker | ⚠️ Not yet tested |

## Known Minor Gaps / Future Work

- `disc_number` / `number_of_discs` in `m3u8_reader.py` both read from `TPOS` — should split on `/` (low priority)
- No batch transactions in `pacemaker_writer.py` — each track insert commits individually (fine for typical sizes)
- No test suite beyond `--selftest` and the CI smoke tests
- UI modernisation (dark theme, flat buttons, panel headers) — discussed but not yet implemented
- Repair tool matches only by filename; a secondary match by title+artist+duration could rescue renamed files
