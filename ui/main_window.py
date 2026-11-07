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
import errno
import os
import re
import shutil
import sys
import threading
import zlib
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QProgressDialog,
    QApplication, QFrame, QInputDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QMenu,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal, QObject, QUrl, QEvent, QMimeData
from PyQt6.QtGui import QAction, QPainter, QColor, QBrush, QDrag

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _PREVIEW_OK = True
except ImportError:
    _PREVIEW_OK = False

from core.rekordbox_reader import (
    RekordboxError, RekordboxNotFoundError, RekordboxReader, PlaylistNode, TrackInfo,
)
from core import logger as _logger
from ui.style import ACCENT

_WAVE_COL_ROLE = Qt.ItemDataRole.UserRole + 3   # stores list[tuple[int,int]] waveform data

# The main delete key on a Mac keyboard is Key_Backspace to Qt. Key_Delete is fn+delete.
_REMOVE_TRACK_KEYS = (
    (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) if sys.platform == "darwin" else (Qt.Key.Key_Delete,)
)

_ROW_HOVER_TINT = QColor(ACCENT)
_ROW_HOVER_TINT.setAlpha(90)

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
    When preview_row matches the track being painted, draws a stop-button overlay.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.preview_row: int = -1   # original track index being previewed (-1 = none)

    def paint(self, painter: QPainter, option, index) -> None:
        data = index.data(_WAVE_COL_ROLE)
        is_preview = (index.data(Qt.ItemDataRole.UserRole) == self.preview_row)

        if not data:
            painter.fillRect(option.rect, QColor("#181818"))
            if is_preview:
                self._draw_stop_icon(painter, option.rect)
            else:
                painter.setPen(QColor("#333333"))
                mid_y = option.rect.center().y()
                painter.drawLine(option.rect.left() + 2, mid_y,
                                 option.rect.right() - 2, mid_y)
            self._paint_hover_tint(painter, option.rect, index)
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

        self._paint_hover_tint(painter, option.rect, index)

    def _paint_hover_tint(self, painter: QPainter, rect, index) -> None:
        """Overlay the drag-reorder hover highlight (see _RowHoverDelegate)."""
        table = self.parent()
        if table is not None and index.row() == getattr(table, "_hover_row", -1):
            painter.save()
            painter.fillRect(rect, _ROW_HOVER_TINT)
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


class _RowHoverDelegate(QStyledItemDelegate):
    """
    Paints the drag-reorder hover highlight on every central-table column
    except the waveform one (which draws its own, in _WaveformDelegate — see
    _paint_hover_tint there). A plain per-item setBackground() doesn't work
    here: once a stylesheet rule targets QTableWidget::item (see style.py),
    Qt's style engine takes over background painting entirely and ignores
    each item's own background role.
    """

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        table = self.parent()
        if table is not None and index.row() == getattr(table, "_hover_row", -1):
            painter.save()
            painter.fillRect(option.rect, _ROW_HOVER_TINT)
            painter.restore()


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by a numeric UserRole+1 value when set."""
    _SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __lt__(self, other: "QTableWidgetItem") -> bool:
        a = self.data(self._SORT_ROLE)
        b = other.data(self._SORT_ROLE)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


class _CentralTrackTable(QTableWidget):
    """
    Central track table — lists either every track in the Editor library
    ("all tracks" mode, nothing selected) or a single case's/device's tracks.

    Supports dragging selected rows out as file locations (dropped onto a
    case in the Editor panel's case list to add them there) and accepting
    drops of files/folders from the file manager for import. The delete key and
    right-click remove tracks from the open case in case_mode, and unassigned
    tracks from the library in library_mode.
    """
    files_dropped = pyqtSignal(list)                  # list[str] local file/folder paths
    remove_from_case_requested = pyqtSignal(list)     # list[int] indexes into the case's tracks
    remove_from_library_requested = pyqtSignal(list)  # list[str] locations
    reorder_requested = pyqtSignal(list)              # list[str] locations, new case order
    reorder_blocked = pyqtSignal()                    # drag attempted while sorted by another column

    def __init__(self, rows: int = 0, cols: int = 0, parent=None):
        super().__init__(rows, cols, parent)
        self._tracks: list = []
        self._case_mode: bool = False
        self._library_mode: bool = False
        self._hover_row: int = -1
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        # Paints the drag hover/reorder highlight; the waveform column (2)
        # paints its own copy of the same tint via _WaveformDelegate.
        self.setItemDelegate(_RowHoverDelegate(self))

    def set_tracks(self, tracks: list) -> None:
        self._tracks = tracks
        self._hover_row = -1

    def set_case_mode(self, case_mode: bool) -> None:
        self._case_mode = case_mode

    def set_library_mode(self, library_mode: bool) -> None:
        self._library_mode = library_mode

    def _reorder_allowed(self) -> bool:
        """A drag only means something in running order: any other sort would be saved instead."""
        header = self.horizontalHeader()
        return (header.sortIndicatorSection() == 0
                and header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder)

    def _row_location(self, row: int) -> "str | None":
        """
        The location for a given VISUAL row, resolved through the row's
        stored original-index (column 0's UserRole) rather than indexing
        self._tracks directly — necessary because sorting by a column
        reorders rows on screen without reordering self._tracks itself.
        """
        item0 = self.item(row, 0)
        if item0 is None:
            return None
        orig = item0.data(Qt.ItemDataRole.UserRole)
        if orig is None or not (0 <= orig < len(self._tracks)):
            return None
        return self._tracks[orig].location

    def _track_index(self, row: int) -> "int | None":
        """The index into self._tracks for a given VISUAL row (see _row_location)."""
        item0 = self.item(row, 0)
        return item0.data(Qt.ItemDataRole.UserRole) if item0 is not None else None

    def _selected_track_indexes(self) -> list:
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        indexes = (self._track_index(r) for r in rows)
        return [i for i in indexes if i is not None]

    def _selected_locations(self) -> list:
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        locations = (self._row_location(r) for r in rows)
        return [loc for loc in locations if loc is not None]

    def startDrag(self, supportedActions) -> None:
        locations = self._selected_locations()
        if not locations:
            return
        mime = QMimeData()
        mime.setData(MIME_LOCATIONS, "\n".join(locations).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _set_hover_row(self, row: int) -> None:
        if row == self._hover_row:
            return
        self._hover_row = row
        self.viewport().update()

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasUrls() or (self._case_mode and mime.hasFormat(MIME_LOCATIONS)):
            if not mime.hasUrls() and not self._reorder_allowed():
                self.reorder_blocked.emit()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        mime = event.mimeData()
        if self._case_mode and mime.hasFormat(MIME_LOCATIONS):
            if not self._reorder_allowed():
                self._set_hover_row(-1)
                event.ignore()
                return
            event.acceptProposedAction()
            self._set_hover_row(self.indexAt(event.position().toPoint()).row())
        elif mime.hasUrls():
            event.acceptProposedAction()
            self._set_hover_row(-1)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_hover_row(-1)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._set_hover_row(-1)
        mime = event.mimeData()
        if self._case_mode and mime.hasFormat(MIME_LOCATIONS):
            if not self._reorder_allowed():
                self.reorder_blocked.emit()
                event.ignore()
                return
            pos = event.position().toPoint()
            index = self.indexAt(pos)
            row = index.row()
            if row != -1 and pos.y() > self.visualRect(index).center().y():
                row += 1   # dropped on the lower half of the row -> insert after it
            self._emit_reorder(row)
            event.acceptProposedAction()
        elif mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _emit_reorder(self, insert_before_visual_row: int) -> None:
        """
        Reassemble the case's track order: take the on-screen order, pull the
        selected rows out, and reinsert them (keeping their relative order) at the
        drop position. Works on rows rather than locations, so that a track in the
        case twice keeps both copies.
        """
        dragged = self._selected_track_indexes()
        if not dragged:
            return
        dragged_set = set(dragged)
        visual = [self._track_index(row) for row in range(self.rowCount())]
        if insert_before_visual_row == -1:
            insert_before_visual_row = len(visual)
        insert_at = sum(1 for i in visual[:insert_before_visual_row] if i not in dragged_set)
        remaining = [i for i in visual if i not in dragged_set]
        new_order = remaining[:insert_at] + dragged + remaining[insert_at:]
        if new_order != list(range(len(self._tracks))):
            self.reorder_requested.emit([self._tracks[i].location for i in new_order])

    def _request_remove(self) -> None:
        if self._case_mode:
            indexes = self._selected_track_indexes()
            if indexes:
                self.remove_from_case_requested.emit(indexes)
        elif self._library_mode:
            locations = self._selected_locations()
            if locations:
                self.remove_from_library_requested.emit(locations)

    def keyPressEvent(self, event) -> None:
        if (self._case_mode or self._library_mode) and event.key() in _REMOVE_TRACK_KEYS:
            self._request_remove()
            return
        super().keyPressEvent(event)

    def _on_context_menu(self, pos) -> None:
        if not (self._case_mode or self._library_mode) or not self.selectedIndexes():
            return
        menu = QMenu(self)
        action = menu.addAction("Remove from Case" if self._case_mode else "Remove from Library")
        if menu.exec(self.viewport().mapToGlobal(pos)) == action:
            self._request_remove()


from core.audio_formats import ALL_AUDIO_EXTENSIONS, is_supported
from core.device_backup import backup_device_db
from core.pacemaker_writer import PacemakerWriter, track_details_differ
from core import sync_map as _sync_map
from core import sync_state
from core.device_finder import (
    find_device_db, ensure_editor_db, default_editor_db_path, device_backup_dir,
    device_root, device_music_root, find_orphaned_device_files, is_on_device,
)
from core.m3u8_reader import load_m3u8_tracks
from core.file_import import expand_dropped_paths, read_tracks_from_files
from ui.playlist_tree import PlaylistTreeWidget
from ui.sync_panel import SyncPanel, SyncQueueItem
from ui.pacemaker_panel import PacemakerLibraryPanel, MIME_LOCATIONS, _fmt_bytes
from ui.player_bar import PlayerBar


# ---------------------------------------------------------------------------
# Dialog helpers
# ---------------------------------------------------------------------------

# Room to leave on the device for its database to grow
_DEVICE_SPACE_MARGIN = 50_000_000

DUPLICATES_PROMPT = "There are duplicates being added to the playlist. Add duplicate tracks or skip?"
_ADD_DUPLICATES, _SKIP_DUPLICATES = "Add", "Skip"


def _ask(parent, icon, title: str, text: str, buttons: list, default: "str | None" = None) -> str:
    """Ask with named buttons, given as [(label, role)]. Returns the label chosen, "" if none."""
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText(text)
    made = {}
    for label, role in buttons:
        button = box.addButton(label, role)
        made[button] = label
        if label == default:
            box.setDefaultButton(button)
    box.exec()
    return made.get(box.clickedButton(), "")


def _ask_add_duplicates(parent) -> bool:
    """True to add the duplicate tracks, False to skip them."""
    return _ask(
        parent, QMessageBox.Icon.Question, "Duplicate Tracks", DUPLICATES_PROMPT,
        [(_ADD_DUPLICATES, QMessageBox.ButtonRole.AcceptRole),
         (_SKIP_DUPLICATES, QMessageBox.ButtonRole.RejectRole)],
        default=_SKIP_DUPLICATES,
    ) == _ADD_DUPLICATES


def _has_repeats(items: list) -> bool:
    return len(set(items)) != len(items)


def _without_repeated_tracks(tracks: list) -> list:
    seen = set()
    kept = []
    for track in tracks:
        if track.location not in seen:
            seen.add(track.location)
            kept.append(track)
    return kept


def _list_files(paths: list, limit: int = 10) -> str:
    """Bullet list of entries ("path" or "path: reason"), showing just the file names."""
    lines = [f"  • {os.path.basename(p.replace(chr(92), '/'))}" for p in paths[:limit]]
    if len(paths) > limit:
        lines.append(f"  …and {len(paths) - limit} more")
    return "\n".join(lines)


def _unsupported_note(paths: list) -> str:
    return (
        f"{len(paths)} file(s) weren't imported because this format isn't supported "
        f"(use MP3, FLAC, M4A, MP4, AAC, WAV or AIFF):\n{_list_files(paths)}"
    )


def _backup_device_db(parent, device_db: str) -> "tuple[bool, str]":
    """Back up a device DB before changing it. Returns (go ahead, backup path)."""
    try:
        return True, backup_device_db(device_db, device_backup_dir())
    except Exception as e:
        _logger.log.error("Device DB backup failed: %s", e, exc_info=True)
        reply = QMessageBox.warning(
            parent, "Couldn't Back Up Pacemaker Database",
            f"The Pacemaker's database couldn't be backed up to this computer.\n\n{e}\n\n"
            "Continue without a backup?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes, ""


class _WorkProgressDialog(QProgressDialog):
    """
    Progress for work on a background thread. Escape does nothing, and closing it asks
    the main window instead, so a Quit can't silently end the work: the main window warns
    first (see closeEvent).
    """

    def reject(self) -> None:
        pass

    def closeEvent(self, event) -> None:
        event.ignore()
        if self.parent() is not None:
            self.parent().close()


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
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self):
        try:
            total_tracks = sum(len(op["tracks"]) for op in self._operations)
            done = 0

            with PacemakerWriter(self._db_path) as writer:
                for op in self._operations:
                    if self._cancelled:
                        self.finished.emit(True, "Import stopped.")
                        return
                    name = op["playlist_name"]
                    tracks = op["tracks"]
                    self.progress.emit(done, total_tracks, f"Adding: {name}")

                    def cb(i, n, _done=done, _total=total_tracks, _name=name):
                        self.progress.emit(_done + i, _total, f"Adding: {_name} ({i}/{n})")

                    writer.add_playlist(name, tracks, cb)
                    done += len(tracks)

            self.finished.emit(True, "Import complete.")
        except Exception as e:
            self.finished.emit(False, str(e))


# ---------------------------------------------------------------------------
# Background worker: drag-and-drop file import (Explorer → Editor library,
# optionally straight into a case)
# ---------------------------------------------------------------------------

class FileImportWorker(QObject):
    progress = pyqtSignal(int, int, str)     # current, total, status message
    finished = pyqtSignal(bool, str, list)    # success, message, files that stopped the import

    def __init__(self, db_path: str, files: list[str], case_id: "int | None",
                 add_duplicates: bool = False, new_case_name: str = ""):
        super().__init__()
        self._db_path = db_path
        self._files = files
        self._case_id = case_id
        self._add_duplicates = add_duplicates
        self._new_case_name = new_case_name
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(0, 0, "Checking files…")
            tracks, problems = read_tracks_from_files(self._files)
            if problems:
                # Stop before anything is written, so the import is all or nothing
                self.finished.emit(False, "Import stopped.", problems)
                return
            total = len(tracks)

            with PacemakerWriter(self._db_path) as writer:
                if self._new_case_name:
                    self._case_id = writer.create_case(self._new_case_name)
                for i, track in enumerate(tracks):
                    if self._cancelled:
                        self.finished.emit(True, f"Import stopped after {i} track(s).", [])
                        return
                    label = track.title or os.path.basename(track.location)
                    self.progress.emit(i, total, f"Importing: {label}")
                    track_id = writer.insert_or_get_track(track)
                    if self._case_id is not None and (
                        self._add_duplicates or not writer.is_track_in_case(self._case_id, track_id)
                    ):
                        writer.link_track_to_case(self._case_id, track_id)
                    self.progress.emit(i + 1, total, f"Importing: {label}")

            self.finished.emit(True, f"Imported {total} track(s).", [])
        except Exception as e:
            self.finished.emit(False, str(e), [])


# ---------------------------------------------------------------------------
# Background worker: Editor → Device sync (add new + remove unchecked)
# ---------------------------------------------------------------------------

class _SyncCancelled(Exception):
    """The user asked to stop. Everything finished so far stays on the device."""


class _DeviceFull(Exception):
    """A copy failed because the device has no space left."""


def _needs_copy(src_path: str, dest: str) -> bool:
    """True unless dest already holds this version of src_path. Copies keep the source's date."""
    if not os.path.exists(dest) or os.path.getsize(dest) != os.path.getsize(src_path):
        return True
    newer_by = os.path.getmtime(src_path) - os.path.getmtime(dest)
    # FAT and exFAT keep times to 2 seconds, in local time, so a clock change looks like an hour
    return newer_by > 2 and abs(newer_by - 3600) > 2


class DeviceSyncWorker(QObject):
    progress = pyqtSignal(int, int, str)   # current, total, status message
    finished = pyqtSignal(bool, str)

    def __init__(self, editor_db: str, device_db: str,
                 to_add: list, to_remove: list, to_update: list, sync_map: dict,
                 library_id: "int | None" = None, erase_all: bool = False,
                 skip_sources: "set[str] | None" = None):
        super().__init__()
        self._editor_db = editor_db
        self._device_db = device_db
        self._to_add = to_add        # [{"case_id", "name", "track_count"}]
        self._to_remove = to_remove  # [{"editor_case_id", "device_case_id", "name"}]
        self._to_update = to_update  # [{"case_id", "device_case_id", "name", "track_count"}]
        self._sync_map = dict(sync_map)  # working copy, saved to the device as it changes
        self._library_id = library_id
        self._erase_all = erase_all      # also clear out tracks in no case
        self._skip_sources = skip_sources or set()
        self._cancelled = False
        self._done = 0
        self._total_tracks = 0
        self.missing = 0                 # source file not found, and no copy on the device
        self.failures: list[str] = []    # "name: reason" for tracks that couldn't be copied
        self.was_cancelled = False
        self.device_lost = False         # the device disappeared part-way through

    def cancel(self) -> None:
        """Stop after the current track. Safe to call from any thread."""
        self._cancelled = True

    @staticmethod
    def _local_path(location: str, device_db: str) -> str:
        """
        Where a device DB location lives on disk.
        /pmdb_tracks/a/b/xxxxxxxx.mp3  ->  <device root>/.Pacemaker/Music/a/b/xxxxxxxx.mp3
        """
        if location.startswith("/pmdb_tracks/"):
            rel = location[len("/pmdb_tracks/"):].replace("/", os.sep)
            return os.path.join(device_music_root(device_db), rel)
        return location  # old-format path stored as a local path

    @classmethod
    def expected_locations(cls, tracks: list, device_db: str,
                           skip_sources: "set[str] | None" = None) -> list[str]:
        """
        The device locations a case should hold, in order. A track whose source file
        can't be found stays if its copy is already on the device, so disconnecting a
        music drive doesn't make a sync strip those tracks from the case.
        """
        locations = []
        for track in tracks:
            if skip_sources and track.location in skip_sources:
                continue
            location = cls._pmdb_location(track.location)
            if os.path.exists(track.location) or os.path.exists(cls._local_path(location, device_db)):
                locations.append(location)
        return locations

    def _save_map(self) -> None:
        """Record progress on the device as it happens, so an interrupted sync isn't forgotten."""
        _sync_map.write(self._device_db, self._sync_map, self._library_id)

    def _push_track(self, track, device: PacemakerWriter) -> "int | None":
        """Put one track on the device. Returns its device track_id, or None if it wasn't."""
        name = os.path.basename(track.location.replace("\\", "/"))
        if track.location in self._skip_sources:
            self.failures.append(f"{name}: another track would have overwritten it")
            return None
        location = self._pmdb_location(track.location)
        local_copy = self._local_path(location, self._device_db)
        date_source = track.location
        if os.path.exists(track.location):
            try:
                self._copy_track(track.location, self._device_db)
                _logger.log.debug("  Copied: %s -> %s", track.location, location)
            except OSError as e:
                _logger.log.error("  Copy failed for %s: %s", track.location, e, exc_info=True)
                if not os.path.exists(self._device_db):
                    raise   # the device has gone: end the sync
                if e.errno == errno.ENOSPC:
                    raise _DeviceFull(
                        f"The Pacemaker ran out of space while copying {name}. "
                        "What finished so far has been kept."
                    ) from e
                self.failures.append(f"{name}: {e.strerror or e}")
                return None
        elif os.path.exists(local_copy):
            date_source = local_copy
        else:
            _logger.log.warning("  Track file not found, skipping: %s", track.location)
            self.missing += 1
            return None
        return device.insert_or_get_track(
            dataclasses.replace(track, location=location), date_source=date_source
        )

    @staticmethod
    def _delete_device_files(locations: list[str], device_db: str) -> None:
        """Delete audio files (and their .str companions) for the given pmdb locations."""
        for loc in locations:
            local_path = DeviceSyncWorker._local_path(loc, device_db)
            if not is_on_device(local_path, device_db):
                continue   # an old-format location pointing at the computer's own files
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
                str_path = os.path.splitext(local_path)[0] + ".str"
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
        crc = f"{zlib.crc32(src_path.encode()) & 0xFFFFFFFF:08x}"
        ext = os.path.splitext(src_path)[1].lower()
        return f"/pmdb_tracks/{crc[0]}/{crc[1]}/{crc}{ext}"

    @classmethod
    def _copy_track(cls, src_path: str, device_db: str) -> tuple[str, str]:
        """
        Copy src_path to the correct location under <device root>/.Pacemaker/Music/X/Y/
        and create the companion 0-byte .str file the firmware expects. The copy goes to a
        temporary name first, so a failure can't leave a cut-off track behind.
        Returns (local_dest_path, pmdb_location_string).
        """
        pmdb_loc = cls._pmdb_location(src_path)
        dest = cls._local_path(pmdb_loc, device_db)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if _needs_copy(src_path, dest):
            temp = dest + ".part"
            try:
                shutil.copy2(src_path, temp)
                os.replace(temp, dest)
            except OSError:
                try:
                    os.remove(temp)
                except OSError:
                    pass
                raise

        # Create companion 0-byte .str file (firmware requires this)
        str_path = os.path.splitext(dest)[0] + ".str"
        if not os.path.exists(str_path):
            open(str_path, "wb").close()

        return dest, pmdb_loc

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise _SyncCancelled()

    def _push_case_tracks(self, tracks: list, device: PacemakerWriter, on_pushed=None) -> list[int]:
        track_ids = []
        for track in tracks:
            self._check_cancelled()
            self.progress.emit(
                self._done, max(self._total_tracks, 1),
                f"Copying: {os.path.basename(track.location.replace(chr(92), '/'))}"
            )
            track_id = self._push_track(track, device)
            if track_id is not None:
                track_ids.append(track_id)
                if on_pushed:
                    on_pushed(track_id)
            self._done += 1
        return track_ids

    def _remove_cases(self) -> int:
        removed = 0
        deleted_locs: list[str] = []
        with PacemakerWriter(self._device_db) as device:
            for r in self._to_remove:
                self._check_cancelled()
                _logger.log.info("  Removing case: %s (device id %s)", r["name"], r["device_case_id"])
                self.progress.emit(0, 1, f"Removing: {r['name']}")
                locs = device.get_case_track_locations(r["device_case_id"])
                deleted_locs.extend(device.remove_playlist(r["device_case_id"], list(locs)))
                if r.get("editor_case_id") is not None:
                    self._sync_map.pop(str(r["editor_case_id"]), None)
                self._save_map()
                removed += 1
            if self._erase_all:
                deleted_locs.extend(device.delete_all_unreferenced_tracks())
        self._delete_device_files(deleted_locs, self._device_db)
        return removed

    def _update_case(self, case: dict, tracks: list, device: PacemakerWriter) -> None:
        device_case_id = case["device_case_id"]
        _logger.log.info("  Updating case: %s (device id %s)", case["name"], device_case_id)
        old_locations = device.get_case_track_locations(device_case_id)
        failures_before = len(self.failures)
        track_ids = self._push_case_tracks(tracks, device)
        device.rename_case(device_case_id, case["name"])
        if len(self.failures) > failures_before:
            return   # keep its current tracks rather than lose the ones that failed
        device.reorder_case_tracks(device_case_id, track_ids)
        dropped = old_locations - device.get_case_track_locations(device_case_id)
        self._delete_device_files(device.delete_orphan_tracks(list(dropped)), self._device_db)

    def _add_case(self, case: dict, tracks: list, device: PacemakerWriter) -> int:
        """Returns how many tracks went on. Recorded first, so a stopped sync can finish it later."""
        device_case_id = device.create_case(case["name"])
        self._sync_map[str(case["case_id"])] = device_case_id
        self._save_map()
        track_ids = self._push_case_tracks(
            tracks, device, on_pushed=lambda track_id: device.link_track_to_case(device_case_id, track_id)
        )
        if tracks and not track_ids:
            # Nothing could be copied: don't leave an empty case claiming to be synced
            device.delete_case(device_case_id)
            del self._sync_map[str(case["case_id"])]
            self._save_map()
        return len(track_ids)

    def _run_sync(self) -> list[str]:
        """Do the work and return the parts of the summary message."""
        parts = []
        if self._to_remove:
            _logger.log.info("Device sync: removing %d case(s)", len(self._to_remove))
            parts.append(f"Removed {self._remove_cases()} case(s) from device")

        cases = self._to_update + self._to_add
        if cases:
            with PacemakerWriter(self._editor_db) as editor:
                tracks = {c["case_id"]: editor.get_case_tracks_as_trackinfo(c["case_id"]) for c in cases}
            self._total_tracks = sum(len(t) for t in tracks.values())
            _logger.log.info(
                "Device sync: %d case(s) to update, %d to add, %d tracks",
                len(self._to_update), len(self._to_add), self._total_tracks,
            )
            added = copied = 0
            with PacemakerWriter(self._device_db) as device:
                for case in self._to_update:
                    self._update_case(case, tracks[case["case_id"]], device)
                for case in self._to_add:
                    count = self._add_case(case, tracks[case["case_id"]], device)
                    copied += count
                    if count or not tracks[case["case_id"]]:
                        added += 1
            if self._to_add:
                parts.insert(0, f"Added {added} case(s) ({copied} tracks copied)")
            if self._to_update:
                parts.append(f"Updated {len(self._to_update)} case(s)")
        self._save_map()
        return parts

    def run(self):
        try:
            parts = self._run_sync()
        except _SyncCancelled:
            self.was_cancelled = True
            try:
                self._save_map()
            except OSError as e:
                _logger.log.error("Could not save sync record: %s", e)
            self.finished.emit(True, "Sync cancelled. What finished is kept: click Sync to carry on.")
            return
        except Exception as e:
            self.device_lost = not os.path.exists(self._device_db)
            _logger.log.error("Device sync error: %s", e, exc_info=True)
            self.finished.emit(False, str(e))
            return

        if self.missing:
            parts.append(f"{self.missing} track(s) skipped (file not found)")
        if self.failures:
            parts.append(f"{len(self.failures)} track(s) couldn't be copied")
        msg = ".  ".join(parts) + "." if parts else "Device already in sync."
        _logger.log.info("Device sync complete: %s", msg)
        self.finished.emit(True, msg)


# ---------------------------------------------------------------------------
# Repair Device Database dialog
# ---------------------------------------------------------------------------

class RepairDeviceDbDialog(QDialog):
    """
    Scans the device music.db for tracks whose location no longer exists,
    matches them by filename against audio files found in a chosen folder,
    and rewrites the location column with the correct device path.
    """

    _AUDIO_EXTS = ALL_AUDIO_EXTENSIONS

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
            # Music lives at <device root>/.Pacemaker/Music — default scan root there
            music_root = device_music_root(device_db)
            if os.path.isdir(music_root):
                self._scan_root = music_root
                self._scan_edit.setText(music_root)
            else:
                # Fallback to device root
                root = device_root(device_db)
                if os.path.isdir(root):
                    self._scan_root = root
                    self._scan_edit.setText(root)

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
        music_root = device_music_root(path)
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

        music_root = device_music_root(self._device_db)

        def pmdb_to_local(loc: str) -> str:
            """Convert /pmdb_tracks/X/Y/hash.mp3 -> <device root>/.Pacemaker/Music/X/Y/hash.mp3"""
            rel = loc.replace("/pmdb_tracks/", "").replace("/", os.sep)
            return os.path.join(music_root, rel)

        def is_valid_device_path(loc: str) -> bool:
            """A location is valid if it's a pmdb path and the file exists on the device."""
            if not loc:
                return False
            if loc.startswith("/pmdb_tracks/"):
                return os.path.exists(pmdb_to_local(loc))
            # A local path on the device — legacy check
            return is_on_device(loc, self._device_db) and os.path.exists(loc)

        self._fixes = []
        unmatched = []
        outside_music_folder = []
        music_prefix = os.path.normcase(music_root) + os.sep

        for row in rows:
            loc = row["location"] or ""
            if is_valid_device_path(loc):
                continue  # path resolves to a real file on the device — skip

            basename = os.path.basename(loc).lower() if loc else ""
            if basename and basename in file_index:
                found_path = file_index[basename]
                # Only a file inside .Pacemaker/Music has a location the firmware can load.
                # A computer path must never go in the device DB.
                if not os.path.normcase(found_path).startswith(music_prefix):
                    outside_music_folder.append(loc)
                    continue
                rel = found_path[len(music_root):].lstrip(os.sep).replace(os.sep, "/")
                self._fixes.append({
                    "track_id": row["track_id"],
                    "old": loc,
                    "new": f"/pmdb_tracks/{rel}",
                })
            elif loc:
                unmatched.append(loc)

        total_broken = len(self._fixes) + len(unmatched) + len(outside_music_folder)
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
        if outside_music_folder:
            lines.append(
                f"  \u2022 {len(outside_music_folder)} found outside the Pacemaker's Music folder, "
                f"so can't be fixed (only files in {music_root} can be used)"
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
        if not _backup_device_db(self, self._device_db)[0]:
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

        try:
            tracks, problems, unsupported = load_m3u8_tracks(self._m3u8_path)
        except OSError as e:
            progress.close()
            QMessageBox.critical(self, "Can't Read Playlist", f"The playlist couldn't be read:\n\n{e}")
            return
        progress.close()

        if problems:
            QMessageBox.warning(
                self, "Import Stopped",
                f"{len(problems)} file(s) in this playlist would cause problems, so nothing "
                f"was imported:\n\n{_list_files(problems)}\n\n"
                "Fix or remove these files, or take them out of the playlist, then try again."
            )
            return

        if not tracks:
            text = "No valid tracks found in the M3U8 file."
            if unsupported:
                text += "\n\n" + _unsupported_note(unsupported)
            QMessageBox.warning(self, "No Tracks", text)
            return

        if _has_repeats([t.location for t in tracks]) and not _ask_add_duplicates(self):
            tracks = _without_repeated_tracks(tracks)

        msg = f"Found {len(tracks)} track(s)."
        if unsupported:
            msg += "\n\n" + _unsupported_note(unsupported)
        msg += f"\n\nImport as case: \"{case_name}\"?"

        reply = QMessageBox.question(self, "Confirm Import", msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with PacemakerWriter(self._db_path) as writer:
                case_name = writer.unique_case_name(case_name)
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
    _device_scan_done = pyqtSignal(str, str)   # path scanned for, path found

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
        self._import_thread: QThread | None = None
        self._import_progress_dlg: QProgressDialog | None = None
        self._import_unsupported: list[str] = []
        self._cleanup_thread: QThread | None = None
        self._cleanup_worker = None
        self._worker = None
        self._import_worker = None
        self._push_worker = None
        self._device_syncing = False
        self._cleaning_up = False
        self._quit_when_idle = False
        self._asking_to_quit = False
        self._device_scan_running = False
        self._device_locations: set[str] = set()
        self._last_backup = ""
        self._player_follows_table = True

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
            self._preview_player.errorOccurred.connect(
                lambda _error, text: self.statusBar().showMessage(f"Couldn't play this track: {text}")
            )
        else:
            self._preview_player = None
            self._preview_audio_out = None

        self._build_menu()
        self._build_ui()
        self._load_rekordbox()
        self._autodetect_pacemaker_dbs()

        # Every 2 s, on a background thread: look for a device, or check it's still there.
        self._device_scan_done.connect(self._on_device_scan_done)
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

        reload_action = QAction("Reload Rekordbox Library", self)
        reload_action.triggered.connect(self._reload_rekordbox)
        file_menu.addAction(reload_action)

        import_action = QAction("Import from M3U8…", self)
        import_action.triggered.connect(self._open_m3u8_import)
        file_menu.addAction(import_action)

        repair_action = QAction("Repair Device Database…", self)
        repair_action.triggered.connect(self._open_repair_dialog)
        file_menu.addAction(repair_action)

        cleanup_action = QAction("Clean Up Device Files…", self)
        cleanup_action.triggered.connect(self._cleanup_orphaned_device_files)
        file_menu.addAction(cleanup_action)
        # None of these may run alongside a sync
        self._sync_blocked_actions = [reload_action, import_action, repair_action, cleanup_action]

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
            accept_track_drops=True,
            show_new_case=True,
        )
        self._editor_panel.refresh_requested.connect(self._load_editor_library)
        self._editor_panel.delete_requested.connect(self._on_delete_editor_case)
        self._editor_panel.rename_requested.connect(self._on_rename_editor_case)
        self._editor_panel.inline_rename_committed.connect(self._on_inline_rename_editor_case)
        self._editor_panel.push_requested.connect(self._confirm_and_sync_to_device)
        self._editor_panel.case_selected.connect(self._on_editor_case_selected)
        self._editor_panel.new_case_requested.connect(self._on_new_case_requested)
        self._editor_panel.tracks_dropped_on_case.connect(self._on_tracks_dropped_on_case)
        self._editor_panel.files_dropped_on_case.connect(self._on_files_dropped_on_case)
        self._editor_panel.files_dropped_on_empty.connect(self._on_files_dropped_on_empty)

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
        self._device_panel.track_tab_play_requested.connect(self._on_device_track_tab_play)
        self._device_panel.track_tab_selected.connect(self._on_device_track_tab_selected)

        # ── Player bar (top, like Rekordbox) ──────────────────────────────
        self._player_bar = PlayerBar()
        self._player_bar.track_changed.connect(self._on_player_track_changed)
        self._player_bar.track_changed.connect(lambda _: self._stop_preview())
        self._player_bar.playback_error.connect(self.statusBar().showMessage)

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
        rb_header = QHBoxLayout()
        rb_header.setContentsMargins(0, 0, 4, 0)
        rb_header.setSpacing(0)
        rb_header.addWidget(self._make_section_header("REKORDBOX LIBRARY"), stretch=1)
        reload_btn = QPushButton("Reload")
        reload_btn.setFixedHeight(20)
        reload_btn.setToolTip("Read the Rekordbox library again, to pick up new or changed playlists")
        reload_btn.clicked.connect(self._reload_rekordbox)
        rb_header.addWidget(reload_btn)
        rb_layout.addLayout(rb_header)
        rb_layout.addWidget(self._tree, stretch=1)
        file_manager = "Finder" if sys.platform == "darwin" else "File Explorer"
        self._no_rekordbox_note = QLabel(
            "No Rekordbox library found.\n\n"
            "You don't need one to use this app. Drag audio files or folders from "
            f"{file_manager} onto EDITOR LIBRARY to make a new case, or onto a case to add "
            "to it. You can also use File → Import from M3U8.\n\n"
            "If you install Rekordbox later, open it once, then click Reload."
        )
        self._no_rekordbox_note.setWordWrap(True)
        self._no_rekordbox_note.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._no_rekordbox_note.setContentsMargins(10, 10, 10, 10)
        self._no_rekordbox_note.hide()
        rb_layout.addWidget(self._no_rekordbox_note, stretch=1)
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
        from ui.style import BG_PANEL, BORDER, FG_SECONDARY, ACCENT

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
        self._all_tracks_btn = QPushButton("← All Tracks")
        self._all_tracks_btn.setFlat(True)
        self._all_tracks_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._all_tracks_btn.setStyleSheet(
            f"QPushButton {{ color: {ACCENT}; border: none; background: transparent; "
            f"font-size: 11px; padding: 0; }}"
            f"QPushButton:hover {{ text-decoration: underline; }}"
        )
        self._all_tracks_btn.clicked.connect(self._on_show_all_tracks_clicked)
        self._all_tracks_btn.setVisible(False)
        hdr_layout.addWidget(self._all_tracks_btn)
        self._track_view_lbl = QLabel("Select a case to view tracks")
        self._track_view_lbl.setStyleSheet(f"color: {FG_SECONDARY}; font-size: 11px;")
        hdr_layout.addWidget(self._track_view_lbl)
        hdr_layout.addStretch()
        layout.addWidget(hdr)

        # Central track table  cols: #  ★  ~wave  Title  Artist  BPM  Key  Time  Genre  Cases
        self._track_table = _CentralTrackTable(0, 10)
        self._track_table.setHorizontalHeaderLabels(
            ["#", "★", "~", "Title", "Artist", "BPM", "Key", "Time", "Genre", "Cases"]
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
            QHeaderView.ResizeMode.Fixed,    # Cases
        ]):
            hh.setSectionResizeMode(col, mode)
        self._track_table.setColumnWidth(0, 36)
        self._track_table.setColumnWidth(1, 26)
        self._track_table.setColumnWidth(2, 160)   # waveform preview
        self._track_table.setColumnWidth(5, 52)
        self._track_table.setColumnWidth(6, 58)
        self._track_table.setColumnWidth(7, 56)
        self._track_table.setColumnWidth(8, 100)
        self._track_table.setColumnWidth(9, 48)
        self._track_table.setColumnHidden(9, True)   # only shown in "all tracks" mode
        self._wave_delegate = _WaveformDelegate(self._track_table)
        self._track_table.setItemDelegateForColumn(2, self._wave_delegate)

        self._track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._track_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._track_table.verticalHeader().setVisible(False)
        self._track_table.setAlternatingRowColors(True)
        self._track_table.setSortingEnabled(True)
        self._track_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._track_table.verticalHeader().setDefaultSectionSize(22)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)   # lock waveform col
        self._track_table.itemDoubleClicked.connect(self._on_track_double_clicked)
        self._track_table.viewport().installEventFilter(self)
        self._track_table.files_dropped.connect(self._on_central_files_dropped)
        self._track_table.remove_from_case_requested.connect(self._on_remove_from_case_requested)
        self._track_table.reorder_requested.connect(self._on_reorder_requested)
        self._track_table.remove_from_library_requested.connect(self._on_remove_from_library_requested)
        self._track_table.reorder_blocked.connect(
            lambda: self.statusBar().showMessage(
                "To change the running order, sort by the # column first (click its header)."
            )
        )
        self._wave_items: dict = {}   # track index -> its waveform cell, wherever sorting has moved it

        # Store tracks and source for playback
        self._track_table_tracks: list[TrackInfo] = []
        self._track_table_source: str = ""   # "editor" | "editor_all" | "device" | ""
        self._track_table_case_id: "int | None" = None   # editor case open in the table, if any

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
        # ensure_editor_db() creates a fresh, empty Editor DB at the default
        # per-platform location if one doesn't exist yet (e.g. first run on a
        # machine that never had the real Tonium Editor installed, or any
        # first run at all on macOS).
        try:
            editor_path = ensure_editor_db()
            with PacemakerWriter(editor_path) as writer:
                writer.get_all_cases()   # a damaged file only shows up when it's read
        except Exception as e:
            _logger.log.error("Could not open the Editor library: %s", e, exc_info=True)
            self._warn_editor_db_problem(e)
            editor_path = ""
        if editor_path:
            self._panel.set_db_path(editor_path)   # triggers _on_editor_db_changed

        device_path = find_device_db()
        if device_path:
            self._connect_device(device_path)
            self.statusBar().showMessage(
                f"Device found: {device_path}"
                + (f"  |  Editor: {editor_path}" if editor_path else "")
            )

    def _warn_editor_db_problem(self, error: Exception) -> None:
        path = default_editor_db_path()
        folder = os.path.dirname(path)
        text = str(error).lower()
        if isinstance(error, PermissionError) or "readonly" in text or "permission" in text:
            fixes = [
                f"Check you have permission to change this folder: {folder}",
                "Close the Tonium Editor if it's open, then reopen this app.",
            ]
        elif isinstance(error, OSError) and error.errno == errno.ENOSPC or "disk is full" in text:
            fixes = ["Your computer's disk is full. Free up some space, then reopen this app."]
        elif "not a database" in text or "malformed" in text or "no such table" in text:
            fixes = [
                f"The library file looks damaged. Move or rename it, and this app will make a new, "
                f"empty one next time it opens: {path}",
                "Your Rekordbox library and your Pacemaker aren't affected.",
            ]
        else:
            fixes = [
                "Close the Tonium Editor if it's open, then reopen this app.",
                f"Check this folder exists and you can change it: {folder}",
                "Restart your computer and try again.",
            ]
        QMessageBox.critical(
            self, "Couldn't Open Editor Library",
            f"The app couldn't open or create your Editor Library, so you won't be able to "
            f"import or sync until this is fixed.\n\nDetails: {error}\n\nTry this:\n"
            + "\n".join(f"  • {fix}" for fix in fixes)
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
        except RekordboxNotFoundError as e:
            # Not a fault: the app also works from dropped files and M3U8 playlists
            _logger.log.info("No Rekordbox library: %s", e)
            self._tree.hide()
            self._no_rekordbox_note.show()
            self.statusBar().showMessage("No Rekordbox library found.")
        except Exception as e:
            _logger.log.error("Could not load Rekordbox: %s", e, exc_info=True)
            title = e.title if isinstance(e, RekordboxError) else "Couldn't Read Rekordbox Library"
            self.statusBar().showMessage(title)
            QMessageBox.warning(
                self, title,
                f"{e}\n\n"
                "You can still add music with File → Import from M3U8, or by "
                "dragging audio files into the library."
            )

    def _refresh_tree(self):
        self._tree.load_tree(self._playlist_nodes, set())

    def _reload_rekordbox(self) -> None:
        """Re-read the Rekordbox library, keeping what's ticked and what's expanded."""
        ticked = set(self._tree.get_checked_playlist_ids())
        previous = self._rb_reader
        try:
            reader = RekordboxReader()
            nodes = reader.get_playlist_tree()
        except Exception as e:
            _logger.log.error("Could not reload Rekordbox: %s", e, exc_info=True)
            title = e.title if isinstance(e, RekordboxError) else "Couldn't Read Rekordbox Library"
            unchanged = "" if previous is None else "\n\nThe list hasn't been changed."
            QMessageBox.warning(self, title, f"{e}{unchanged}")
            return
        self._rb_reader = reader
        self._playlist_nodes = nodes
        self._no_rekordbox_note.hide()
        self._tree.show()
        self._tree.load_tree(nodes, ticked)
        self._player_bar.set_rekordbox_reader(reader)
        self._recompute_queue()
        if previous is not None:
            try:
                previous.close()
            except Exception:
                pass
        self.statusBar().showMessage("Rekordbox library reloaded.")

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

        Format:  folder segments joined with "_" (each stripped + 4 chars)
                 + "_" + playlist name (stripped, spaces removed, 12 chars)

        Example: ["2025", "08 - Aug", "IYKYK", "Deep or Heavy"]
              →  "2025_Aug_IYKY_DeeporHeavy"

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

        checked_ids = self._tree.get_checked_playlist_ids()
        if not checked_ids:
            QMessageBox.information(self, "Nothing to do", "No playlists are checked.")
            return

        path_map = self._build_playlist_path_map()
        operations = []
        unsupported: list[str] = []

        try:
            with PacemakerWriter(db_path) as writer:
                names_in_batch: set[str] = set()
                for pid in checked_ids:
                    node = self._find_node(pid)
                    if node is None:
                        continue
                    read = self._rb_reader.get_playlist_tracks(pid)
                    tracks = [t for t in read if is_supported(t.location)]
                    unsupported.extend(t.location for t in read if not is_supported(t.location))
                    if read and not tracks:
                        continue   # nothing in it could be imported
                    base_name = self._make_case_name(path_map.get(pid, [node.name]))
                    # Shortened names can collide, with existing cases or within this batch
                    case_name = writer.unique_case_name(base_name)
                    suffix = 2
                    while case_name.casefold() in names_in_batch:
                        case_name = writer.unique_case_name(f"{base_name}_{suffix}")
                        suffix += 1
                    names_in_batch.add(case_name.casefold())
                    operations.append({
                        "playlist_id": pid,
                        "playlist_name": case_name,
                        "tracks": tracks,
                    })
        except Exception as e:
            _logger.log.error("Could not read playlists for import: %s", e, exc_info=True)
            QMessageBox.critical(self, "Import Failed", f"The playlists couldn't be read:\n\n{e}")
            return

        if not operations:
            if unsupported:
                QMessageBox.warning(self, "Nothing to Import", _unsupported_note(unsupported))
            return

        if any(_has_repeats([t.location for t in op["tracks"]]) for op in operations):
            if not _ask_add_duplicates(self):
                for op in operations:
                    op["tracks"] = _without_repeated_tracks(op["tracks"])

        total_tracks = sum(len(o["tracks"]) for o in operations)
        text = (
            f"Add {len(operations)} playlist(s) ({total_tracks} tracks) to the Editor library?\n\n"
            "Existing cases are not affected."
        )
        if unsupported:
            text += "\n\n" + _unsupported_note(unsupported)
        reply = QMessageBox.question(
            self, "Confirm Import", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_sync(db_path, operations)

    def _run_sync(self, db_path: str, operations: list[dict]):
        total_tracks = sum(len(op["tracks"]) for op in operations)

        self._sync_progress_dlg = _WorkProgressDialog(
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
            self._sync_progress_dlg.hide()
            self._sync_progress_dlg.deleteLater()
            self._sync_progress_dlg = None
        self._end_thread(self._sync_thread)
        self._worker = None

        if success:
            _logger.log.info("Import to editor finished: %s", message)
            self._tree.uncheck_all()
            self._load_editor_library()
        else:
            _logger.log.error("Import to editor failed: %s", message)
            QMessageBox.critical(self, "Import Failed", f"An error occurred:\n\n{message}")

        self.statusBar().showMessage(message)
        self._quit_if_requested()

    # ------------------------------------------------------------------
    # Editor → Device sync  (Rekordbox-style: checked = on device)
    # ------------------------------------------------------------------

    def _load_sync_record(self, device_db: str) -> _sync_map.SyncRecord:
        """
        The device's sync record. A damaged one is set aside and rebuilt by matching case
        names, because otherwise the next Sync would see every case as new and add duplicates.
        """
        record = _sync_map.read(device_db)
        if record.status != _sync_map.CORRUPT:
            return record
        editor_db = self._panel.db_path
        if not editor_db:
            return _sync_map.SyncRecord(status=_sync_map.MISSING)
        _logger.log.warning("Sync record on %s is damaged: rebuilding it", device_db)
        try:
            with PacemakerWriter(editor_db) as editor, PacemakerWriter(device_db) as device:
                cases = _sync_map.rebuild_by_name(editor.get_all_cases(), device.get_all_cases())
                library_id = editor.library_id()
            _sync_map.set_aside_corrupt(device_db)
            _sync_map.write(device_db, cases, library_id)
        except Exception as e:
            _logger.log.error("Could not rebuild the sync record: %s", e, exc_info=True)
            QMessageBox.warning(
                self, "Sync Record Damaged",
                f"The Pacemaker's record of synced cases is damaged and couldn't be rebuilt.\n\n{e}"
            )
            return _sync_map.SyncRecord(status=_sync_map.MISSING)
        QMessageBox.warning(
            self, "Sync Record Repaired",
            "The Pacemaker's record of synced cases was damaged, so it's been rebuilt by matching "
            f"case names. {len(cases)} case(s) matched.\n\n"
            "Check the ticks in Editor Library before you next click Sync."
        )
        self._editor_panel.set_checked_cases(
            {c["case_id"] for c in self._editor_panel.get_checked_cases()} | {int(k) for k in cases}
        )
        return _sync_map.SyncRecord(cases, library_id)

    def _editor_library_id(self) -> "int | None":
        editor_db = self._panel.db_path
        if not editor_db:
            return None
        try:
            with PacemakerWriter(editor_db) as writer:
                return writer.library_id()
        except Exception as e:
            _logger.log.error("Could not read the library ID: %s", e, exc_info=True)
            return None

    def _is_synced_elsewhere(self, record: _sync_map.SyncRecord) -> bool:
        """True if the device's record names a different Editor library than this one."""
        library_id = self._editor_library_id()
        return record.library_id is not None and library_id is not None and record.library_id != library_id

    def _confirm_and_sync_to_device(self):
        editor_db = self._panel.db_path
        device_db = self._device_panel.db_path

        if not editor_db:
            QMessageBox.warning(self, "No Editor Database", "Editor database not loaded.")
            return
        if not device_db or not os.path.isfile(device_db):
            if device_db:
                self._disconnect_device_ui()
            QMessageBox.warning(
                self, "No Device",
                "Pacemaker device not found.\n\n"
                "Connect the device and click Refresh, or use Browse to locate its music.db."
            )
            return

        record = self._load_sync_record(device_db)

        try:
            with PacemakerWriter(editor_db) as editor, PacemakerWriter(device_db) as device:
                all_editor_cases = editor.get_all_cases()
                library_id = editor.library_id()
                device_cases = device.get_all_cases()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        editor_case_by_id = {c["case_id"]: c for c in all_editor_cases}
        checked_ids = {c["case_id"] for c in self._editor_panel.get_checked_cases()}

        # A Pacemaker synced to another computer: its Editor case IDs mean nothing here,
        # so the only safe sync is to replace everything on it.
        erase_all = record.library_id is not None and record.library_id != library_id
        if erase_all:
            if not checked_ids:
                QMessageBox.information(
                    self, "Pacemaker Synced to Another Computer",
                    "This Pacemaker is synced to a different computer, so nothing was changed.\n\n"
                    "Syncing from this computer replaces everything on it. To do that, tick the "
                    "cases you want on it, then click Sync."
                )
                return
            if device_cases and not self._confirm_replace_device(len(device_cases), len(checked_ids)):
                return
            record = _sync_map.SyncRecord()

        try:
            plan = self._plan_device_sync(
                editor_db, device_db, record.cases, editor_case_by_id, checked_ids, device_cases, erase_all
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not read the Pacemaker:\n\n{e}")
            return
        to_add, to_remove, to_update, sync_map, tracks_by_case, collisions = plan
        skip_sources = {skipped for _kept, skipped in collisions}

        if not to_add and not to_remove and not to_update:
            try:
                _sync_map.write(device_db, sync_map, library_id)   # entries for deleted cases may have gone
            except OSError as e:
                _logger.log.error("Could not save sync record: %s", e)
            # Count unsynced cases the user hasn't checked yet
            unsynced_unchecked = [
                eid for eid in editor_case_by_id
                if str(eid) not in sync_map and eid not in checked_ids
            ]
            hint = (
                f"  ({len(unsynced_unchecked)} unchecked case{'s' if len(unsynced_unchecked) != 1 else ''}"
                f" not yet on device — check them and sync again)"
                if unsynced_unchecked else ""
            )
            self.statusBar().showMessage(f"Device already in sync with checked cases.{hint}")
            return

        pushed_tracks = [t for c in to_add + to_update for t in tracks_by_case[c["case_id"]]]

        if skip_sources & {t.location for t in pushed_tracks}:
            if not self._confirm_name_collisions(collisions):
                return
        if not self._confirm_device_space(device_db, pushed_tracks, to_remove, skip_sources):
            return
        if (to_remove or to_update) and not erase_all:
            if not self._confirm_device_changes(to_add, to_remove, to_update):
                return

        go_ahead, self._last_backup = _backup_device_db(self, device_db)
        if not go_ahead:
            return

        self._run_device_sync(
            editor_db, device_db, to_add, to_remove, to_update, sync_map,
            library_id, erase_all, skip_sources,
        )

    def _plan_device_sync(self, editor_db: str, device_db: str, cases: dict, editor_case_by_id: dict,
                          checked_ids: set, device_cases: list, erase_all: bool) -> tuple:
        """
        Diff the ticks against what's really on the device: new cases to push, cases to
        remove, and cases on the device that no longer match the Editor Library.
        Returns (to_add, to_remove, to_update, sync_map, tracks_by_case, collisions).
        """
        sync_map = dict(cases)
        device_names = {c["case_id"]: c["name"] for c in device_cases}
        synced_ids = {int(k) for k in sync_map}

        with PacemakerWriter(device_db) as device, PacemakerWriter(editor_db) as editor:
            tracks_by_case = {
                eid: editor.get_case_tracks_as_trackinfo(eid)
                for eid in checked_ids if eid in editor_case_by_id
            }
            collisions = self._find_name_collisions(tracks_by_case, device)
            skip_sources = {skipped for _kept, skipped in collisions}

            to_add = [editor_case_by_id[eid] for eid in tracks_by_case if eid not in synced_ids]
            to_remove: list[dict] = []
            to_update: list[dict] = []

            if erase_all:
                to_remove = [
                    {"editor_case_id": None, "device_case_id": c["case_id"], "name": c["name"]}
                    for c in device_cases
                ]
                return to_add, to_remove, to_update, sync_map, tracks_by_case, collisions

            for key, device_case_id in list(sync_map.items()):
                editor_case_id = int(key)
                exists_on_device = device_case_id in device_names
                if editor_case_id not in tracks_by_case:
                    if exists_on_device:
                        to_remove.append({
                            "editor_case_id": editor_case_id,
                            "device_case_id": device_case_id,
                            "name": device_names[device_case_id],
                        })
                    else:
                        del sync_map[key]   # already gone from the device
                elif not exists_on_device:
                    del sync_map[key]       # ticked, so put it back
                    to_add.append(editor_case_by_id[editor_case_id])
                else:
                    case = editor_case_by_id[editor_case_id]
                    if self._device_case_differs(
                        case["name"], tracks_by_case[editor_case_id], device_names[device_case_id],
                        device_case_id, device, device_db, skip_sources,
                    ):
                        to_update.append({**case, "device_case_id": device_case_id})
        return to_add, to_remove, to_update, sync_map, tracks_by_case, collisions

    @staticmethod
    def _device_case_differs(name: str, tracks: list, device_name: str, device_case_id: int,
                             device: PacemakerWriter, device_db: str, skip_sources: set) -> bool:
        """True if the device's copy of a case has a different name, tracks, order or track details."""
        if name != device_name:
            return True
        expected = DeviceSyncWorker.expected_locations(tracks, device_db, skip_sources)
        on_device = device.get_case_tracks_as_trackinfo(device_case_id)
        if expected != [t.location for t in on_device]:
            return True
        by_location = {t.location: t for t in on_device}
        for track in tracks:
            if track.location in skip_sources:
                continue
            device_track = by_location.get(DeviceSyncWorker._pmdb_location(track.location))
            if device_track is not None and track_details_differ(track, device_track):
                return True
        return False

    @staticmethod
    def _find_name_collisions(tracks_by_case: dict, device: PacemakerWriter) -> list:
        """
        Device file names come from a checksum of the source path, so two different files
        could very rarely get the same one. Returns [(file kept, file that would overwrite it)],
        where the kept file may be a title already on the device.
        """
        sources: dict[str, str] = {}
        collisions = []
        checked_on_device = set()
        for tracks in tracks_by_case.values():
            for track in tracks:
                location = DeviceSyncWorker._pmdb_location(track.location)
                existing = sources.setdefault(location, track.location)
                if existing != track.location:
                    if (existing, track.location) not in collisions:
                        collisions.append((existing, track.location))
                    continue
                if location in checked_on_device:
                    continue
                checked_on_device.add(location)
                on_device = device.get_track_by_location(location)
                if on_device is not None and (
                    on_device.title != track.title and on_device.artist != track.artist
                    and on_device.file_size != track.file_size
                ):
                    collisions.append((f"{on_device.title} (on the Pacemaker)", track.location))
        return collisions

    def _confirm_name_collisions(self, collisions: list) -> bool:
        pairs = [
            f"  • {os.path.basename(kept.replace(chr(92), '/'))}  /  "
            f"{os.path.basename(skipped.replace(chr(92), '/'))}"
            for kept, skipped in collisions[:10]
        ]
        if len(collisions) > 10:
            pairs.append(f"  …and {len(collisions) - 10} more")
        return _ask(
            self, QMessageBox.Icon.Warning, "Tracks Would Overwrite Each Other",
            "These tracks would get the same file name on the Pacemaker, so one would replace "
            "the other:\n\n" + "\n".join(pairs) + "\n\n"
            "If you continue, the second track of each pair is left off the Pacemaker. "
            "Moving or renaming its file on your computer avoids this.",
            [("Continue", QMessageBox.ButtonRole.AcceptRole),
             ("Cancel", QMessageBox.ButtonRole.RejectRole)],
            default="Cancel",
        ) == "Continue"

    def _confirm_replace_device(self, device_case_count: int, ticked_count: int) -> bool:
        """Two separate confirmations, so a Pacemaker can't be wiped by accident."""
        understood = _ask(
            self, QMessageBox.Icon.Warning, "Pacemaker Synced to Another Computer",
            "WARNING: this Pacemaker is synced to a different computer, or to an Editor Library "
            "that has since been replaced.\n\n"
            "Syncing it from this computer will overwrite its entire contents. Everything on it "
            f"now ({device_case_count} case(s) and their music) will be deleted and replaced "
            f"with the {ticked_count} case(s) ticked here.",
            [("I Understand", QMessageBox.ButtonRole.AcceptRole),
             ("Cancel", QMessageBox.ButtonRole.RejectRole)],
            default="Cancel",
        )
        if understood != "I Understand":
            return False
        return _ask(
            self, QMessageBox.Icon.Critical, "Are You Sure?",
            f"Are you sure? This permanently deletes all {device_case_count} case(s) on the Pacemaker "
            "and their music files.\n\nThis can't be undone.",
            [("Erase and Sync", QMessageBox.ButtonRole.DestructiveRole),
             ("Cancel", QMessageBox.ButtonRole.RejectRole)],
            default="Cancel",
        ) == "Erase and Sync"

    @staticmethod
    def _bytes_needed_on_device(device_db: str, tracks: list, to_remove: list, skip_sources: set) -> tuple:
        """Returns (bytes this sync will copy, bytes it will free by removing cases)."""
        needed = 0
        wanted = set()
        for track in tracks:
            location = DeviceSyncWorker._pmdb_location(track.location)
            if location in wanted or track.location in skip_sources:
                continue
            wanted.add(location)
            if os.path.exists(track.location) and _needs_copy(
                track.location, DeviceSyncWorker._local_path(location, device_db)
            ):
                needed += os.path.getsize(track.location)

        freed = 0
        removing = {r["device_case_id"] for r in to_remove}
        if removing:
            with PacemakerWriter(device_db) as device:
                locations = {
                    loc for case_id in removing for loc in device.get_case_track_locations(case_id)
                }
                for location in locations - wanted:
                    local = DeviceSyncWorker._local_path(location, device_db)
                    if (device.get_case_ids_for_location(location) <= removing
                            and is_on_device(local, device_db) and os.path.exists(local)):
                        freed += os.path.getsize(local)
        return needed, freed

    def _confirm_device_space(self, device_db: str, tracks: list, to_remove: list, skip_sources: set) -> bool:
        """Warn before a sync that would use up all the space on the device."""
        try:
            needed, freed = self._bytes_needed_on_device(device_db, tracks, to_remove, skip_sources)
            free = shutil.disk_usage(device_root(device_db)).free
        except OSError as e:
            _logger.log.error("Could not check device space: %s", e)
            return True   # the sync itself will report a real failure
        if not needed or needed + _DEVICE_SPACE_MARGIN <= free + freed:
            return True
        return _ask(
            self, QMessageBox.Icon.Warning, "Not Enough Space on Pacemaker",
            f"This sync needs {_fmt_bytes(needed)}, but the Pacemaker only has "
            f"{_fmt_bytes(free + freed)} available, so it will use up all the space and some "
            "tracks won't fit.\n\nUntick some cases to make room, or sync anyway to copy what fits.",
            [("Sync Anyway", QMessageBox.ButtonRole.AcceptRole),
             ("Cancel", QMessageBox.ButtonRole.RejectRole)],
            default="Cancel",
        ) == "Sync Anyway"

    def _confirm_device_changes(self, to_add: list, to_remove: list, to_update: list) -> bool:
        """Ask before changing what's already on the device. Returns True to go ahead."""
        def section(heading: str, cases: list) -> str:
            limit = 10
            names = [f"  • {c['name']}" for c in cases[:limit]]
            if len(cases) > limit:
                names.append(f"  …and {len(cases) - limit} more")
            return heading + "\n" + "\n".join(names)

        sections = []
        if to_remove:
            sections.append(section(
                "Remove from the Pacemaker, including music files no other case uses:", to_remove
            ))
        if to_update:
            sections.append(section("Update to match your Editor Library:", to_update))
        if to_add:
            sections.append(section("Add to the Pacemaker:", to_add))

        title = "Remove Cases from Pacemaker?" if to_remove else "Update Cases on Pacemaker?"
        reply = QMessageBox.question(
            self, title,
            "\n\n".join(sections) + "\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _set_device_syncing(self, syncing: bool) -> None:
        """While syncing, nothing that touches the device can run, Eject included."""
        self._device_syncing = syncing
        self._editor_panel.setEnabled(not syncing)
        self._device_panel.setEnabled(not syncing)
        self._device_panel.set_eject_enabled(not syncing)
        for action in self._sync_blocked_actions:
            action.setEnabled(not syncing)
        self._editor_panel.push_button.setEnabled(not syncing and bool(self._device_panel.db_path))

    def _run_device_sync(self, editor_db: str, device_db: str,
                         to_add: list, to_remove: list, to_update: list, sync_map: dict,
                         library_id: "int | None" = None, erase_all: bool = False,
                         skip_sources: "set[str] | None" = None):
        self._set_device_syncing(True)

        total_tracks = sum(c.get("track_count", 0) for c in to_add + to_update)
        self._push_progress_dlg = _WorkProgressDialog(
            "Syncing to device…", "Cancel Sync", 0, max(total_tracks, 1), self
        )
        self._push_progress_dlg.setWindowTitle("Sync to Device")
        self._push_progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._push_progress_dlg.setMinimumDuration(0)
        self._push_progress_dlg.setAutoClose(False)
        self._push_progress_dlg.setAutoReset(False)
        self._push_progress_dlg.setValue(0)
        self._push_progress_dlg.canceled.connect(self._cancel_device_sync)
        self._push_progress_dlg.show()

        self._push_thread = QThread()
        self._push_worker = DeviceSyncWorker(
            editor_db, device_db, to_add, to_remove, to_update, sync_map,
            library_id, erase_all, skip_sources,
        )
        self._push_worker.moveToThread(self._push_thread)

        self._push_thread.started.connect(self._push_worker.run)
        self._push_worker.progress.connect(self._on_device_sync_progress)
        self._push_worker.finished.connect(self._on_device_sync_finished)

        self._push_thread.start()

    def _cancel_device_sync(self) -> None:
        """The Cancel Sync button. The current track finishes, so nothing is left half copied."""
        if self._push_worker is not None:
            self._push_worker.cancel()
        dlg = self._push_progress_dlg
        if dlg is not None:
            dlg.setLabelText("Stopping after the current track…")
            for button in dlg.findChildren(QPushButton):
                button.setEnabled(False)
            dlg.show()   # QProgressDialog hides itself on cancel

    def _on_device_sync_progress(self, current: int, total: int, status: str):
        worker = self._push_worker
        if self._push_progress_dlg and worker is not None and not worker._cancelled:
            self._push_progress_dlg.setValue(current)
            self._push_progress_dlg.setLabelText(status)

    def _on_device_sync_finished(self, success: bool, message: str):
        if self._push_progress_dlg:
            self._push_progress_dlg.hide()
            self._push_progress_dlg.deleteLater()
            self._push_progress_dlg = None
        self._end_thread(self._push_thread)
        worker = self._push_worker
        self._push_worker = None
        self._set_device_syncing(False)

        if worker.device_lost:
            backup = f"\n\nA copy of the database from before this sync is at:\n{self._last_backup}" if self._last_backup else ""
            message = "Device removed during sync."
            self._disconnect_device_ui()
            QMessageBox.critical(
                self, "Device Removed During Sync",
                "The Pacemaker was removed during the sync, so it didn't finish.\n\n"
                "Its database may be damaged. Reconnect it and check your cases. If tracks won't "
                f"load, use File → Repair Device Database.{backup}"
            )
        elif success:
            if not worker.was_cancelled:
                self._play_completion_sound()
            self._load_device_library()
            if worker.failures:
                QMessageBox.warning(
                    self, "Some Tracks Weren't Copied",
                    f"{len(worker.failures)} track(s) couldn't be copied to the Pacemaker:\n\n"
                    f"{_list_files(worker.failures)}\n\n"
                    "Click Sync again to retry. Cases with a failed track keep the tracks they "
                    "already had on the Pacemaker."
                )
        else:
            self._load_device_library()
            QMessageBox.critical(self, "Sync Failed", f"An error occurred:\n\n{message}")

        self.statusBar().showMessage(message)
        self._quit_if_requested()

    @staticmethod
    def _end_thread(thread: "QThread | None") -> None:
        """A worker has just finished: let its thread end, so it's safe to replace or quit."""
        if thread is not None:
            thread.quit()
            thread.wait(5000)

    @staticmethod
    def _play_completion_sound() -> None:
        """Play a short completion chime, with no extra dependencies."""
        try:
            if sys.platform.startswith("win"):
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)  # asterisk = info chime
            else:
                QApplication.beep()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Quitting
    # ------------------------------------------------------------------

    def _work_running(self) -> str:
        """What's running that shouldn't be cut off, as a noun, or "" if nothing is."""
        if self._device_syncing:
            return "sync to the Pacemaker"
        if self._cleaning_up:
            return "clean-up of the Pacemaker"
        if self._sync_progress_dlg is not None or self._import_progress_dlg is not None:
            return "import"
        return ""

    def _stop_running_work(self) -> None:
        for worker in (self._push_worker, self._worker, self._import_worker, self._cleanup_worker):
            if worker is not None:
                worker.cancel()

    def _quit_if_requested(self) -> None:
        if self._quit_when_idle and not self._work_running():
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event) -> None:
        work = self._work_running()
        if work:
            event.ignore()
            if self._quit_when_idle:
                self.statusBar().showMessage("Stopping safely, then quitting…")
            elif not self._asking_to_quit:
                self._ask_to_quit_during(work)
            return
        self._shut_down()
        super().closeEvent(event)

    def _ask_to_quit_during(self, work: str) -> None:
        """The first Quit during work only warns. Stopping ends the work safely, then quits."""
        self._asking_to_quit = True
        stop_label = "Stop and Quit"
        choice = _ask(
            self, QMessageBox.Icon.Warning, "Still Working",
            f"A {work} is still running.\n\n"
            "If you quit now, it stops safely after the current track, and the rest doesn't get "
            "done. You can carry on later.",
            [("Keep Going", QMessageBox.ButtonRole.RejectRole),
             (stop_label, QMessageBox.ButtonRole.DestructiveRole)],
            default="Keep Going",
        )
        self._asking_to_quit = False
        if choice == stop_label:
            self._quit_when_idle = True
            self._stop_running_work()
            self._quit_if_requested()   # it may have finished while the question was showing

    def _shut_down(self) -> None:
        """A QThread destroyed while running aborts the app, so wait for them all."""
        self._device_poll_timer.stop()
        self._stop_preview()
        self._player_bar.shut_down()
        if getattr(self, "_wave_worker", None):
            self._wave_worker.cancel()
        threads = [self._sync_thread, self._push_thread, self._import_thread, self._cleanup_thread]
        threads += [thread for thread, _worker in getattr(self, "_wave_threads", [])]
        for thread in threads:
            if thread is not None:
                thread.quit()
                thread.wait(5000)
        if self._rb_reader is not None:
            try:
                self._rb_reader.close()
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

    def _load_editor_library(self):
        # Reloading must leave the ticks alone: only the user changes them, plus connecting
        # a device, which adds the cases already on it.
        currently_checked = {c["case_id"] for c in self._editor_panel.get_checked_cases()}
        db_path = self._panel.db_path
        if not db_path:
            self._editor_panel.clear()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                cases = writer.get_all_cases()
            self._editor_panel.load_cases(cases, set())
        except Exception as e:
            self._editor_panel.clear()
            self.statusBar().showMessage(f"Could not read Editor library: {e}")
            return
        self._editor_panel.set_checked_cases(currently_checked)
        self._update_copy_sizes()
        # Default/refresh the central table to the whole-library view unless
        # a specific case is currently open there.
        if self._track_table_source in ("", "editor_all"):
            self._show_all_editor_tracks()
        elif self._track_table_source == "editor":
            self._refresh_open_case_header()

    def _refresh_open_case_header(self) -> None:
        """Keep the track table's heading true after the open case changes or goes."""
        case = self._editor_panel.case_by_id(self._track_table_case_id)
        if case is None:
            self._show_all_editor_tracks()
        else:
            self._track_view_lbl.setText(f"{case['name']}  —  {case['track_count']} tracks")

    def _update_copy_sizes(self) -> None:
        """Give the Editor panel what it needs to show how much the ticked cases would copy."""
        db_path = self._panel.db_path
        if not db_path:
            return
        try:
            with PacemakerWriter(db_path) as writer:
                sizes = writer.get_case_track_sizes()
        except Exception as e:
            _logger.log.error("Could not read case sizes: %s", e)
            return
        on_device = {
            location for tracks in sizes.values() for location in tracks
            if DeviceSyncWorker._pmdb_location(location) in self._device_locations
        }
        self._editor_panel.set_track_sizes(sizes, on_device, bool(self._device_panel.db_path))

    def _on_editor_case_selected(self, case_id: int) -> None:
        """
        Load Editor case tracks into the central track table, or show the
        whole-library "all tracks" view when nothing is selected.
        """
        db_path = self._panel.db_path
        if not db_path:
            self._clear_track_table()
            return
        if case_id == -1:
            self._show_all_editor_tracks()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                tracks = writer.get_case_tracks_as_trackinfo(case_id)
            case = self._editor_panel.case_by_id(case_id)
            name = case["name"] if case else "Case"
            self._load_tracks_in_table(tracks, source="editor",
                                       label=f"{name}  —  {len(tracks)} tracks")
            self._track_table_case_id = case_id
            self._track_table.set_case_mode(True)
            self._track_table.set_library_mode(False)
        except Exception:
            self._clear_track_table()

    def _show_all_editor_tracks(self) -> None:
        """
        Show every track in the Editor library — the default view when no
        case is selected, and the drag source for assigning tracks to cases.
        """
        db_path = self._panel.db_path
        if not db_path:
            self._clear_track_table()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                pairs = writer.get_all_tracks_as_trackinfo()
            tracks = [p[0] for p in pairs]
            case_counts = [p[1] for p in pairs]
            orphan_count = sum(1 for c in case_counts if c == 0)
            label = f"All Tracks  —  {len(tracks)} tracks"
            if orphan_count:
                label += f"  ·  {orphan_count} unassigned"
            self._load_tracks_in_table(tracks, source="editor_all", label=label,
                                       case_counts=case_counts)
            self._track_table_case_id = None
            self._track_table.set_case_mode(False)
            self._track_table.set_library_mode(True)
        except Exception:
            self._clear_track_table()

    def _on_device_case_selected(self, case_id: int) -> None:
        """Load Device case tracks into the central track table (read-only)."""
        db_path = self._device_panel.db_path
        if case_id == -1:
            # Deselecting on the device only affects the table if it's showing the device
            if self._track_table_source == "device":
                self._show_all_editor_tracks()
            return
        if not db_path:
            self._clear_track_table()
            return
        try:
            with PacemakerWriter(db_path) as writer:
                tracks = writer.get_case_tracks_as_trackinfo(case_id)
            case = self._device_panel.case_by_id(case_id)
            name = case["name"] if case else "Case"
            self._load_tracks_in_table(tracks, source="device",
                                       label=f"{name}  —  {len(tracks)} tracks  [device]")
            self._track_table_case_id = None
            self._track_table.set_case_mode(False)
            self._track_table.set_library_mode(False)
        except Exception:
            self._clear_track_table()

    def _load_tracks_in_table(self, tracks: list[TrackInfo], source: str, label: str,
                               case_counts: "list[int] | None" = None) -> None:
        """
        Populate the central track table. source='editor'|'editor_all'|'device'.
        case_counts, when given (whole-library "all tracks" view only), shows
        and fills the Cases column and greys out rows for orphaned tracks.
        """
        from ui.style import ACCENT
        from PyQt6.QtGui import QColor as _QColor

        # Stop any inline preview that was running for the previous case
        self._stop_preview()

        # Cancel any running waveform batch
        if hasattr(self, "_wave_worker") and self._wave_worker:
            self._wave_worker.cancel()

        self._track_table.setSortingEnabled(False)
        self._track_table.setRowCount(0)
        self._wave_items = {}
        self._track_table_tracks = list(tracks)
        self._track_table.set_tracks(self._track_table_tracks)
        self._track_table_source = source
        self._track_view_lbl.setText(label)
        self._all_tracks_btn.setVisible(source == "editor")
        self._track_table.setColumnHidden(9, case_counts is None)

        STAR_MAP = {0: "", 1: "★", 2: "★★", 3: "★★★", 4: "★★★★", 5: "★★★★★"}
        orphan_color = _QColor("#888888")

        for i, t in enumerate(tracks):
            self._track_table.insertRow(i)
            secs = int(t.play_time_secs or 0)
            duration = f"{secs // 60}:{secs % 60:02d}"
            bpm_val = int(t.bpm) if t.bpm else 0
            if bpm_val > 1000:
                bpm_val = round(bpm_val / 100)
            bpm = str(bpm_val) if bpm_val else ""
            stars = STAR_MAP.get(t.rating, "")
            case_count = case_counts[i] if case_counts else None
            is_orphan = case_count == 0

            # cols: #(0) ★(1) ~wave(2) Title(3) Artist(4) BPM(5) Key(6) Time(7) Genre(8) Cases(9)
            _numeric_sort = {0: i + 1, 5: bpm_val, 7: secs}
            for col, val in enumerate([
                str(i + 1), stars, "", t.title or "", t.artist or "",
                bpm, t.key or "", duration, t.genre or "",
                str(case_count) if case_count is not None else "",
            ]):
                item = _SortableItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setData(Qt.ItemDataRole.UserRole, i)
                if col in _numeric_sort:
                    item.setData(_SortableItem._SORT_ROLE, _numeric_sort[col])
                if col in (0, 1, 2, 5, 6, 7, 9):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 1 and val:
                    item.setForeground(_QColor(ACCENT))
                elif is_orphan and col != 1:
                    item.setForeground(orphan_color)
                self._track_table.setItem(i, col, item)
                if col == 2:
                    self._wave_items[i] = item

        self._track_table.setSortingEnabled(True)

        # Fetch waveforms in background (only for a single open case, where we
        # have RB data — never for the whole-library "all tracks" view)
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

    def _on_wave_row_done(self, generation: int, track_index: int, data) -> None:
        # Ignore results from a cancelled/superseded batch
        if generation != getattr(self, "_wave_generation", 0):
            return
        # By track, not row: the table may have been sorted since the batch started
        item = self._wave_items.get(track_index)
        if item is not None and data:
            item.setData(_WAVE_COL_ROLE, data)

    def _clear_track_table(self) -> None:
        self._track_table.setRowCount(0)
        self._wave_items = {}
        self._track_table_tracks = []
        self._track_table.set_tracks([])
        self._track_table_source = ""
        self._track_table_case_id = None
        self._track_table.set_case_mode(False)
        self._track_table.set_library_mode(False)
        self._track_table.setColumnHidden(9, True)
        self._track_view_lbl.setText("Select a case to view tracks")
        self._all_tracks_btn.setVisible(False)

    def _on_show_all_tracks_clicked(self) -> None:
        """"← All Tracks" link — deselect the open case and show the whole library."""
        self._editor_panel.clear_selection()
        # Deselecting normally triggers this via case_selected(-1), but if the
        # list had nothing selected to begin with (e.g. the open case was just
        # deleted, so it's already gone from the list) that signal never
        # fires — call it directly so the link always works.
        self._show_all_editor_tracks()

    def _playable_location(self, location: str) -> str:
        """The path on this computer for a track in the table (device tracks hold firmware paths)."""
        if self._track_table_source == "device":
            return DeviceSyncWorker._local_path(location, self._device_panel.db_path)
        return location

    def _on_track_double_clicked(self, item: QTableWidgetItem) -> None:
        """Play the double-clicked track."""
        if self._track_table_source not in ("editor", "editor_all", "device"):
            return
        orig_row = item.data(Qt.ItemDataRole.UserRole)
        if orig_row is None:
            orig_row = item.row()
        if 0 <= orig_row < len(self._track_table_tracks):
            tracks = [
                dataclasses.replace(t, location=self._playable_location(t.location))
                for t in self._track_table_tracks
            ]
            self._player_follows_table = True
            self._player_bar.load_and_play(tracks, orig_row)

    def _on_player_track_changed(self, index: int) -> None:
        """Highlight the currently playing track row in the table."""
        if not self._player_follows_table:
            return
        for row in range(self._track_table.rowCount()):
            item = self._track_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == index:
                self._track_table.selectRow(row)
                break

    def _on_device_track_tab_play(self, track: dict) -> None:
        """Play a track double-clicked in the Device panel's Tracks tab."""
        location = DeviceSyncWorker._local_path(track.get("location") or "", self._device_panel.db_path)
        self._player_follows_table = False
        self._player_bar.load_and_play([TrackInfo(
            location=location,
            title=track.get("title") or "",
            artist=track.get("artist") or "",
            play_time_secs=int(track.get("play_time_secs") or 0),
        )], 0)

    def _on_device_track_tab_selected(self, track_id: int) -> None:
        """Show which cases hold the track selected in the Device panel's Tracks tab."""
        names: list[str] = []
        db_path = self._device_panel.db_path
        if db_path and track_id >= 0:
            try:
                with PacemakerWriter(db_path) as writer:
                    names = writer.get_cases_for_track(track_id)
            except Exception as e:
                _logger.log.error("Could not read cases for track: %s", e)
        self._device_panel.set_track_cases_banner(names)

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

        if self._preview_row == orig_row:
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
            location = self._playable_location(track.location)
            if not os.path.exists(location):
                self.statusBar().showMessage(f"File not found: {location}")
                return
            self._player_bar.stop()
            self._stop_preview()
            self._preview_row = orig_row
            self._wave_delegate.preview_row = orig_row
            self._preview_seek_fraction = fraction
            if self._preview_player:
                self._preview_player.setSource(QUrl.fromLocalFile(location))
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
        deleted_ids = {c["case_id"] for c in cases}

        def _refresh():
            self._load_editor_library()
            # If the case open in the central table was just deleted, its
            # track view is now stale (and the "All Tracks" link would have
            # nothing to deselect) — fall back to the whole-library view.
            if self._track_table_case_id in deleted_ids:
                self._show_all_editor_tracks()

        self._delete_cases(
            db_path=self._panel.db_path,
            cases=cases,
            update_sync_state=True,
            refresh_fn=_refresh,
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
                    if writer.case_name_exists(new_name, ignore_case_id=case["case_id"]):
                        self._warn_duplicate_case_name(new_name)
                        continue
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
                if writer.case_name_exists(new_name, ignore_case_id=case_id):
                    self._warn_duplicate_case_name(new_name)
                    self._load_editor_library()   # put the old name back in the list
                    return
                writer.rename_case(case_id, new_name)
            _logger.log.info("Renamed editor case %d to %r (inline)", case_id, new_name)
            self.statusBar().showMessage(f'Renamed to "{new_name}".')
            self._load_editor_library()
        except Exception as e:
            _logger.log.error("Inline rename failed: %s", e, exc_info=True)
            QMessageBox.critical(self, "Rename Failed", str(e))

    def _warn_duplicate_case_name(self, name: str) -> None:
        QMessageBox.warning(
            self, "Name Already Used",
            f'There\'s already a case called "{name}". Please choose a different name.',
        )

    def _on_new_case_requested(self) -> None:
        """Handle the Editor panel's "New Case" button."""
        db_path = self._panel.db_path
        if not db_path:
            QMessageBox.warning(self, "No Database", "Please select an Editor database first.")
            return
        name = self._ask_new_case_name(db_path)
        if not name:
            return
        try:
            with PacemakerWriter(db_path) as writer:
                writer.create_case(name)
            self._load_editor_library()
            self.statusBar().showMessage(f"Created case \"{name}\".")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _ask_new_case_name(self, db_path: str, suggestion: str = "") -> str:
        """Returns a name no case is using, or "" if the user cancelled or chose a taken one."""
        name, ok = QInputDialog.getText(self, "New Case", "Case name:", text=suggestion)
        name = name.strip()
        if not ok or not name:
            return ""
        try:
            with PacemakerWriter(db_path) as writer:
                if writer.case_name_exists(name):
                    self._warn_duplicate_case_name(name)
                    return ""
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return ""
        return name

    def _on_tracks_dropped_on_case(self, case_id: int, locations: list) -> None:
        """Handle tracks dragged from the central table onto a case in the side list."""
        db_path = self._panel.db_path
        if not db_path:
            return
        added = skipped = 0
        try:
            with PacemakerWriter(db_path) as writer:
                in_case = writer.get_case_track_locations(case_id)
                add_duplicates = False
                if _has_repeats(locations) or in_case & set(locations):
                    add_duplicates = _ask_add_duplicates(self)
                for loc in locations:
                    track_id = writer.get_track_id_by_location(loc)
                    if track_id is None:
                        continue
                    if loc in in_case and not add_duplicates:
                        skipped += 1
                        continue
                    writer.link_track_to_case(case_id, track_id)
                    in_case.add(loc)
                    added += 1
            self._load_editor_library()
            self._refresh_open_editor_track_view(case_id)
            note = f" Skipped {skipped} duplicate(s)." if skipped else ""
            self.statusBar().showMessage(f"Added {added} track(s) to case.{note}")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_files_dropped_on_case(self, case_id: int, paths: list) -> None:
        """Handle files/folders dragged from Explorer directly onto a case."""
        self._import_dropped_files(paths, case_id)

    def _on_files_dropped_on_empty(self, paths: list) -> None:
        """Handle files/folders dragged below the cases, which makes a new case from them."""
        self._import_dropped_files(paths, None, make_case=True)

    def _on_central_files_dropped(self, paths: list) -> None:
        """
        Handle files/folders dragged from Explorer onto the central table.
        Imports straight into the currently open case, or as orphan tracks
        when the whole-library "all tracks" view is showing.
        """
        if self._track_table_source not in ("editor", "editor_all"):
            self.statusBar().showMessage(
                "Those files weren't imported. Open a case or All Tracks in Editor Library, then drop them there."
            )
            return
        self._import_dropped_files(paths, self._track_table_case_id)

    def _import_dropped_files(self, paths: list, case_id: "int | None", make_case: bool = False) -> None:
        db_path = self._panel.db_path
        if not db_path:
            QMessageBox.warning(self, "No Database", "Please select an Editor database first.")
            return

        files, self._import_unsupported = expand_dropped_paths(paths)
        if not files:
            if self._import_unsupported:
                QMessageBox.warning(self, "Nothing to Import", _unsupported_note(self._import_unsupported))
            else:
                self.statusBar().showMessage("No audio files found in what you dropped.")
            return

        new_case_name = ""
        add_duplicates = False
        if make_case:
            folder = paths[0] if len(paths) == 1 and os.path.isdir(paths[0]) else ""
            new_case_name = self._ask_new_case_name(db_path, os.path.basename(folder.rstrip("/\\")))
            if not new_case_name:
                return
            if _has_repeats(files):
                add_duplicates = _ask_add_duplicates(self)
        elif case_id is not None:
            try:
                with PacemakerWriter(db_path) as writer:
                    in_case = writer.get_case_track_locations(case_id)
            except Exception as e:
                QMessageBox.critical(self, "Import Error", str(e))
                return
            if _has_repeats(files) or in_case & set(files):
                add_duplicates = _ask_add_duplicates(self)

        self._import_progress_dlg = _WorkProgressDialog(
            "Checking files…", None, 0, 0, self
        )
        self._import_progress_dlg.setWindowTitle("Importing Tracks")
        self._import_progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._import_progress_dlg.setMinimumDuration(0)
        self._import_progress_dlg.show()

        self._import_thread = QThread()
        self._import_worker = FileImportWorker(db_path, files, case_id, add_duplicates, new_case_name)
        self._import_worker.moveToThread(self._import_thread)
        self._import_thread.started.connect(self._import_worker.run)

        def on_progress(i, n, msg, _dlg=self._import_progress_dlg):
            if n:
                _dlg.setRange(0, n)
                _dlg.setValue(i)
            _dlg.setLabelText(msg)

        self._import_worker.progress.connect(on_progress)
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_thread.start()

    def _on_import_finished(self, success: bool, message: str, problems: list) -> None:
        if self._import_progress_dlg:
            self._import_progress_dlg.hide()
            self._import_progress_dlg.deleteLater()
            self._import_progress_dlg = None
        self._end_thread(self._import_thread)
        self._import_worker = None
        if success:
            self._load_editor_library()
            self._refresh_open_editor_track_view(None)
            self.statusBar().showMessage(message)
            if self._import_unsupported:
                QMessageBox.warning(self, "Some Files Weren't Imported", _unsupported_note(self._import_unsupported))
        elif problems:
            self.statusBar().showMessage(message)
            QMessageBox.warning(
                self, "Import Stopped",
                f"{len(problems)} file(s) would cause problems, so nothing was imported:\n\n"
                f"{_list_files(problems)}\n\n"
                "Fix these files or leave them out, then try again."
            )
        else:
            QMessageBox.critical(self, "Import Error", message)
        self._quit_if_requested()

    def _refresh_open_editor_track_view(self, affected_case_id: "int | None") -> None:
        """
        Re-render the open case's track view if it's affected. (The
        whole-library "all tracks" view is already refreshed by
        _load_editor_library, called just before this in every handler.)
        """
        if self._track_table_source == "editor" and (
            affected_case_id is None or self._track_table_case_id == affected_case_id
        ):
            self._on_editor_case_selected(self._track_table_case_id)

    def _on_remove_from_case_requested(self, track_indexes: list) -> None:
        """
        Remove the selected rows from the open case (delete key / context menu). Works on
        rows, so removing one copy of a track that's in the case twice leaves the other.
        """
        if self._track_table_source != "editor" or self._track_table_case_id is None:
            return
        db_path = self._panel.db_path
        if not db_path:
            return
        case_id = self._track_table_case_id
        removing = set(track_indexes)
        try:
            with PacemakerWriter(db_path) as writer:
                keep = [
                    writer.get_track_id_by_location(t.location)
                    for i, t in enumerate(self._track_table_tracks) if i not in removing
                ]
                writer.reorder_case_tracks(case_id, [t for t in keep if t is not None])
            self._load_editor_library()
            self._refresh_open_editor_track_view(case_id)
            self.statusBar().showMessage(f"Removed {len(removing)} track(s) from case.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_remove_from_library_requested(self, locations: list) -> None:
        """Remove unassigned tracks from the library (delete key / context menu in All Tracks)."""
        db_path = self._panel.db_path
        if not db_path or self._track_table_source != "editor_all":
            return
        try:
            with PacemakerWriter(db_path) as writer:
                unassigned = [loc for loc in locations if not writer.get_case_ids_for_location(loc)]
                in_cases = len(locations) - len(unassigned)
                if not unassigned:
                    QMessageBox.information(
                        self, "Can't Remove",
                        "Only tracks that aren't in any case can be removed from the library. "
                        "Open the case and remove the track from it first."
                    )
                    return
                note = f"\n\n{in_cases} selected track(s) are in a case, so they'll stay." if in_cases else ""
                reply = QMessageBox.question(
                    self, "Remove from Library",
                    f"Remove {len(unassigned)} unassigned track(s) from the Editor Library?\n\n"
                    f"The music files on your computer aren't touched.{note}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                removed = writer.delete_orphan_tracks(unassigned)
            self._load_editor_library()
            self.statusBar().showMessage(f"Removed {len(removed)} track(s) from the library.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_reorder_requested(self, new_order_locations: list) -> None:
        """Persist a drag-reorder of tracks within the currently open case."""
        if self._track_table_source != "editor" or self._track_table_case_id is None:
            return
        db_path = self._panel.db_path
        if not db_path:
            return
        case_id = self._track_table_case_id
        try:
            with PacemakerWriter(db_path) as writer:
                track_ids = []
                for loc in new_order_locations:
                    track_id = writer.get_track_id_by_location(loc)
                    if track_id is not None:
                        track_ids.append(track_id)
                writer.reorder_case_tracks(case_id, track_ids)
            self._refresh_open_editor_track_view(case_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ------------------------------------------------------------------
    # Device library panel
    # ------------------------------------------------------------------

    def _on_device_db_changed(self, path: str):
        self._connect_device(path)

    def _connect_device(self, db_path: str) -> None:
        """A device has been found or chosen: load it and tick the cases already on it."""
        self._device_panel.set_db_path_label(db_path)
        self._load_device_library()
        record = self._load_sync_record(db_path)
        elsewhere = self._is_synced_elsewhere(record)
        if not elsewhere:
            self._editor_panel.set_checked_cases(
                {c["case_id"] for c in self._editor_panel.get_checked_cases()} | record.editor_case_ids()
            )
        self._editor_panel.push_button.setEnabled(not self._device_syncing)
        root = device_root(db_path)
        self._device_path_lbl.setText(
            f"Device: {root}" + ("  (synced to another computer)" if elsewhere else "")
        )

    def _disconnect_device_ui(self) -> None:
        """The device has gone. The ticks stay: they're still what the user wants on it."""
        self._release_device_playback(device_root(self._device_panel.db_path))
        self._device_panel.set_db_path_label("")
        self._device_panel.clear()
        self._device_locations = set()
        self._device_path_lbl.setText("Device: not connected")
        self._editor_panel.push_button.setEnabled(False)
        self._update_copy_sizes()
        if self._track_table_source == "device":
            self._show_all_editor_tracks()

    def _release_device_playback(self, root: str) -> None:
        """Stop anything playing from the device, so nothing holds its files open."""
        if not root:
            return
        if self._track_table_source == "device":
            self._stop_preview()
            if self._preview_player:
                self._preview_player.setSource(QUrl())
        self._player_bar.release_if_under(root)

    def _load_device_library(self):
        db_path = self._device_panel.db_path
        if not db_path:
            self._device_panel.clear()
            return
        if not os.path.isfile(db_path):
            self._on_device_lost()
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
        self._device_locations = {t["location"] for t in all_tracks}
        self._load_device_storage()
        self._update_copy_sizes()

    def _load_device_storage(self) -> None:
        """Read device drive free/total space and used bytes from DB, update storage bar."""
        device_db = self._device_panel.db_path
        if not device_db:
            return
        try:
            total, used, free = shutil.disk_usage(device_root(device_db))
        except OSError as e:
            _logger.log.warning("Could not read device storage: %s", e)
            self._device_panel.set_storage_info(0, 0)
            return
        self._device_panel.set_storage_info(used, total)

    def _on_delete_device_case(self, cases: list):
        device_db = self._device_panel.db_path
        deleted_ids = {c["case_id"] for c in cases}

        def _refresh():
            # Forget the deleted cases, so their Editor ticks clear rather than the
            # next Sync treating them as still wanted and putting them straight back.
            record = self._load_sync_record(device_db)
            kept = {k: v for k, v in record.cases.items() if v not in deleted_ids}
            forgotten = {int(k) for k in record.cases} - {int(k) for k in kept}
            try:
                _sync_map.write(device_db, kept, record.library_id)
            except OSError as e:
                _logger.log.error("Could not save sync record: %s", e)
            self._editor_panel.set_checked_cases(
                {c["case_id"] for c in self._editor_panel.get_checked_cases()} - forgotten
            )
            self._load_device_library()

        self._delete_cases(
            db_path=device_db,
            cases=cases,
            update_sync_state=False,
            refresh_fn=_refresh,
        )

    def _eject_device(self):
        db_path = self._device_panel.db_path
        if not db_path:
            return
        work = self._work_running()
        if work:
            self.statusBar().showMessage(f"Can't eject during a {work}.")
            return
        root = device_root(db_path)   # e.g. "J:\" or "/Volumes/PACEMAKER"
        self._release_device_playback(root)

        if sys.platform.startswith("win"):
            letter = os.path.splitdrive(root)[0].rstrip(":")   # "J:\" -> "J"
            ok, err = self._do_eject_windows(letter)
        else:
            ok, err = self._do_eject_mac(root)

        # Clear the UI regardless — the device is being disconnected
        self._disconnect_device_ui()
        if ok:
            self.statusBar().showMessage(f"{root} ejected — safe to disconnect.")
        else:
            fallback = ("Use 'Safely Remove Hardware' in the system tray."
                        if sys.platform.startswith("win")
                        else "Try ejecting it from Finder instead.")
            QMessageBox.warning(
                self, "Eject Failed",
                f"Could not safely eject {root}.\n{err}\n\n{fallback}"
            )

    @staticmethod
    def _do_eject_windows(letter: str):
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

    @staticmethod
    def _do_eject_mac(mount_path: str):
        """
        Eject a mounted volume via `diskutil eject`.
        Returns (success: bool, error_message: str).
        NOTE: written from Windows — needs verification on an actual Mac.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["diskutil", "eject", mount_path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                return True, ""
            return False, (result.stderr or result.stdout).strip()
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Device auto-detection (polled every 2 s)
    # ------------------------------------------------------------------

    def _poll_for_device(self) -> None:
        """
        Look for a Pacemaker, or check the current one is still there. A slow or sleeping
        drive can take seconds to answer, so this runs off the UI thread. During a sync the
        worker notices a lost device itself. A second device is ignored while one is loaded.
        """
        if self._device_scan_running or self._work_running():
            return
        self._device_scan_running = True
        threading.Thread(
            target=self._scan_for_device, args=(self._device_panel.db_path,), daemon=True
        ).start()

    def _scan_for_device(self, current: str) -> None:
        found = ""
        try:
            if current:
                found = current if os.path.isfile(current) else ""
            else:
                found = find_device_db()
        except Exception as e:
            _logger.log.error("Device scan failed: %s", e)
            found = current
        try:
            self._device_scan_done.emit(current, found)
        except RuntimeError:
            pass   # the window closed while scanning

    def _on_device_scan_done(self, scanned_for: str, found: str) -> None:
        self._device_scan_running = False
        if self._device_panel.db_path != scanned_for or self._work_running():
            return   # things changed while scanning: the next poll will catch up
        if scanned_for and not found:
            self._on_device_lost()
        elif found and not scanned_for:
            self._auto_load_device(found)

    def _on_device_lost(self) -> None:
        """The device vanished without being ejected, outside of any sync."""
        _logger.log.warning("Device disappeared: %s", self._device_panel.db_path)
        self._disconnect_device_ui()
        self.statusBar().showMessage(
            "Pacemaker disconnected. Use Eject before unplugging it, to keep its files safe."
        )

    def _auto_load_device(self, db_path: str) -> None:
        """Called when a Pacemaker drive is detected. Loads its library."""
        self._connect_device(db_path)
        self.statusBar().showMessage(
            f"Pacemaker detected on {device_root(db_path)} — Device Library loaded."
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

        if db_path == self._device_panel.db_path and not _backup_device_db(self, db_path)[0]:
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
        """Delete audio files in <device root>/.Pacemaker/Music not referenced in the device DB."""
        device_db = self._device_panel.db_path
        if not device_db:
            QMessageBox.warning(self, "No Device", "No device database loaded.")
            return

        music_root = device_music_root(device_db)
        if not os.path.isdir(music_root):
            QMessageBox.information(self, "Clean Up", "No Music folder found on device.")
            return

        try:
            with PacemakerWriter(device_db) as writer:
                db_tracks = writer.get_all_tracks_with_case_count()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        to_delete = find_orphaned_device_files(music_root, {t["location"] for t in db_tracks})

        if not to_delete:
            QMessageBox.information(self, "Clean Up", "No orphaned audio files found — device is clean.")
            return

        total_size = sum(os.path.getsize(p) for p in to_delete if os.path.exists(p))
        reply = QMessageBox.question(
            self, "Clean Up Orphaned Files",
            f"Found {len(to_delete)} audio file(s) on the device not in the database.\n"
            f"Total size: {total_size / 1_000_000_000:.2f} GB\n\n"
            + ("WARNING: the database lists no tracks at all, so this is every audio file on "
               "the device. If the database is damaged, these may still be your music.\n\n"
               if not db_tracks else "")
            + "Permanently delete these files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
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
            thread.wait(5000)
            self._cleaning_up = False
            self._cleanup_worker = None
            self._load_device_storage()
            if self._quit_when_idle:
                self._quit_if_requested()
                return
            QMessageBox.information(
                self, "Clean Up Complete",
                f"Deleted {deleted} file(s) and freed {freed / 1_000_000_000:.2f} GB.",
            )

        thread.started.connect(worker.run)
        worker.file_done.connect(on_file_done)
        worker.finished.connect(on_finished)
        progress.canceled.connect(worker.cancel)
        self._cleaning_up = True
        self._cleanup_thread = thread
        self._cleanup_worker = worker
        thread.start()

    # ------------------------------------------------------------------
    # Debug logging
    # ------------------------------------------------------------------

    def _toggle_debug_logging(self, checked: bool) -> None:
        if checked:
            log_path = self._debug_log_path()
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

    def _debug_log_path(self) -> str:
        """The log sits next to the Editor DB, or its default location if none is loaded."""
        editor_db = self._panel.db_path or default_editor_db_path()
        return os.path.join(os.path.dirname(editor_db), "rb_pm_sync.log")

    def _open_log_file(self) -> None:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        log_path = self._debug_log_path()
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
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._load_editor_library()
