# rekordbox-pacemaker-sync — LLM Handoff Document

This file gives any AI assistant (Claude, Gemini, Cursor, ChatGPT, etc.) full context to continue development immediately. Read this before touching any code.

---

## What This Project Is

A PyQt6 desktop GUI application that syncs playlists from **Rekordbox** (DJ software) to the **Tonium Pacemaker** (a hardware DJ device). The user sees their Rekordbox playlists as a checkbox tree — checking a playlist and clicking Sync adds it to the Editor Library. Cases can then be pushed to the physical device, with the app copying the audio files.

The Pacemaker calls playlists **"cases"**. This app creates/deletes cases in the Pacemaker's SQLite database (`music.db`) and manages the tracks within them.

---

## Three Reference Projects (also open in workspace)

| Project | Path | Purpose |
|---|---|---|
| Pacemaker | `d:\Documents\Code Projects\Python Projects\Pacemaker` | Original m3u8 → Pacemaker injector. Defines the `music.db` schema and insertion logic. |
| pyrekordbox | `d:\Documents\Code Projects\pyrekordbox` | Open-source library for reading Rekordbox's encrypted `master.db` (v6+). Note: the installed version (0.4.4) differs from this local copy — see Import Notes below. |
| SSDReconstruction | `d:\Documents\Code Projects\SSDReconstruction` | Reference for Pacemaker storage/path handling. |

---

## Project Structure

```
rekordbox-pacemaker-sync/
├── main.py                    # Entry point — creates QApplication, launches MainWindow
├── requirements.txt
├── CLAUDE.md                  # This file
├── core/
│   ├── rekordbox_reader.py    # Reads Rekordbox master.db via pyrekordbox
│   ├── pacemaker_writer.py    # Reads/writes Pacemaker music.db (plain SQLite)
│   ├── sync_state.py          # Persists sync state as JSON alongside music.db
│   ├── device_finder.py       # Auto-detects Editor and Device database paths
│   └── m3u8_reader.py         # Parses M3U8 files + reads audio metadata via Mutagen
└── ui/
    ├── main_window.py         # Main window — see detailed breakdown below
    ├── playlist_tree.py       # Left panel: Rekordbox playlist tree with checkboxes
    ├── sync_panel.py          # Middle panel: Editor DB path, sync queue, progress
    └── pacemaker_panel.py     # Reusable panel for Editor Library and Device Library
```

---

## UI Layout

Five-column horizontal layout with narrow connector strips between panels:

```
┌──────────────────┬──────┬──────────────────┬──────┬──────────────────────────┐
│ Rekordbox Library│ Sync │  Editor Library   │ Push │  Device Library          │
│ (playlist tree)  │  to  │  (AppData         │  to  │  (J:\.Pacemaker\         │
│                  │Editor│   music.db)       │Device│   music.db)              │
│ □ Folder         │  ↓   │ ☑ Case A (12 trk) │  →   │ Case A (12 tracks)       │
│ ├─ □ Playlist A  │      │ □ Case B (8 trk)  │      │ Case B (8 tracks)        │
│ └─ □ Playlist B  │      │                  │      │                          │
│                  │      │ [All][None]       │      │ [Refresh][Browse][⏏Eject]│
│                  │      │ [Refresh][Delete] │      │ [Delete]                 │
└──────────────────┴──────┴──────────────────┴──────┴──────────────────────────┘
```

- **Left (Rekordbox Library)**: Checkbox tree. Folders are tristate (checking a folder checks all children). Tree starts **collapsed**; expansion state is **persisted** via `QSettings`.
- **Connector strip 1**: "Sync to / Editor Library" button — triggers sync of checked playlists to Editor DB.
- **Editor Library**: Cases in `%APPDATA%\Tonium\Pacemaker\music.db`. Checkboxes to select which cases to push. "All/None" toggle. Double-click or Rename button to rename. Multi-select delete.
- **Connector strip 2**: "Push (N) to / Device →" button — copies checked cases + their audio files to device.
- **Device Library**: Cases on the connected Pacemaker. Browse button for manual path. Eject button. Multi-select delete.

File menu: **Import from M3U8…** | **Repair Device Database…** | Exit

---

## Database Details

### Rekordbox `master.db` (encrypted, read-only)

Accessed via **pyrekordbox 0.4.4** (`Rekordbox6Database`). SQLCipher-encrypted — pyrekordbox handles decryption after `python -m pyrekordbox setup` is run once.

Key pyrekordbox API (version 0.4.4 — important: API changed from ≤0.3.x):
```python
from pyrekordbox import Rekordbox6Database as MasterDatabase
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

**Known pyrekordbox 0.4.4 gotchas:**
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

**Editor DB** (`%APPDATA%\Tonium\Pacemaker\music.db`):
- Stores tracks with **PC file paths** (e.g. `C:\Users\opera\Music\track.mp3`)
- Managed by the Pacemaker desktop Editor app and by this tool
- The "working copy" — sync Rekordbox playlists here first

**Device DB** (`<drive>:\.Pacemaker\music.db`):
- Stores tracks with **device-local paths** (e.g. `J:\Music\track.mp3`)
- Push copies audio files to `<drive>\Music\` and writes device paths into this DB
- Never write PC paths into the device DB — causes tracks to fail to load

**Push process** (`DevicePushWorker`):
1. For each checked Editor case, reads tracks via `get_case_tracks_as_trackinfo()`
2. Copies each audio file to `<device_drive>\Music\` (skips if same filename+size exists)
3. Uses `dataclasses.replace(track, location=dest_path)` to create device-path TrackInfo
4. Calls `device.insert_or_get_track(device_track)` with the device path
5. Links track to case via `casetracks`

**Filename collision handling**: if `track.mp3` already exists but is a different file, appends `_NNNN` suffix derived from `hash(src_path)`.

---

## Case Naming (Folder-Aware)

Rekordbox has folders; Pacemaker has no folder concept. Case names are auto-generated from the full folder path:

```
["2025", "08 - Aug", "IYKYK", "Deep or Heavy"]
→  "2025_Aug_IYK_DeaporHeavy"
```

Rules (`_make_case_name` in `main_window.py`):
- Folder segments: strip leading ordinal prefix (`08 - ` → `Aug`), take first 3 chars
- Playlist name: strip ordinal prefix, remove spaces, take first 12 chars
- Join all with `_`
- Root-level playlists (no folders): kept as-is, truncated to 24 chars with `…`

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

**`DevicePushWorker(QObject)`** — Editor → Device push thread
- Signals: `progress(int, int, str)`, `finished(bool, str)`
- `__init__(editor_db, device_db, cases)`: `cases` is list of `{"case_id", "name", "track_count", ...}`
- `_copy_track(src_path, music_root) -> str` (static): copies file, skips if same size exists, handles collisions
- `run()`: copies files, writes device-path tracks to device DB, per-track progress

**`RepairDeviceDbDialog(QDialog)`** — File → Repair Device Database…
- Scans device DB for tracks with non-existent `location` paths
- Walks a chosen folder for audio files, builds `{lowercase_filename: full_path}` index
- Matches broken tracks by filename, shows count of fixable vs unmatched
- Applies fixes via `UPDATE tracks SET location = ? WHERE track_id = ?`
- Auto-populates device DB path and `<drive>\Music\` scan folder if device panel has a path

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
- `_eject_device()`: PowerShell `Shell.Application` COM eject, `CREATE_NO_WINDOW` flag
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

## Important: Python 3.9 Compatibility

The project runs on **Python 3.9**. All files include `from __future__ import annotations` as the first import after the module docstring. Without it, `X | Y`, `list[str]`, `dict[str, int]` etc. raise `TypeError` at runtime.

**Any new file must include this line.**

---

## Import Notes (pyrekordbox 0.4.4)

| Old (≤ 0.3.x) | New (0.4.4) |
|---|---|
| `from pyrekordbox import MasterDatabase` | `from pyrekordbox import Rekordbox6Database` |
| `from pyrekordbox.masterdb.models import DjmdPlaylist` | `from pyrekordbox.db6.tables import DjmdPlaylist` |

`rekordbox_reader.py` uses alias: `from pyrekordbox import Rekordbox6Database as MasterDatabase`

---

## Setup & Running

```bash
# Install dependencies (run as Administrator if on global Python)
pip install -r requirements.txt

# One-time pyrekordbox setup (decryption key for Rekordbox master.db)
python -m pyrekordbox setup

# Run
python main.py
```

Pacemaker database paths (auto-detected on startup):
- **Editor**: `C:\Users\<username>\AppData\Roaming\Tonium\Pacemaker\music.db`
- **Device**: `<drive>:\.Pacemaker\music.db` (e.g. `J:\.Pacemaker\music.db`)

---

## Dependencies

```
PyQt6>=6.4.0           # GUI
pyrekordbox>=0.3.0     # Rekordbox DB reader (installed: 0.4.4)
sqlcipher3-wheels      # SQLCipher encryption support for Rekordbox DB
mutagen>=1.47.0        # Audio metadata (used by M3U8 import)
sqlalchemy>=2.0.0      # ORM used internally by pyrekordbox
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

## Known Minor Gaps / Future Work

- `disc_number` / `number_of_discs` in `m3u8_reader.py` both read from `TPOS` — should split on `/` (low priority)
- No batch transactions in `pacemaker_writer.py` — each track insert commits individually (fine for typical sizes)
- No test suite
- UI modernisation (dark theme, flat buttons, panel headers) — discussed but not yet implemented
- Repair tool matches only by filename; a secondary match by title+artist+duration could rescue renamed files
