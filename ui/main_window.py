"""
Main application window.

Layout:
  Left        — PlaylistTreeWidget (Rekordbox library)
  Middle      — SyncPanel (Editor DB path, sync queue, progress, sync button)
  Right-top   — PacemakerLibraryPanel: Editor library  (AppData music.db)
  Right-bottom— PacemakerLibraryPanel: Device library  (device .Pacemaker/music.db)

Menu:
  File → Import from M3U8…
  File → Exit
"""

from __future__ import annotations
import dataclasses
import json
import os
import re
import shutil
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QProgressDialog,
    QApplication, QFrame, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject, QUrl, QEvent
from PyQt6.QtGui import QAction, QPainter, QColor, QBrush

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _PREVIEW_OK = True
except ImportError:
    _PREVIEW_OK = False

from core.rekordbox_reader import RekordboxReader, PlaylistNode, TrackInfo
from core import logger as _logger

_WAVE_COL_ROLE = Qt.ItemDataRole.UserRole + 3   # stores list[tuple[int,int]] waveform data

# Color palette matching player_bar.py
_WDELEGATE_COLORS = [
    QColor("#555555"),  # 0 silent
    QColor("#ff88aa"),  # 1 pink
    QColor("#5599ff"),  # 2 blue
    QColor("#88ccff"),  # 3 bright blue
    QColor("#44ddcc"),  # 4 cyan
    QColor("#e8631a"),  # 5 orange
    QColor("#ffdd44"),  # 6 yellow
    QColor("#44cc44"),  # 7 green
]


class _WaveformDelegate(QStyledItemDelegate):
    """Draws a mini waveform preview inside a table cell.
    When preview_row matches the row being painted, draws a stop-button overlay.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preview_row: int = -1   # row currently being previewed (-1 = none)

    def paint(self, painter: QPainter, option, index) -> None:
        data = index.data(_WAVE_COL_ROLE)
        is_preview = (index.row() == self.preview_row)

        if not data:
            painter.fillRect(option.rect, QColor("#181818"))
            if is_preview:
                self._draw_stop_icon(painter, option.rect)
            else:
                painter.setPen(QColor("#333333"))
                mid_y = option.rect.center().y()
                painter.drawLine(option.rect.left() + 2, mid_y,
                                 option.rect.right() - 2, mid_y)
            return

        r = option.rect
        painter.save()
        painter.setClipRect(r)
        painter.fillRect(r, QColor("#181818"))
        n = len(data)
        w = max(r.width(), 1)
        h = r.height()
        mid = r.top() + h / 2
        max_h = h * 0.85 / 2
        painter.setPen(Qt.PenStyle.NoPen)

        # Downsample or upsample: map n data points into w pixel columns.
        # Each pixel column takes the max height and dominant color of its bucket.
        for px in range(w):
            lo = int(px * n / w)
            hi = int((px + 1) * n / w)
            if hi <= lo:
                hi = lo + 1
            bucket = data[lo:hi]
            peak_h, peak_c = max(bucket, key=lambda v: v[0])
            bar_h = max((peak_h / 31.0) * max_h, 1.0)
            x = r.left() + px
            col = _WDELEGATE_COLORS[peak_c % len(_WDELEGATE_COLORS)]
            painter.setBrush(QBrush(col))
            painter.drawRect(x, int(mid - bar_h), 1, max(int(bar_h * 2), 1))

        if is_preview:
            self._draw_stop_icon(painter, r)
        painter.restore()

    @staticmethod
    def _draw_stop_icon(painter: QPainter, rect) -> None:
        """Light dim overlay + white stop square pinned to the left edge."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 80)))
        painter.drawRect(rect)
        # Stop square: left-aligned, vertically centred
        painter.setBrush(QBrush(QColor("#ffffff")))
        sq = 7
        cx = rect.left() + 11   # 11 px from left edge
        cy = rect.center().y()
        painter.drawRect(cx - sq // 2, cy - sq // 2, sq, sq)

    def sizeHint(self, option, index):
        return option.rect.size()


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric UserRole+1 value when set."""
    _SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __lt__(self, other: "QTableWidgetItem") -> bool:
        a = self.data(self._SORT_ROLE)
        b = other.data(self._SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)
from core.pacemaker_writer import PacemakerWriter
from core import sync_state
from core.device_finder import find_editor_db, find_device_db
from core.m3u8_reader import load_m3u8_tracks
from ui.playlist_tree import PlaylistTreeWidget
from ui.sync_panel import SyncPanel, SyncQueueItem
from ui.pacemaker_panel import PacemakerLibraryPanel
from ui.player_bar import PlayerBar


# ---------------------------------------------------------------------------
# Per-device sync map  (<drive>:\.Pacemaker\rb_sync_map.json)
# Maps str(editor_case_id) → device_case_id so each device independently
# tracks which Editor cases have been pushed to it.
# ---------------------------------------------------------------------------

def _sync_map_path(device_db: str) -> str:
    drive = os.path.splitdrive(device_db)[0]
    return os.path.join(drive, os.sep, ".Pacemaker", "rb_sync_map.json")


def _read_sync_map(device_db: str) -> dict:
    """Returns {str(editor_case_id): device_case_id}. Empty dict on any error."""
    try:
        with open(_sync_map_path(device_db), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_sync_map(device_db: str, sync_map: dict) -> None:
    try:
        with open(_sync_map_path(device_db), "w", encoding="utf-8") as f:
            json.dump(sync_map, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Background worker: batch waveform fetch for track table
# ---------------------------------------------------------------------------

class _WaveformBatchWorker(QObject):
    row_done = pyqtSignal(int, int, object)  # (generation, row_index, data or None)
    finished = pyqtSignal()

    def __init__(self, generation: int, locations: list[str]):
        super().__init__()
        self._generation = generation
        self._locations = locations
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self):
        # Open a fresh RekordboxReader in this thread — SQLAlchemy sessions are
        # not thread-safe so we must never share the main thread's reader here.
        from core.rekordbox_reader import RekordboxReader
        from core.logger import log as _log
        reader = None
        try:
            reader = RekordboxReader()
        except Exception as e:
            _log.error("WaveformBatch: could not open RekordboxReader: %s", e, exc_info=True)

        for i, loc in enumerate(self._locations):
            if self._cancelled:
                break
            data = None
            if reader:
                data = reader.get_waveform_data(loc)
                if data is None:
                    _log.debug("WaveformBatch: no waveform data for %s", loc)
            self.row_done.emit(self._generation, i, data)

        if reader:
            try:
                reader.close()
            except Exception:
                pass
        self.finished.emit()


# ---------------------------------------------------------------------------
# Background worker: Rekordbox → Editor sync
# ---------------------------------------------------------------------------

class SyncWorker(QObject):
    progress = pyqtSignal(int, int, str)   # current, total, status message
    finished = pyqtSignal(bool, str)        # success, message

    def __init__(self, db_path: str, operations: list[dict]):
        super().__init__()
        self._db_path = db_path
        self._operations = operations

    def run(self):
        try:
            total_tracks = sum(len(op["tracks"]) for op in self._operations)
            done = 0

            with PacemakerWriter(self._db_path) as writer:
                for op in self._operations:
                    name = op["playlist_name"]
                    tracks = op["tracks"]
                    self.progress.emit(done, total_tracks, f"Adding: {name}")

                    def cb(i, n, _done=done, _total=total_tracks, _name=name):
                        self.progress.emit(_done + i, _total, f"Adding: {_name} ({i}/{n})")

                    writer.add_playlist(name, tracks, cb)
                    done += len(tracks)

            self.finished.emit(True, "Sync complete.")
        except Exception as e:
            self.finished.emit(False, str(e))


# ---------------------------------------------------------------------------
# Background worker: Editor → Device sync (add new + remove unchecked)
# ---------------------------------------------------------------------------

class DeviceSyncWorker(QObject):
    progress = pyqtSignal(int, int, str)   # current, total, status message
    finished = pyqtSignal(bool, str)

    def __init__(self, editor_db: str, device_db: str,
                 to_add: list, to_remove: list, sync_map: dict):
        super().__init__()
        self._editor_db = editor_db
        self._device_db = device_db
        self._to_add = to_add        # [{"case_id", "name", "track_count"}]
        self._to_remove = to_remove  # [{"editor_case_id", "device_case_id", "name"}]
        self._sync_map = dict(sync_map)  # working copy; written to device on completion

    @staticmethod
    def _delete_device_files(locations: list[str], device_db: str) -> None:
        r"""
        Delete audio files (and their .str companions) for the given pmdb locations.
        /pmdb_tracks/a/b/xxxxxxxx.mp3  ->  <drive>:\.Pacemaker\Music\a\b\xxxxxxxx.mp3
        """
        device_drive = os.path.splitdrive(device_db)[0]
        for loc in locations:
            if loc.startswith("/pmdb_tracks/"):
                rel = loc[len("/pmdb_tracks/"):].replace("/", os.sep)
                win_path = os.path.join(
                    device_drive, os.sep, ".Pacemaker", "Music", rel
                )
            else:
                win_path = loc  # old-format path stored as Windows path
            try:
                if os.path.exists(win_path):
                    os.remove(win_path)
                str_path = os.path.splitext(win_path)[0] + ".str"
                if os.path.exists(str_path):
                    os.remove(str_path)
            except Exception:
                pass

    @staticmethod
    def _pmdb_location(src_path: str) -> str:
        """
        Derive the device-side DB location string for a source file.
        Format: /pmdb_tracks/X/Y/HHHHHHHH.ext
        where HHHHHHHH = CRC32 of the source path (8 hex chars),
        X = first hex char, Y = second hex char.
        This matches the folder layout the Pacemaker firmware expects.
        """
        import zlib
        crc = f"{zlib.crc32(src_path.encode()) & 0xFFFFFFFF:08x}"
        ext = os.path.splitext(src_path)[1].lower()
        return f"/pmdb_tracks/{crc[0]}/{crc[1]}/{crc}{ext}"

    @staticmethod
    def _copy_track(src_path: str, device_db: str) -> tuple[str, str]:
        """
        Copy src_path to the correct location under J:\\.Pacemaker\\Music\\X\\Y\\
        and create the companion 0-byte .str file the firmware expects.
        Returns (windows_dest_path, pmdb_location_string).
        """
        import zlib
        device_drive = os.path.splitdrive(device_db)[0]   # e.g. "J:"
        crc = f"{zlib.crc32(src_path.encode()) & 0xFFFFFFFF:08x}"
        ext = os.path.splitext(src_path)[1].lower()
        dir1, dir2 = crc[0], crc[1]

        dest_dir = os.path.join(device_drive, os.sep, ".Pacemaker", "Music", dir1, dir2)
        os.makedirs(dest_dir, exist_ok=True)

        dest = os.path.join(dest_dir, f"{crc}{ext}")
        pmdb_loc = f"/pmdb_tracks/{dir1}/{dir2}/{crc}{ext}"

        # Skip copy if already there and same size
        if not (os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(src_path)):
            shutil.copy2(src_path, dest)

        # Create companion 0-byte .str file (firmware requires this)
        str_path = os.path.splitext(dest)[0] + ".str"
        if not os.path.exists(str_path):
            open(str_path, "wb").close()

        return dest, pmdb_loc

    def run(self):
        try:
            from core.logger import log as _log
            removed = 0
            done = 0
            skipped = 0

            # ── Phase 1: Remove unchecked cases from device ──────────────
            if self._to_remove:
                _log.info("Device sync: removing %d case(s)", len(self._to_remove))
                deleted_locs: list[str] = []
                with PacemakerWriter(self._device_db) as device:
                    for r in self._to_remove:
                        _log.info("  Removing case: %s (device id %s)", r['name'], r['device_case_id'])
                        self.progress.emit(0, 1, f"Removing: {r['name']}")
                        locs = device.get_case_track_locations(r["device_case_id"])
                        actually_deleted = device.remove_playlist(
                            r["device_case_id"], list(locs)
                        )
                        _log.info("  Deleted %d track record(s) from DB", len(actually_deleted))
                        deleted_locs.extend(actually_deleted)
                        self._sync_map.pop(str(r["editor_case_id"]), None)
                        removed += 1

                _log.info("Device sync: deleting %d file(s) from device storage", len(deleted_locs))
                self._delete_device_files(deleted_locs, self._device_db)

            # ── Phase 2: Push new checked cases to device ─────────────────
            total_tracks = sum(c.get("track_count", 0) for c in self._to_add)

            if self._to_add:
                _log.info("Device sync: pushing %d case(s), %d tracks", len(self._to_add), total_tracks)
                with PacemakerWriter(self._editor_db) as editor:
                    all_tracks = {
                        c["case_id"]: editor.get_case_tracks_as_trackinfo(c["case_id"])
                        for c in self._to_add
                    }

                with PacemakerWriter(self._device_db) as device:
                    for case in self._to_add:
                        tracks = all_tracks[case["case_id"]]
                        device_case_id = device.create_case(case["name"])
                        self._sync_map[str(case["case_id"])] = device_case_id

                        for track in tracks:
                            self.progress.emit(
                                done, max(total_tracks, 1),
                                f"Copying: {os.path.basename(track.location)}"
                            )
                            if not os.path.exists(track.location):
                                _log.warning("  Track file not found, skipping: %s", track.location)
                                skipped += 1
                                done += 1
                                continue
                            try:
                                _win_dest, pmdb_loc = self._copy_track(
                                    track.location, self._device_db
                                )
                                _log.debug("  Copied: %s -> %s", track.location, pmdb_loc)
                            except Exception as _copy_err:
                                _log.error("  Copy failed for %s: %s", track.location, _copy_err, exc_info=True)
                                skipped += 1
                                done += 1
                                continue
                            device_track = dataclasses.replace(track, location=pmdb_loc)
                            tid = device.insert_or_get_track(device_track)
                            device.link_track_to_case(device_case_id, tid)
                            done += 1

            # ── Phase 3: Persist sync map to device ───────────────────────
            _write_sync_map(self._device_db, self._sync_map)

            parts = []
            if self._to_add:
                parts.append(
                    f"Added {len(self._to_add)} case(s) "
                    f"({done - skipped} tracks copied)"
                )
            if removed:
                parts.append(f"Removed {removed} case(s) from device")
            if skipped:
                parts.append(f"{skipped} track(s) skipped (file not found)")
            msg = ".  ".join(parts) + "." if parts else "Device already in sync."
            _log.info("Device sync complete: %s", msg)
            self.finished.emit(True, msg)

        except Exception as e:
            _log.error("Device sync error: %s", e, exc_info=True)
            self.finished.emit(False, str(e))


# ---------------------------------------------------------------------------
# Repair Device Database dialog
# ---------------------------------------------------------------------------

class RepairDeviceDbDialog(QDialog):
    """
    Scans the device music.db for tracks whose location no longer exists,
    matches them by filename against audio files found in a chosen folder,
    and rewrites the location column with the correct device path.
    """

    _AUDIO_EXTS = {".mp3", ".flac", ".aac", ".m4a", ".mp4", ".wav", ".aiff", ".ogg"}

    def __init__(self, device_db: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Repair Device Database")
        self.setMinimumWidth(620)
        self._device_db = device_db
        self._scan_root = ""
        self._fixes: list[dict] = []
        self._build_ui()

        if device_db:
            self._db_edit.setText(device_db)
            drive = os.path.splitdrive(device_db)[0]
            # Music lives at J:\.Pacemaker\Music\ — default scan root there
            music_root = os.path.join(drive, os.sep, ".Pacemaker", "Music")
            if os.path.isdir(music_root):
                self._scan_root = music_root
                self._scan_edit.setText(music_root)
            else:
                # Fallback to device root
                device_root = drive + os.sep
                if os.path.isdir(device_root):
                    self._scan_root = device_root
                    self._scan_edit.setText(device_root)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(
            "Scans the device database for tracks with broken file paths and\n"
            "attempts to fix them by matching audio files on the device by filename."
        ))

        db_row = QHBoxLayout()
        db_row.addWidget(QLabel("Device music.db:"))
        self._db_edit = QLineEdit()
        self._db_edit.setReadOnly(True)
        self._db_edit.setPlaceholderText("Select device music.db…")
        db_browse = QPushButton("Browse…")
        db_browse.clicked.connect(self._browse_db)
        db_row.addWidget(self._db_edit, stretch=1)
        db_row.addWidget(db_browse)
        layout.addLayout(db_row)

        scan_row = QHBoxLayout()
        scan_row.addWidget(QLabel("Music folder to scan:"))
        self._scan_edit = QLineEdit()
        self._scan_edit.setReadOnly(True)
        self._scan_edit.setPlaceholderText("Folder containing audio files on the device…")
        scan_browse = QPushButton("Browse…")
        scan_browse.clicked.connect(self._browse_scan)
        scan_row.addWidget(self._scan_edit, stretch=1)
        scan_row.addWidget(scan_browse)
        layout.addLayout(scan_row)

        self._scan_btn = QPushButton("Scan for Broken Tracks")
        self._scan_btn.clicked.connect(self._do_scan)
        layout.addWidget(self._scan_btn)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self._result_label = QLabel("Click Scan to begin.")
        self._result_label.setWordWrap(True)
        layout.addWidget(self._result_label)

        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("Apply Fixes")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._do_apply)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        btn_row.addWidget(self._apply_btn)
        layout.addLayout(btn_row)

    def _browse_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Device music.db", "", "SQLite Database (*.db)"
        )
        if not path:
            return
        self._device_db = path
        self._db_edit.setText(path)
        drive = os.path.splitdrive(path)[0]
        music_root = os.path.join(drive, os.sep, ".Pacemaker", "Music")
        if os.path.isdir(music_root) and not self._scan_edit.text():
            self._scan_root = music_root
            self._scan_edit.setText(music_root)

    def _browse_scan(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Music Folder on Device")
        if folder:
            self._scan_root = folder
            self._scan_edit.setText(folder)

    def _do_scan(self):
        if not self._device_db:
            QMessageBox.warning(self, "Missing", "Please select the device music.db first.")
            return
        if not self._scan_root:
            QMessageBox.warning(self, "Missing", "Please select a folder to scan for music files.")
            return

        self._result_label.setText("Scanning…")
        QApplication.processEvents()

        # The device drive letter (e.g. "J:"). A stored path is only valid if
        # it starts with this drive — anything else is a PC path and is broken
        # even if the file happens to exist on the PC.
        device_drive = os.path.splitdrive(self._device_db)[0].upper()  # e.g. "J:"

        # Index every audio file on the device by lowercase filename
        file_index: dict[str, str] = {}
        for dirpath, _, filenames in os.walk(self._scan_root):
            for fname in filenames:
                if os.path.splitext(fname)[1].lower() in self._AUDIO_EXTS:
                    key = fname.lower()
                    if key not in file_index:
                        file_index[key] = os.path.join(dirpath, fname)

        # Read all track locations from the device DB
        try:
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(self._device_db)
            conn.row_factory = _sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT track_id, location FROM tracks")
            rows = cur.fetchall()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self, "Database Error", f"Could not read database:\n{e}")
            self._result_label.setText("Scan failed.")
            return

        device_drive = os.path.splitdrive(self._device_db)[0].upper()  # e.g. "J:"

        def pmdb_to_windows(loc: str) -> str:
            """Convert /pmdb_tracks/X/Y/hash.mp3 → J:\\.Pacemaker\\Music\\X\\Y\\hash.mp3"""
            rel = loc.replace("/pmdb_tracks/", "").replace("/", os.sep)
            return os.path.join(device_drive, os.sep, ".Pacemaker", "Music", rel)

        def is_valid_device_path(loc: str) -> bool:
            """A location is valid if it's a pmdb path and the file exists on the device."""
            if not loc:
                return False
            if loc.startswith("/pmdb_tracks/"):
                return os.path.exists(pmdb_to_windows(loc))
            # Windows path on the device drive — legacy check
            return os.path.splitdrive(loc)[0].upper() == device_drive and os.path.exists(loc)

        self._fixes = []
        unmatched = []

        for row in rows:
            loc = row["location"] or ""
            if is_valid_device_path(loc):
                continue  # path resolves to a real file on the device — skip

            basename = os.path.basename(loc).lower() if loc else ""
            if basename and basename in file_index:
                # Convert the Windows path we found back to a pmdb location string
                win_path = file_index[basename]
                # Try to express as /pmdb_tracks/... if it's inside .Pacemaker\Music
                pacemaker_music = os.path.join(device_drive, os.sep, ".Pacemaker", "Music")
                if win_path.startswith(pacemaker_music):
                    rel = win_path[len(pacemaker_music):].lstrip(os.sep).replace(os.sep, "/")
                    new_loc = f"/pmdb_tracks/{rel}"
                else:
                    new_loc = win_path  # fallback: store as-is
                self._fixes.append({
                    "track_id": row["track_id"],
                    "old": loc,
                    "new": new_loc,
                })
            elif loc:
                unmatched.append(loc)

        total_broken = len(self._fixes) + len(unmatched)
        if total_broken == 0:
            self._result_label.setText(
                f"Scanned {len(rows)} track(s) — no broken paths found. Database looks healthy."
            )
            self._apply_btn.setEnabled(False)
            return

        lines = [f"Scanned {len(rows)} track(s). Found {total_broken} with broken paths:"]
        lines.append(f"  \u2022 {len(self._fixes)} can be auto-fixed (filename matched on device)")
        if unmatched:
            lines.append(
                f"  \u2022 {len(unmatched)} could not be matched "
                f"(no audio file with that name found in the scan folder)"
            )
        # Show a sample of what will be fixed so the user can verify the paths look right
        if self._fixes:
            sample = self._fixes[0]
            lines.append(f"\nExample fix:")
            lines.append(f"  From: {sample['old']}")
            lines.append(f"  To:   {sample['new']}")
        self._result_label.setText("\n".join(lines))
        self._apply_btn.setEnabled(bool(self._fixes))

    def _do_apply(self):
        if not self._fixes:
            return
        try:
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(self._device_db)
            cur = conn.cursor()
            for fix in self._fixes:
                cur.execute(
                    "UPDATE tracks SET location = ? WHERE track_id = ?",
                    (fix["new"], fix["track_id"])
                )
            conn.commit()
            conn.close()

            QMessageBox.information(
                self, "Repair Complete",
                f"Fixed {len(self._fixes)} track location(s).\n\n"
                "Safely eject and reconnect the device for changes to take effect."
            )
            self._fixes = []
            self._apply_btn.setEnabled(False)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to apply fixes:\n{e}")


# ---------------------------------------------------------------------------
# M3U8 Import dialog
# ---------------------------------------------------------------------------

class M3U8ImportDialog(QDialog):
    def __init__(self, db_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import from M3U8")
        self.setMinimumWidth(500)
        self._db_path = db_path
        self._m3u8_path = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Select an M3U8 playlist file to import:"))

        file_row = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setReadOnly(True)
        self._file_edit.setPlaceholderText("No file selected…")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        file_row.addWidget(self._file_edit)
        file_row.addWidget(browse)
        layout.addLayout(file_row)

        layout.addWidget(QLabel("Case name (leave blank to use filename):"))
        self._name_edit = QLineEdit()
        layout.addWidget(self._name_edit)

        btn_row = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self._import_btn = QPushButton("Import")
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        btn_row.addStretch()
        btn_row.addWidget(cancel)
        btn_row.addWidget(self._import_btn)
        layout.addLayout(btn_row)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select M3U8 file", "", "M3U8 Playlist (*.m3u8 *.m3u)"
        )
        if path:
            self._m3u8_path = path
            self._file_edit.setText(path)
            if not self._name_edit.text():
                self._name_edit.setText(os.path.splitext(os.path.basename(path))[0])
            self._import_btn.setEnabled(bool(self._db_path))

    def _do_import(self):
        if not self._db_path:
            QMessageBox.warning(self, "No Database", "Please select a Pacemaker database first.")
            return

        case_name = self._name_edit.text().strip() or os.path.splitext(
            os.path.basename(self._m3u8_path)
        )[0]

        progress = QProgressDialog("Reading tracks…", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        QApplication.processEvents()

        tracks, errors = load_m3u8_tracks(self._m3u8_path)
        progress.close()

        if not tracks:
            QMessageBox.warning(self, "No Tracks", "No valid tracks found in the M3U8 file.")
            return

        msg = f"Found {len(tracks)} track(s)."
        if errors:
            msg += f"\n{len(errors)} file(s) could not be read and will be skipped."
        msg += f"\n\nImport as case: \"{case_name}\"?"

        reply = QMessageBox.question(self, "Confirm Import", msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with PacemakerWriter(self._db_path) as writer:
                case_id, locations = writer.add_playlist(case_name, tracks)
            QMessageBox.information(
                self, "Done",
                f"Imported {len(locations)} tracks as \"{case_name}\"."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rekordbox → Pacemaker Sync")
        self.setMinimumSize(1200, 600)
        self.resize(1600, 750)

        self._rb_reader: RekordboxReader | None = None
        self._playlist_nodes: list[PlaylistNode] = []
        self._sync_thread: QThread | None = None
        self._sync_progress_dlg: QProgressDialog | None = None
        self._push_thread: QThread | None = None
        self._push_progress_dlg: QProgressDialog | None = None

        # Inline waveform preview player (separate from the main PlayerBar)
        self._preview_row: int = -1
        self._preview_seek_fraction: float = 0.0   # seek target when media loads
        if _PREVIEW_OK:
            self._preview_player = QMediaPlayer()
            self._preview_audio_out = QAudioOutput()
            self._preview_player.setAudioOutput(self._preview_audio_out)
            self._preview_audio_out.setVolume(0.7)
            self._preview_player.mediaStatusChanged.connect(
                self._on_preview_media_status
            )
        else:
            self._preview_player = None
            self._preview_audio_out = None

        self._build_menu()
        self._build_ui()
        self._load_rekordbox()
        self._autodetect_pacemaker_dbs()

        # Poll for Pacemaker device every 2 s; stops once a device is loaded.
        self._device_poll_timer = QTimer(self)
        self._device_poll_timer.setInterval(2000)
        self._device_poll_timer.timeout.connect(self._poll_for_device)
        self._device_poll_timer.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("File")

        import_action = QAction("Import from M3U8…", self)
        import_action.triggered.connect(self._open_m3u8_import)
        file_menu.addAction(import_action)

        repair_action = QAction("Repair Device Database…", self)
        repair_action.triggered.connect(self._open_repair_dialog)
        file_menu.addAction(repair_action)

        cleanup_action = QAction("Clean Up Device Files…", self)
        cleanup_action.triggered.connect(self._cleanup_orphaned_device_files)
        file_menu.addAction(cleanup_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        debug_menu = menu.addMenu("Debug")
        self._debug_toggle = QAction("Enable Debug Logging", self)
        self._debug_toggle.setCheckable(True)
        self._debug_toggle.triggered.connect(self._toggle_debug_logging)
        debug_menu.addAction(self._debug_toggle)

        self._open_log_action = QAction("Open Log File…", self)
        self._open_log_action.setEnabled(False)
        self._open_log_action.triggered.connect(self._open_log_file)
        debug_menu.addAction(self._open_log_action)

    def _build_ui(self):
        # ── Hidden state manager — not added to any layout ────────────────
        self._panel = SyncPanel()
        self._panel.db_path_changed.connect(self._on_editor_db_changed)

        # ── Rekordbox playlist tree ───────────────────────────────────────
        self._tree = PlaylistTreeWidget()
        self._tree.selection_changed.connect(self._on_selection_changed)

        # ── Editor library panel ──────────────────────────────────────────
        self._editor_panel = PacemakerLibraryPanel(
            title="EDITOR LIBRARY",
            show_browse=False,
            show_push_button=True,
            show_rename=True,
            show_checkboxes=True,
            show_tracklist=False,
            show_selection_size=True,
            show_tabs=True,
        )
        self._editor_panel.refresh_requested.connect(self._load_editor_library)
        self._editor_panel.delete_requested.connect(self._on_delete_editor_case)
        self._editor_panel.rename_requested.connect(self._on_rename_editor_case)
        self._editor_panel.inline_rename_committed.connect(self._on_inline_rename_editor_case)
        self._editor_panel.push_requested.connect(self._confirm_and_sync_to_device)
        self._editor_panel.case_selected.connect(self._on_editor_case_selected)
        self._editor_panel.track_tab_play_requested.connect(self._on_track_tab_play)
        self._editor_panel.track_tab_selected.connect(self._on_track_tab_selected)

        # ── Device library panel ──────────────────────────────────────────
        self._device_panel = PacemakerLibraryPanel(
            title="DEVICE",
            show_browse=True,
            show_push_button=False,
            show_eject=True,
            show_tracklist=False,
            show_storage=True,
            show_tabs=True,
        )
        self._device_panel.refresh_requested.connect(self._load_device_library)
        self._device_panel.delete_requested.connect(self._on_delete_device_case)
        self._device_panel.db_path_changed.connect(self._on_device_db_changed)
        self._device_panel.eject_requested.connect(self._eject_device)
        self._device_panel.case_selected.connect(self._on_device_case_selected)

        # ── Player bar (top, like Rekordbox) ──────────────────────────────
        self._player_bar = PlayerBar()
        self._player_bar.track_changed.connect(self._on_player_track_changed)
        self._player_bar.track_changed.connect(lambda _: self._stop_preview())

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = self._build_toolbar()

        # ── Rekordbox tree column ─────────────────────────────────────────
        from ui.style import ACCENT, BORDER
        _accent_ss_rb = (
            f"QPushButton {{ background-color: {ACCENT}; color: #ffffff; "
            f"font-weight: bold; border: none; border-radius: 0; padding: 6px; }}"
            f"QPushButton:hover {{ background-color: #ff7a30; }}"
            f"QPushButton:disabled {{ background-color: #5a3010; color: #666666; }}"
        )
        import_btn = self._panel.sync_button
        import_btn.setText("↓  Import to\nEditor Library")
        import_btn.setFixedHeight(40)
        import_btn.setStyleSheet(_accent_ss_rb)
        import_btn.clicked.connect(self._confirm_and_sync)

        rb_col = QWidget()
        rb_layout = QVBoxLayout(rb_col)
        rb_layout.setContentsMargins(0, 0, 0, 0)
        rb_layout.setSpacing(0)
        rb_layout.addWidget(self._make_section_header("REKORDBOX LIBRARY"))
        rb_layout.addWidget(self._tree, stretch=1)
        rb_layout.addWidget(import_btn)

        # ── Central track area ────────────────────────────────────────────
        main_area = self._build_main_area()

        # ── Sync connector strip (between track table and device panel) ───
        sync_connector = self._make_sync_connector()

        # ── 5-column content splitter ─────────────────────────────────────
        # RB tree | Editor Library | Track table | Sync connector | Device
        content = QSplitter(Qt.Orientation.Horizontal)
        content.addWidget(rb_col)
        content.addWidget(self._editor_panel)
        content.addWidget(main_area)
        content.addWidget(sync_connector)
        content.addWidget(self._device_panel)
        content.setSizes([220, 210, 900, 48, 220])
        content.setStretchFactor(0, 0)
        content.setStretchFactor(1, 0)
        content.setStretchFactor(2, 1)
        content.setStretchFactor(3, 0)
        content.setStretchFactor(4, 0)
        content.setCollapsible(3, False)  # connector strip — not collapsible

        # ── Root ──────────────────────────────────────────────────────────
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setSpacing(0)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(toolbar)
        root_layout.addWidget(self._player_bar)   # player at TOP like Rekordbox
        root_layout.addWidget(content, stretch=1)
        self.setCentralWidget(root)

    # ── Layout builders ───────────────────────────────────────────────────

    def _build_toolbar(self) -> QWidget:
        from ui.style import ACCENT, BG_MAIN, BORDER, FG_SECONDARY
        _accent_ss = (
            f"QPushButton {{ background-color: {ACCENT}; color: #ffffff; "
            f"font-weight: bold; border: none; border-radius: 3px; padding: 4px 14px; }}"
            f"QPushButton:hover {{ background-color: #ff7a30; }}"
            f"QPushButton:disabled {{ background-color: #5a3010; color: #666666; }}"
        )

        bar = QWidget()
        bar.setFixedHeight(46)
        bar.setStyleSheet(
            f"QWidget {{ background-color: {BG_MAIN}; border-bottom: 1px solid {BORDER}; }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        brand = QLabel("RB → PM")
        brand.setStyleSheet(f"color: {ACCENT}; font-weight: bold; font-size: 14px; border: none;")
        layout.addWidget(brand)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"color: {BORDER}; border: none;")
        layout.addWidget(sep)

        # Import button lives at the bottom of the RB tree panel, not the toolbar

        layout.addStretch()

        self._editor_path_lbl = QLabel("Editor: not loaded")
        self._editor_path_lbl.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 10px; border: none;")
        layout.addWidget(self._editor_path_lbl)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"color: {BORDER}; border: none;")
        layout.addWidget(sep2)

        self._device_path_lbl = QLabel("Device: not connected")
        self._device_path_lbl.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 10px; border: none;")
        layout.addWidget(self._device_path_lbl)

        return bar

    def _make_sync_connector(self) -> QWidget:
        """Narrow vertical strip housing the Sync button, between track table and device panel."""
        from ui.style import ACCENT, BG_MAIN, BORDER
        _accent_ss = (
            f"QPushButton {{ background-color: {ACCENT}; color: #ffffff; "
            f"font-weight: bold; border: none; border-radius: 3px; "
            f"padding: 6px 4px; font-size: 11px; }}"
            f"QPushButton:hover {{ background-color: #ff7a30; }}"
            f"QPushButton:disabled {{ background-color: #5a3010; color: #666666; }}"
        )

        strip = QWidget()
        strip.setFixedWidth(48)
        strip.setStyleSheet(
            f"QWidget {{ background-color: {BG_MAIN}; "
            f"border-left: 1px solid {BORDER}; border-right: 1px solid {BORDER}; }}"
        )
        layout = QVBoxLayout(strip)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)

        push_btn = self._editor_panel.push_button
        push_btn.setText("Sync\n\u25b6")
        push_btn.setFixedWidth(40)
        push_btn.setFixedHeight(60)
        push_btn.setEnabled(False)   # enabled when device connects
        push_btn.setStyleSheet(_accent_ss)

        layout.addStretch()
        layout.addWidget(push_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        return strip

    def _build_main_area(self) -> QWidget:
        from ui.style import BG_PANEL, BORDER, FG_SECONDARY

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Track view header bar
        hdr = QWidget()
        hdr.setFixedHeight(30)
        hdr.setStyleSheet(
            f"background-color: {BG_PANEL}; border-bottom: 1px solid {BORDER};"
        )
        hdr_layout = QHBoxLayout(hdr)
        hdr_layout.setContentsMargins(12, 0, 12, 0)
        self._track_view_lbl = QLabel("Select a playlist or case to view tracks")
        self._track_view_lbl.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 11px;")
        hdr_layout.addWidget(self._track_view_lbl)
        hdr_layout.addStretch()
        layout.addWidget(hdr)

        # Central track table  cols: #  ★  ~wave  Title  Artist  BPM  Key  Time  Genre
        self._track_table = QTableWidget(0, 9)
        self._track_table.setHorizontalHeaderLabels(
            ["#", "★", "~", "Title", "Artist", "BPM", "Key", "Time", "Genre"]
        )
        hh = self._track_table.horizontalHeader()
        for col, mode in enumerate([
            QHeaderView.ResizeMode.Fixed,    # #
            QHeaderView.ResizeMode.Fixed,    # ★
            QHeaderView.ResizeMode.Fixed,    # ~ waveform
            QHeaderView.ResizeMode.Stretch,  # Title
            QHeaderView.ResizeMode.Stretch,  # Artist
            QHeaderView.ResizeMode.Fixed,    # BPM
            QHeaderView.ResizeMode.Fixed,    # Key
            QHeaderView.ResizeMode.Fixed,    # Time
            QHeaderView.ResizeMode.Fixed,    # Genre
        ]):
            hh.setSectionResizeMode(col, mode)
        self._track_table.setColumnWidth(0, 36)
        self._track_table.setColumnWidth(1, 26)
        self._track_table.setColumnWidth(2, 160)   # waveform preview
        self._track_table.setColumnWidth(5, 52)
        self._track_table.setColumnWidth(6, 58)
        self._track_table.setColumnWidth(7, 56)
        self._track_table.setColumnWidth(8, 100)
        self._wave_delegate = _WaveformDelegate(self._track_table)
        self._track_table.setItemDelegateForColumn(2, self._wave_delegate)

        self._track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._track_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._track_table.verticalHeader().setVisible(False)
        self._track_table.setAlternatingRowColors(True)
        self._track_table.setSortingEnabled(True)
        self._track_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._track_table.verticalHeader().setDefaultSectionSize(22)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)   # lock waveform col
        self._track_table.itemDoubleClicked.connect(self._on_track_double_clicked)
        self._track_table.viewport().installEventFilter(self)

        # Store tracks and source for playback
        self._track_table_tracks: list[TrackInfo] = []
        self._track_table_source: str = ""   # "editor" | "device" | ""

        layout.addWidget(self._track_table, stretch=1)
        return widget

    @staticmethod
    def _make_section_header(title: str) -> QLabel:
        from ui.style import BG_MAIN, FG_SECONDARY
        lbl = QLabel(title)
        lbl.setFixedHeight(24)
        lbl.setStyleSheet(
            f"background-color: {BG_MAIN}; color: {FG_SECONDARY}; "
            f"font-size: 10px; font-weight: bold; padding: 0 8px;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        return lbl

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    def _autodetect_pacemaker_dbs(self):
        editor_path = find_editor_db()
        if editor_path:
            self._panel.set_db_path(editor_path)   # triggers _on_editor_db_changed

        device_path = find_device_db()
        if device_path:
            self._device_panel.set_db_path_label(device_path)
            self._load_device_library()
            self.statusBar().showMessage(
                f"Device found: {device_path}"
                + (f"  |  Editor: {editor_path}" if editor_path else "")
            )

    # ------------------------------------------------------------------
    # Rekordbox loading
    # ------------------------------------------------------------------

    def _load_rekordbox(self):
        try:
            _logger.log.info("Loading Rekordbox library…")
            self._rb_reader = RekordboxReader()
            self._playlist_nodes = self._rb_reader.get_playlist_tree()
            self._refresh_tree()
            self._player_bar.set_rekordbox_reader(self._rb_reader)
            _logger.log.info("Rekordbox library loaded — %d top-level nodes", len(self._playlist_nodes))
            self.statusBar().showMessage("Rekordbox library loaded.")
        except Exception as e:
            _logger.log.error("Could not load Rekordbox: %s", e, exc_info=True)
            self.statusBar().showMessage(f"Could not load Rekordbox: {e}")
            QMessageBox.warning(
                self, "Rekordbox Not Found",
                f"Could not connect to the Rekordbox database:\n\n{e}\n\n"
                "You can still use File → Import from M3U8."
            )

    def _refresh_tree(self):
        self._tree.load_tree(self._playlist_nodes, set())

    # ------------------------------------------------------------------
    # Queue computation
    # ------------------------------------------------------------------

    def _on_selection_changed(self):
        self._recompute_queue()

    def _recompute_queue(self):
        if not self._rb_reader:
            return

        checked_ids = set(self._tree.get_checked_playlist_ids())
        path_map = self._build_playlist_path_map()
        items: list[SyncQueueItem] = []

        for pid in checked_ids:
            node = self._find_node(pid)
            if node is None:
                continue
            case_name = self._make_case_name(path_map.get(pid, [node.name]))
            items.append(SyncQueueItem(
                playlist_id=pid,
                name=case_name,
                track_count=node.track_count,
                action="add",
                was_synced=False,
            ))

        self._panel.update_queue(items)

    def _find_node(self, playlist_id: str) -> PlaylistNode | None:
        def search(nodes):
            for n in nodes:
                if n.id == playlist_id:
                    return n
                found = search(n.children)
                if found:
                    return found
            return None
        return search(self._playlist_nodes)

    def _build_playlist_path_map(self) -> dict:
        """Return {playlist_id: [folder_name, ..., playlist_name]} for all playlists."""
        result = {}

        def traverse(nodes, ancestors):
            for node in nodes:
                if node.is_folder:
                    traverse(node.children, ancestors + [node.name])
                else:
                    result[node.id] = ancestors + [node.name]

        traverse(self._playlist_nodes, [])
        return result

    @staticmethod
    def _shorten_segment(segment: str, max_chars: int) -> str:
        """Strip a leading ordinal prefix (e.g. '08 - ') then truncate."""
        cleaned = re.sub(r"^\d+\s*[-–]\s*", "", segment).strip()
        return cleaned[:max_chars] if cleaned else segment[:max_chars]

    @staticmethod
    def _make_case_name(parts: list) -> str:
        """
        Build a compact, screen-friendly case name from folder path parts.

        Format:  folder segments joined with "_" (each stripped + 3 chars)
                 + "_" + playlist name (stripped, spaces removed, 12 chars)

        Example: ["2025", "08 - Aug", "IYKYK", "Deep or Heavy"]
              →  "2025_Aug_IYK_DeaporHeavy"  (approx)

        Root-level playlists (no folders) are kept as-is, truncated to 24 chars.
        """
        if not parts:
            return "Unnamed"

        if len(parts) == 1:
            name = parts[0]
            return name if len(name) <= 24 else name[:23] + "\u2026"

        folder_parts = parts[:-1]
        playlist_name = parts[-1]

        short_folders = [MainWindow._shorten_segment(f, 4) for f in folder_parts]
        pl_clean = re.sub(r"^\d+\s*[-–]\s*", "", playlist_name).strip()
        pl_compact = pl_clean.replace(" ", "")[:12]

        return "_".join(short_folders + [pl_compact])

    # ------------------------------------------------------------------
    # Rekordbox → Editor sync
    # ------------------------------------------------------------------

    def _confirm_and_sync(self):
        db_path = self._panel.db_path
        if not db_path:
            QMessageBox.warning(self, "No Database", "Please select an Editor database first.")
            return

        checked_ids = set(self._tree.get_checked_playlist_ids())
        if not checked_ids:
            QMessageBox.information(self, "Nothing to do", "No playlists are checked.")
            return

        path_map = self._build_playlist_path_map()
        operations = []

        for pid in checked_ids:
            node = self._find_node(pid)
            if node is None:
                continue
            case_name = self._make_case_name(path_map.get(pid, [node.name]))
            tracks = self._rb_reader.get_playlist_tracks(pid)
            operations.append({
                "playlist_id": pid,
                "playlist_name": case_name,
                "tracks": tracks,
            })

        if not operations:
            return

        total_tracks = sum(len(o["tracks"]) for o in operations)
        reply = QMessageBox.question(
            self, "Confirm Sync to Editor",
            f"Add {len(operations)} playlist(s) ({total_tracks} tracks) to the Editor library?\n\n"
            "Existing cases are not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_sync(db_path, operations)

    def _run_sync(self, db_path: str, operations: list[dict]):
        total_tracks = sum(len(op["tracks"]) for op in operations)

        self._sync_progress_dlg = QProgressDialog(
            "Importing track 0 of 0…", None, 0, max(total_tracks, 1), self
        )
        self._sync_progress_dlg.setWindowTitle("Import to Editor Library")
        self._sync_progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._sync_progress_dlg.setMinimumDuration(0)
        self._sync_progress_dlg.setValue(0)
        self._sync_progress_dlg.show()

        self._sync_thread = QThread()
        self._worker = SyncWorker(db_path, operations)
        self._worker.moveToThread(self._sync_thread)

        self._sync_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_sync_progress)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.finished.connect(self._sync_thread.quit)

        self._sync_thread.start()

    def _on_sync_progress(self, current: int, total: int, status: str):
        dlg = self._sync_progress_dlg
        if dlg is not None:
            dlg.setValue(current)
            dlg.setLabelText(f"Importing track {current} of {total}…")

    def _on_sync_finished(self, success: bool, message: str):
        if self._sync_progress_dlg:
            self._sync_progress_dlg.close()
            self._sync_progress_dlg = None

        if success:
            _logger.log.info("Import to editor finished: %s", message)
            self._tree.uncheck_all()
            self._load_editor_library()
        else:
            _logger.log.error("Import to editor failed: %s", message)
            QMessageBox.critical(self, "Import Failed", f"An error occurred:\n\n{message}")

        self.statusBar().showMessage(message)

    # ------------------------------------------------------------------
    # Editor → Device sync  (Rekordbox-style: checked = on device)
    # ------------------------------------------------------------------

    def _confirm_and_sync_to_device(self):
        editor_db = self._panel.db_path
        device_db = self._device_panel.db_path

        if not editor_db:
            QMessageBox.warning(self, "No Editor Database", "Editor database not loaded.")
            return
        if not device_db:
            QMessageBox.warning(
                self, "No Device",
                "Pacemaker device not found.\n\n"
                "Connect the device and click Refresh, or use Browse to locate its music.db."
            )
            return

        # All Editor cases and which are checked
        try:
            with PacemakerWriter(editor_db) as writer:
                all_editor_cases = writer.get_all_cases()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        editor_case_by_id = {c["case_id"]: c for c in all_editor_cases}
        checked_ids = {c["case_id"] for c in self._editor_panel.get_checked_cases()}

        # Current sync map — tells us what is already on this device
        sync_map = _read_sync_map(device_db)
        synced_editor_ids = {int(k) for k in sync_map}

        # Diff: new cases to push, existing cases to remove
        to_add = [
            editor_case_by_id[eid] for eid in checked_ids
            if eid not in synced_editor_ids and eid in editor_case_by_id
        ]
        to_remove = [
            {
                "editor_case_id": int(k),
                "device_case_id": v,
                "name": editor_case_by_id.get(int(k), {}).get("name", str(k)),
            }
            for k, v in sync_map.items()
            if int(k) not in checked_ids
        ]

        if not to_add and not to_remove:
            # Count unsynced cases the user hasn't checked yet
            unsynced_unchecked = [
                eid for eid in editor_case_by_id
                if eid not in synced_editor_ids and eid not in checked_ids
            ]
            hint = (
                f"  ({len(unsynced_unchecked)} unchecked case{'s' if len(unsynced_unchecked) != 1 else ''}"
                f" not yet on device — check them and sync again)"
                if unsynced_unchecked else ""
            )
            self.statusBar().showMessage(f"Device already in sync with checked cases.{hint}")
            return

        self._run_device_sync(editor_db, device_db, to_add, to_remove, sync_map)

    def _run_device_sync(self, editor_db: str, device_db: str,
                         to_add: list, to_remove: list, sync_map: dict):
        self._editor_panel.setEnabled(False)
        self._device_panel.setEnabled(False)
        self._editor_panel.push_button.setEnabled(False)

        total_tracks = sum(c.get("track_count", 0) for c in to_add)
        self._push_progress_dlg = QProgressDialog(
            "Syncing to device…", None, 0, max(total_tracks, 1), self
        )
        self._push_progress_dlg.setWindowTitle("Sync to Device")
        self._push_progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._push_progress_dlg.setMinimumDuration(0)
        self._push_progress_dlg.setValue(0)
        self._push_progress_dlg.show()

        self._push_thread = QThread()
        self._push_worker = DeviceSyncWorker(editor_db, device_db, to_add, to_remove, sync_map)
        self._push_worker.moveToThread(self._push_thread)

        self._push_thread.started.connect(self._push_worker.run)
        self._push_worker.progress.connect(self._on_device_sync_progress)
        self._push_worker.finished.connect(self._on_device_sync_finished)
        self._push_worker.finished.connect(self._push_thread.quit)

        self._push_thread.start()

    def _on_device_sync_progress(self, current: int, total: int, status: str):
        if self._push_progress_dlg:
            self._push_progress_dlg.setValue(current)
            self._push_progress_dlg.setLabelText(status)

    def _on_device_sync_finished(self, success: bool, message: str):
        if self._push_progress_dlg:
            self._push_progress_dlg.close()
            self._push_progress_dlg = None

        self._editor_panel.setEnabled(True)
        self._device_panel.setEnabled(True)
        self._editor_panel.push_button.setEnabled(True)

        if success:
            self._play_completion_sound()
            self._load_device_library()
            self._apply_device_sync_state()
        else:
            QMessageBox.critical(self, "Sync Failed", f"An error occurred:\n\n{message}")

        self.statusBar().showMessage(message)

    @staticmethod
    def _play_completion_sound() -> None:
        """Play a short completion chime using Windows MessageBeep (no extra deps)."""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)  # asterisk = info chime
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Editor library panel
    # ------------------------------------------------------------------

    def _on_editor_db_changed(self, path: str):
        short = os.path.basename(os.path.dirname(path)) if path else ""
        self._editor_path_lbl.setText(f"Editor: {short or 'not loaded'}")
        self._editor_panel.set_db_path_label(path)
        if path:
            try:
                with PacemakerWriter(path) as w:
                    fixed = w.fix_bpm_values()
                if fixed:
                    self.statusBar().showMessage(
                        f"Migrated {fixed} track BPM value(s) from ×100 format."
                    )
            except Exception:
                pass
        self._refresh_tree()
        self._recompute_queue()
        self._load_editor_library()

    def _load_editor_library(self, preserve_checked: bool = True):
        # Save current selections before rebuilding the list so that refreshes
        # triggered by renames, imports, etc. don't wipe the user's checkbox state.
        currently_checked = (
            {c["case_id"] for c in self._editor_panel.get_checked_cases()}
            if preserve_checked else set()
        )
        db_path = self._panel.db_path
        if not db_path:
            self._editor_panel.clear()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                cases = writer.get_all_cases()
                all_tracks = writer.get_all_tracks_with_case_count()
            self._editor_panel.load_cases(cases, set())
            self._editor_panel.load_all_device_tracks(all_tracks)
        except Exception as e:
            self._editor_panel.clear()
            self.statusBar().showMessage(f"Could not read Editor library: {e}")
            return
        self._apply_device_sync_state(extra_checked=currently_checked)

    def _on_editor_case_selected(self, case_id: int) -> None:
        """Load Editor case tracks into the central track table."""
        db_path = self._panel.db_path
        if not db_path or case_id == -1:
            self._clear_track_table()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                tracks = writer.get_case_tracks_as_trackinfo(case_id)
            case = self._editor_panel.selected_case()
            name = case["name"] if case else "Case"
            self._load_tracks_in_table(tracks, source="editor",
                                       label=f"{name}  —  {len(tracks)} tracks")
        except Exception:
            self._clear_track_table()

    def _on_device_case_selected(self, case_id: int) -> None:
        """Load Device case tracks into the central track table (read-only)."""
        db_path = self._device_panel.db_path
        if not db_path or case_id == -1:
            self._clear_track_table()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                tracks = writer.get_case_tracks_as_trackinfo(case_id)
            case = self._device_panel.selected_case()
            name = case["name"] if case else "Case"
            self._load_tracks_in_table(tracks, source="device",
                                       label=f"{name}  —  {len(tracks)} tracks  [device]")
        except Exception:
            self._clear_track_table()

    def _load_tracks_in_table(self, tracks: list[TrackInfo], source: str, label: str) -> None:
        """Populate the central track table. source='editor'|'device'."""
        from ui.style import ACCENT
        from PyQt6.QtGui import QColor as _QColor

        # Stop any inline preview that was running for the previous case
        self._stop_preview()

        # Cancel any running waveform batch
        if hasattr(self, "_wave_worker") and self._wave_worker:
            self._wave_worker.cancel()

        self._track_table.setSortingEnabled(False)
        self._track_table.setRowCount(0)
        self._track_table_tracks = list(tracks)
        self._track_table_source = source
        self._track_view_lbl.setText(label)

        STAR_MAP = {0: "", 1: "★", 2: "★★", 3: "★★★", 4: "★★★★", 5: "★★★★★"}

        for i, t in enumerate(tracks):
            self._track_table.insertRow(i)
            secs = int(t.play_time_secs or 0)
            duration = f"{secs // 60}:{secs % 60:02d}"
            bpm_val = int(t.bpm) if t.bpm else 0
            if bpm_val > 1000:
                bpm_val = round(bpm_val / 100)
            bpm = str(bpm_val) if bpm_val else ""
            stars = STAR_MAP.get(t.rating, "")

            # cols: #(0) ★(1) ~wave(2) Title(3) Artist(4) BPM(5) Key(6) Time(7) Genre(8)
            _numeric_sort = {0: i + 1, 5: bpm_val, 7: secs}
            for col, val in enumerate([
                str(i + 1), stars, "", t.title or "", t.artist or "",
                bpm, t.key or "", duration, t.genre or "",
            ]):
                item = _SortableItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, i)
                if col in _numeric_sort:
                    item.setData(_SortableItem._SORT_ROLE, _numeric_sort[col])
                if col in (0, 1, 2, 5, 6, 7):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 1 and val:
                    item.setForeground(_QColor(ACCENT))
                self._track_table.setItem(i, col, item)

        self._track_table.setSortingEnabled(True)

        # Fetch waveforms in background (only for editor source where we have RB data)
        if source == "editor" and self._rb_reader and tracks:
            self._start_waveform_batch([t.location for t in tracks])

    def _start_waveform_batch(self, locations: list[str]) -> None:
        # Increment generation so any in-flight results from the previous batch
        # are silently discarded by _on_wave_row_done.
        self._wave_generation = getattr(self, "_wave_generation", 0) + 1
        gen = self._wave_generation

        # Cancel previous worker (sets a flag; it will finish soon)
        if hasattr(self, "_wave_worker") and self._wave_worker:
            self._wave_worker.cancel()
            self._wave_worker = None

        # Keep a list of all in-flight (thread, worker) pairs so Python's GC
        # cannot collect them while the C++ thread is still running.
        if not hasattr(self, "_wave_threads"):
            self._wave_threads: list = []

        thread = QThread()
        worker = _WaveformBatchWorker(gen, locations)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.row_done.connect(self._on_wave_row_done)
        worker.finished.connect(thread.quit)

        def _cleanup(t=thread, w=worker):
            try:
                self._wave_threads.remove((t, w))
            except ValueError:
                pass

        thread.finished.connect(_cleanup)
        self._wave_threads.append((thread, worker))

        self._wave_thread = thread
        self._wave_worker = worker
        thread.start()

    def _on_wave_row_done(self, generation: int, row: int, data) -> None:
        # Ignore results from a cancelled/superseded batch
        if generation != getattr(self, "_wave_generation", 0):
            return
        if row < self._track_table.rowCount():
            item = self._track_table.item(row, 2)
            if item and data:
                item.setData(_WAVE_COL_ROLE, data)
                self._track_table.update(
                    self._track_table.model().index(row, 2)
                )

    def _clear_track_table(self) -> None:
        self._track_table.setRowCount(0)
        self._track_table_tracks = []
        self._track_table_source = ""
        self._track_view_lbl.setText("Select a playlist or case to view tracks")

    def _on_track_double_clicked(self, item: QTableWidgetItem) -> None:
        """Play the double-clicked track (Editor source only)."""
        if self._track_table_source != "editor":
            return
        orig_row = item.data(Qt.ItemDataRole.UserRole)
        if orig_row is None:
            orig_row = item.row()
        if 0 <= orig_row < len(self._track_table_tracks):
            self._player_bar.load_and_play(self._track_table_tracks, orig_row)

    def _on_player_track_changed(self, index: int) -> None:
        """Highlight the currently playing track row in the table."""
        from PyQt6.QtGui import QColor
        from ui.style import ACCENT
        if self._track_table_source != "editor":
            return
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == index:
                self._track_table.selectRow(row)
                break

    # ------------------------------------------------------------------
    # Inline waveform click-to-preview
    # ------------------------------------------------------------------

    _STOP_BTN_WIDTH = 20   # px from left edge of the waveform cell that acts as stop

    def eventFilter(self, obj, event) -> bool:
        """Intercept mouse clicks on the track table viewport to handle waveform col."""
        if obj is self._track_table.viewport():
            if event.type() == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    index = self._track_table.indexAt(event.pos())
                    if index.isValid() and index.column() == 2:
                        cell_rect = self._track_table.visualRect(index)
                        x_in_cell = event.pos().x() - cell_rect.left()
                        fraction = max(0.0, min(1.0,
                            x_in_cell / max(cell_rect.width(), 1)
                        ))
                        self._on_wave_cell_clicked_at(index.row(), fraction, x_in_cell)
                        return True   # consume — prevent default selection noise
        return super().eventFilter(obj, event)

    def _on_wave_cell_clicked_at(self, row: int, fraction: float, x_in_cell: float) -> None:
        """
        Handle a click inside the waveform cell of `row`.

        - Active preview row + click in left stop zone  → stop.
        - Active preview row + click outside stop zone  → seek.
        - Different row                                  → start from clicked position.
        """
        item = self._track_table.item(row, 0)
        orig_row = item.data(Qt.ItemDataRole.UserRole) if item else row
        if orig_row is None:
            orig_row = row
        if not (0 <= orig_row < len(self._track_table_tracks)):
            return

        track = self._track_table_tracks[orig_row]

        if self._preview_row == row:
            # Stop button zone (left edge)
            if x_in_cell <= self._STOP_BTN_WIDTH:
                self._stop_preview()
                return
            # Outside stop zone: seek to clicked position
            if self._preview_player:
                dur = self._preview_player.duration()
                if dur <= 0:
                    dur = int(track.play_time_secs * 1000)
                if dur > 0:
                    self._preview_player.setPosition(int(fraction * dur))
        else:
            # New row: stop current preview and main player, start from clicked position
            if not os.path.exists(track.location):
                self.statusBar().showMessage(f"File not found: {track.location}")
                return
            self._player_bar.stop()
            self._stop_preview()
            self._preview_row = row
            self._wave_delegate.preview_row = row
            self._preview_seek_fraction = fraction
            if self._preview_player:
                self._preview_player.setSource(QUrl.fromLocalFile(track.location))
                self._preview_player.play()
            self._track_table.viewport().update()

    def _stop_preview(self) -> None:
        """Stop the inline preview and clear the stop-button overlay."""
        if self._preview_player:
            self._preview_player.stop()
        self._preview_seek_fraction = 0.0
        old = self._preview_row
        self._preview_row = -1
        self._wave_delegate.preview_row = -1
        if old >= 0:
            self._track_table.viewport().update()

    def _on_preview_media_status(self, status) -> None:
        """Seek to the clicked position once media is loaded; stop at end."""
        if not _PREVIEW_OK:
            return
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            # Media just loaded — apply the initial seek if the user clicked mid-track
            if self._preview_seek_fraction > 0.0 and self._preview_player:
                dur = self._preview_player.duration()
                if dur > 0:
                    self._preview_player.setPosition(
                        int(self._preview_seek_fraction * dur)
                    )
                self._preview_seek_fraction = 0.0
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._stop_preview()

    def _on_delete_editor_case(self, cases: list):
        self._delete_cases(
            db_path=self._panel.db_path,
            cases=cases,
            update_sync_state=True,
            refresh_fn=lambda: (
                self._load_editor_library(),
                self._refresh_tree(),
                self._recompute_queue(),
            ),
        )

    def _on_rename_editor_case(self, cases: list):
        db_path = self._panel.db_path
        if not db_path:
            return

        total = len(cases)
        for i, case in enumerate(cases):
            title = "Rename Case" if total == 1 else f"Rename Case ({i + 1} of {total})"
            new_name, ok = QInputDialog.getText(
                self, title, "New name:", text=case["name"]
            )
            if not ok:
                break   # Cancel stops the whole sequence
            new_name = new_name.strip()
            if not new_name or new_name == case["name"]:
                continue  # empty or unchanged — skip silently

            try:
                with PacemakerWriter(db_path) as writer:
                    writer.rename_case(case["case_id"], new_name)
                self.statusBar().showMessage(f'Renamed "{case["name"]}" → "{new_name}".')
            except Exception as e:
                QMessageBox.critical(self, "Rename Failed", str(e))
                break

        self._load_editor_library()

    def _on_inline_rename_editor_case(self, case_id: int, new_name: str) -> None:
        """Handle an inline (double-click) rename committed directly in the list."""
        db_path = self._panel.db_path
        if not db_path:
            return
        try:
            with PacemakerWriter(db_path) as writer:
                writer.rename_case(case_id, new_name)
            _logger.log.info("Renamed editor case %d to %r (inline)", case_id, new_name)
            self.statusBar().showMessage(f'Renamed to "{new_name}".')
            self._load_editor_library()
        except Exception as e:
            _logger.log.error("Inline rename failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Rename Failed", str(e))

    def _on_track_tab_play(self, track_dict: dict) -> None:
        """Play a track selected by double-click in the editor Tracks tab."""
        location = track_dict.get("location") or ""
        if not location or not os.path.exists(location):
            self.statusBar().showMessage(f"File not found: {location}")
            return
        track = TrackInfo(
            location=location,
            title=track_dict.get("title") or "",
            artist=track_dict.get("artist") or "",
            album="", album_artist="", composer="", genre="",
            label="", producer="", remixer="", key="",
            year="", comments="",
            bpm=float(track_dict.get("bpm") or 0),
            rating=int(track_dict.get("rating") or 0),
            track_number=0, number_of_tracks=0,
            disc_number=0, number_of_discs=0,
            bit_rate=int(track_dict.get("bit_rate") or 0),
            sample_rate=int(track_dict.get("sample_rate") or 0),
            play_time_secs=int(track_dict.get("play_time_secs") or 0),
            file_size=int(track_dict.get("file_size") or 0),
            format=track_dict.get("format") or "",
        )
        self._player_bar.load_and_play([track], 0)

    def _on_track_tab_selected(self, track_id: int) -> None:
        """Show which cases contain the selected track in the Tracks tab banner."""
        if track_id == -1:
            self._editor_panel.set_track_cases_banner([])
            return
        db_path = self._panel.db_path
        if not db_path:
            return
        try:
            with PacemakerWriter(db_path) as writer:
                names = writer.get_cases_for_track(track_id)
            self._editor_panel.set_track_cases_banner(names)
        except Exception:
            self._editor_panel.set_track_cases_banner([])

    # ------------------------------------------------------------------
    # Device library panel
    # ------------------------------------------------------------------

    def _on_device_db_changed(self, path: str):
        self._load_device_library()

    def _load_device_library(self):
        db_path = self._device_panel.db_path
        if not db_path:
            self._device_panel.clear()
            return
        # Migrate any ×100 BPM values that may be in the device DB
        try:
            with PacemakerWriter(db_path) as w:
                fixed = w.fix_bpm_values()
            if fixed:
                self.statusBar().showMessage(
                    f"Migrated {fixed} track BPM value(s) in device DB from ×100 format."
                )
        except Exception:
            pass
        try:
            with PacemakerWriter(db_path) as writer:
                cases = writer.get_all_cases()
                all_tracks = writer.get_all_tracks_with_case_count()
            self._device_panel.load_cases(cases, set())
            self._device_panel.load_all_device_tracks(all_tracks)
        except Exception as e:
            self._device_panel.clear()
            self.statusBar().showMessage(f"Could not read Device library: {e}")
            return
        self._load_device_storage()
        self._apply_device_sync_state()

    def _load_device_storage(self) -> None:
        """Read device drive free/total space and used bytes from DB, update storage bar."""
        import ctypes
        device_db = self._device_panel.db_path
        if not device_db:
            return
        drive = os.path.splitdrive(device_db)[0] + os.sep  # e.g. "J:\"

        # Get drive capacity from OS
        free_bytes = ctypes.c_ulonglong(0)
        total_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(
            drive, None, ctypes.byref(total_bytes), ctypes.byref(free_bytes)
        )
        total = total_bytes.value
        free = free_bytes.value
        used = total - free

        self._device_panel.set_storage_info(used, total)

    def _apply_device_sync_state(self, extra_checked: "set[int] | None" = None) -> None:
        """Read sync map from device and check the corresponding Editor cases.

        extra_checked: additional case IDs to keep checked (preserves user selections
        across library refreshes; ignored when None so callers can do a clean reset).
        """
        device_db = self._device_panel.db_path
        if not device_db or not self._panel.db_path:
            if extra_checked:
                self._editor_panel.set_checked_cases(extra_checked)
            return
        sync_map = _read_sync_map(device_db)
        synced_editor_ids = {int(k) for k in sync_map}
        checked = synced_editor_ids | (extra_checked or set())
        self._editor_panel.set_checked_cases(checked)
        self._editor_panel.push_button.setEnabled(True)

    def _on_delete_device_case(self, cases: list):
        self._delete_cases(
            db_path=self._device_panel.db_path,
            cases=cases,
            update_sync_state=False,
            refresh_fn=lambda: self._load_device_library(),
        )

    def _eject_device(self):
        db_path = self._device_panel.db_path
        if not db_path:
            return
        drive = os.path.splitdrive(db_path)[0]   # e.g. "J:"
        letter = drive.rstrip(":")               # "J"

        ok, err = self._do_eject(letter)
        # Clear the UI regardless — the device is being disconnected
        self._device_panel.set_db_path_label("")
        self._device_panel.clear()
        self._device_path_lbl.setText("Device: not connected")
        # Uncheck all Editor cases and disable Sync button (no device connected)
        self._editor_panel.set_checked_cases(set())
        self._editor_panel.push_button.setEnabled(False)
        if ok:
            self.statusBar().showMessage(f"{drive} ejected — safe to disconnect.")
        else:
            QMessageBox.warning(
                self, "Eject Failed",
                f"Could not safely eject {drive}.\n"
                f"{err}\n\n"
                "Use 'Safely Remove Hardware' in the system tray."
            )

    @staticmethod
    def _do_eject(letter: str):
        """
        Physically eject a drive via Windows DeviceIoControl.
        Sequence: open volume → lock → dismount → eject media.
        Returns (success: bool, error_message: str).
        """
        import ctypes
        import ctypes.wintypes

        GENERIC_READ           = 0x80000000
        GENERIC_WRITE          = 0x40000000
        FILE_SHARE_READ        = 0x00000001
        FILE_SHARE_WRITE       = 0x00000002
        OPEN_EXISTING          = 3
        FSCTL_LOCK_VOLUME      = 0x00090018
        FSCTL_DISMOUNT_VOLUME  = 0x00090020
        IOCTL_STORAGE_EJECT_MEDIA = 0x2D4808

        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = ctypes.c_void_p

        handle = k32.CreateFileW(
            f"\\\\.\\{letter}:",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        INVALID = ctypes.c_void_p(-1).value
        if handle is None or handle == INVALID:
            err = ctypes.WinError().strerror
            return False, f"Could not open volume: {err}"

        try:
            br = ctypes.wintypes.DWORD(0)
            k32.DeviceIoControl(handle, FSCTL_LOCK_VOLUME,
                                None, 0, None, 0, ctypes.byref(br), None)
            k32.DeviceIoControl(handle, FSCTL_DISMOUNT_VOLUME,
                                None, 0, None, 0, ctypes.byref(br), None)
            ok = k32.DeviceIoControl(handle, IOCTL_STORAGE_EJECT_MEDIA,
                                     None, 0, None, 0, ctypes.byref(br), None)
            if not ok:
                err = ctypes.WinError().strerror
                return False, f"Eject command failed: {err}"
            return True, ""
        finally:
            k32.CloseHandle(handle)

    # ------------------------------------------------------------------
    # Device auto-detection (polled every 2 s)
    # ------------------------------------------------------------------

    def _poll_for_device(self) -> None:
        """
        Check all connected drives for a Pacemaker database.
        Stops polling once a device is loaded; resumes after eject.
        """
        if self._device_panel.db_path:
            return  # Already have a device loaded

        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                letter = chr(ord("A") + i)
                db_path = rf"{letter}:\.Pacemaker\music.db"
                if os.path.exists(db_path):
                    self._auto_load_device(db_path)
                    return

    def _auto_load_device(self, db_path: str) -> None:
        """Called when a Pacemaker drive is detected. Loads its library."""
        self._device_panel.set_db_path_label(db_path)
        self._load_device_library()
        drive = os.path.splitdrive(db_path)[0]
        self._device_path_lbl.setText(f"Device: {drive}")
        self.statusBar().showMessage(
            f"Pacemaker detected on {drive} — Device Library loaded."
        )

    # ------------------------------------------------------------------
    # Shared delete logic
    # ------------------------------------------------------------------

    def _delete_cases(
        self,
        db_path: str,
        cases: list,
        update_sync_state: bool,
        refresh_fn,
    ):
        if not db_path or not cases:
            return

        if len(cases) == 1:
            detail = f"\"{cases[0]['name']}\""
        else:
            detail = f"{len(cases)} cases"

        reply = QMessageBox.question(
            self, "Delete Case" if len(cases) == 1 else "Delete Cases",
            f"Permanently delete {detail}?\n\n"
            "Tracks not used by any other case will also be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            deleted_locs: list[str] = []
            with PacemakerWriter(db_path) as writer:
                for case in cases:
                    locations = list(writer.get_case_track_locations(case["case_id"]))
                    actually_deleted = writer.remove_playlist(case["case_id"], locations)
                    deleted_locs.extend(actually_deleted)

            # If deleting from the device DB, remove the audio files too
            if db_path == self._device_panel.db_path and deleted_locs:
                DeviceSyncWorker._delete_device_files(deleted_locs, db_path)

            if update_sync_state:
                state = sync_state.load(db_path)
                deleted_ids = {c["case_id"] for c in cases}
                for pid, entry in list(state.items()):
                    if entry.get("pacemaker_case_id") in deleted_ids:
                        sync_state.remove_sync(db_path, pid)

            refresh_fn()
            if len(cases) == 1:
                self.statusBar().showMessage(f'Deleted case "{cases[0]["name"]}".')
            else:
                self.statusBar().showMessage(f"Deleted {len(cases)} cases.")
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))

    # ------------------------------------------------------------------
    # Repair device DB
    # ------------------------------------------------------------------

    def _cleanup_orphaned_device_files(self):
        """Delete audio files in J:\\.Pacemaker\\Music\\ not referenced in the device DB."""
        device_db = self._device_panel.db_path
        if not device_db:
            QMessageBox.warning(self, "No Device", "No device database loaded.")
            return

        device_drive = os.path.splitdrive(device_db)[0]
        music_root = os.path.join(device_drive, os.sep, ".Pacemaker", "Music")
        if not os.path.isdir(music_root):
            QMessageBox.information(self, "Clean Up", "No Music folder found on device.")
            return

        try:
            with PacemakerWriter(device_db) as writer:
                db_tracks = writer.get_all_tracks_with_case_count()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        db_locations = {t["location"] for t in db_tracks}

        _AUDIO_EXTS = {".mp3", ".flac", ".aac", ".m4a", ".mp4", ".wav", ".aiff", ".ogg"}

        to_delete: list[str] = []
        for dirpath, _dirs, files in os.walk(music_root):
            for fname in files:
                if os.path.splitext(fname)[1].lower() not in _AUDIO_EXTS:
                    continue
                full = os.path.join(dirpath, fname)
                rel = full[len(music_root):].lstrip(os.sep).replace(os.sep, "/")
                if f"/pmdb_tracks/{rel}" not in db_locations:
                    to_delete.append(full)

        if not to_delete:
            QMessageBox.information(self, "Clean Up", "No orphaned audio files found — device is clean.")
            return

        total_size = sum(os.path.getsize(p) for p in to_delete if os.path.exists(p))
        reply = QMessageBox.question(
            self, "Clean Up Orphaned Files",
            f"Found {len(to_delete)} audio file(s) on the device not in the database.\n"
            f"Total size: {total_size / 1_000_000_000:.2f} GB\n\n"
            "Permanently delete these files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Run deletion in a background thread with a progress dialog
        progress = QProgressDialog(
            "Deleting orphaned files…", "Cancel", 0, len(to_delete), self
        )
        progress.setWindowTitle("Clean Up Device Files")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        class _CleanupWorker(QObject):
            file_done = pyqtSignal(int, int)      # deleted_count, freed_bytes
            finished  = pyqtSignal(int, int)      # total_deleted, total_freed

            def __init__(self, paths: list):
                super().__init__()
                self._paths = paths
                self._cancelled = False

            def cancel(self) -> None:
                self._cancelled = True

            def run(self) -> None:
                deleted = freed = 0
                for i, path in enumerate(self._paths):
                    if self._cancelled:
                        break
                    try:
                        freed += os.path.getsize(path) if os.path.exists(path) else 0
                        os.remove(path)
                        deleted += 1
                        str_path = os.path.splitext(path)[0] + ".str"
                        if os.path.exists(str_path):
                            os.remove(str_path)
                    except Exception:
                        pass
                    self.file_done.emit(i + 1, freed)
                self.finished.emit(deleted, freed)

        thread = QThread(self)
        worker = _CleanupWorker(to_delete)
        worker.moveToThread(thread)

        def on_file_done(done: int, freed: int) -> None:
            if progress.wasCanceled():
                worker.cancel()
                return
            progress.setValue(done)
            progress.setLabelText(
                f"Deleting file {done} of {len(to_delete)}… "
                f"({freed / 1_000_000_000:.2f} GB freed)"
            )

        def on_finished(deleted: int, freed: int) -> None:
            progress.close()
            thread.quit()
            self._load_device_storage()
            QMessageBox.information(
                self, "Clean Up Complete",
                f"Deleted {deleted} file(s) and freed {freed / 1_000_000_000:.2f} GB.",
            )

        thread.started.connect(worker.run)
        worker.file_done.connect(on_file_done)
        worker.finished.connect(on_finished)
        progress.canceled.connect(worker.cancel)
        thread.start()

    # ------------------------------------------------------------------
    # Debug logging
    # ------------------------------------------------------------------

    def _toggle_debug_logging(self, checked: bool) -> None:
        if checked:
            editor_db = self._panel.db_path
            if editor_db:
                log_dir = os.path.dirname(editor_db)
            else:
                log_dir = os.path.expandvars(r"%APPDATA%\Tonium\Pacemaker")
            log_path = os.path.join(log_dir, "rb_pm_sync.log")
            _logger.enable(log_path)
            self._open_log_action.setEnabled(True)
            self.statusBar().showMessage(f"Debug logging enabled → {log_path}")
            _logger.log.info("App version: rekordbox-pacemaker-sync")
            _logger.log.info("Editor DB: %s", self._panel.db_path or "none")
            _logger.log.info("Device DB: %s", self._device_panel.db_path or "none")
        else:
            _logger.disable()
            self._open_log_action.setEnabled(False)
            self.statusBar().showMessage("Debug logging disabled.")

    def _open_log_file(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        editor_db = self._panel.db_path
        log_dir = os.path.dirname(editor_db) if editor_db else os.path.expandvars(
            r"%APPDATA%\Tonium\Pacemaker"
        )
        log_path = os.path.join(log_dir, "rb_pm_sync.log")
        if os.path.exists(log_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(log_path))
        else:
            QMessageBox.information(self, "Log File", f"Log file not found:\n{log_path}")

    def _open_repair_dialog(self):
        dlg = RepairDeviceDbDialog(self._device_panel.db_path, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_device_library()

    # ------------------------------------------------------------------
    # M3U8 import
    # ------------------------------------------------------------------

    def _open_m3u8_import(self):
        if not self._panel.db_path:
            QMessageBox.warning(
                self, "No Database",
                "Please select a Pacemaker database before importing."
            )
            return
        dlg = M3U8ImportDialog(self._panel.db_path, self)
        dlg.exec()
