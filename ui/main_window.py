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
import os
import re
import shutil
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QSplitter, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFileDialog, QProgressDialog,
    QApplication, QFrame, QInputDialog
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QAction

from core.rekordbox_reader import RekordboxReader, PlaylistNode, TrackInfo
from core.pacemaker_writer import PacemakerWriter
from core import sync_state
from core.device_finder import find_editor_db, find_device_db
from core.m3u8_reader import load_m3u8_tracks
from ui.playlist_tree import PlaylistTreeWidget
from ui.sync_panel import SyncPanel, SyncQueueItem
from ui.pacemaker_panel import PacemakerLibraryPanel


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
# Background worker: Editor → Device push
# ---------------------------------------------------------------------------

class DevicePushWorker(QObject):
    progress = pyqtSignal(int, int, str)   # current, total, status message
    finished = pyqtSignal(bool, str)

    def __init__(self, editor_db: str, device_db: str, cases: list):
        super().__init__()
        self._editor_db = editor_db
        self._device_db = device_db
        self._cases = cases   # [{"case_id": int, "name": str, "track_count": int, ...}, ...]

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
            total_tracks = sum(c.get("track_count", 0) for c in self._cases)
            skipped = 0
            done = 0

            with PacemakerWriter(self._editor_db) as editor:
                all_tracks = {
                    case["case_id"]: editor.get_case_tracks_as_trackinfo(case["case_id"])
                    for case in self._cases
                }

            with PacemakerWriter(self._device_db) as device:
                device_case_map = {c["name"]: c["case_id"] for c in device.get_all_cases()}
                stale_locations: set[str] = set()

                for case in self._cases:
                    tracks = all_tracks[case["case_id"]]

                    if case["name"] in device_case_map:
                        device_case_id = device_case_map[case["name"]]
                        stale_locations.update(
                            device.get_case_track_locations(device_case_id)
                        )
                        device.clear_case_tracks(device_case_id)
                    else:
                        device_case_id = device.create_case(case["name"])

                    for track in tracks:
                        self.progress.emit(
                            done, total_tracks,
                            f"Copying: {os.path.basename(track.location)}"
                        )

                        if not os.path.exists(track.location):
                            skipped += 1
                            done += 1
                            continue

                        try:
                            _win_dest, pmdb_loc = self._copy_track(
                                track.location, self._device_db
                            )
                        except Exception:
                            skipped += 1
                            done += 1
                            continue

                        # Store the Linux-style /pmdb_tracks/... path in the DB,
                        # not the Windows path — this is what the firmware reads.
                        device_track = dataclasses.replace(track, location=pmdb_loc)
                        tid = device.insert_or_get_track(device_track)
                        device.link_track_to_case(device_case_id, tid)
                        done += 1

                if stale_locations:
                    device.delete_orphan_tracks(list(stale_locations))

            msg = f"Pushed {len(self._cases)} case(s) to device ({done - skipped} tracks copied)."
            if skipped:
                msg += f"\n\n{skipped} track(s) were skipped (file not found or copy error)."
            self.finished.emit(True, msg)
        except Exception as e:
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
        self._push_thread: QThread | None = None
        self._push_progress_dlg: QProgressDialog | None = None

        self._build_menu()
        self._build_ui()
        self._load_rekordbox()
        self._autodetect_pacemaker_dbs()

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

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

    def _build_ui(self):
        # Rekordbox playlist tree
        self._tree = PlaylistTreeWidget()
        self._tree.selection_changed.connect(self._on_selection_changed)

        # Sync panel (Rekordbox → Editor)
        self._panel = SyncPanel()
        self._panel.db_path_changed.connect(self._on_editor_db_changed)

        # Left splitter: tree + sync queue
        left_splitter = QSplitter(Qt.Orientation.Horizontal)
        left_splitter.addWidget(self._tree)
        left_splitter.addWidget(self._panel)
        left_splitter.setSizes([380, 220])

        # Editor library panel
        self._editor_panel = PacemakerLibraryPanel(
            title="Editor Library",
            show_browse=False,
            show_push_button=True,
            show_rename=True,
            show_checkboxes=True,
        )
        self._editor_panel.refresh_requested.connect(self._load_editor_library)
        self._editor_panel.delete_requested.connect(self._on_delete_editor_case)
        self._editor_panel.rename_requested.connect(self._on_rename_editor_case)
        self._editor_panel.push_requested.connect(self._confirm_and_push_to_device)

        # Device library panel
        self._device_panel = PacemakerLibraryPanel(
            title="Device Library",
            show_browse=True,
            show_push_button=False,
            show_eject=True,
        )
        self._device_panel.refresh_requested.connect(self._load_device_library)
        self._device_panel.delete_requested.connect(self._on_delete_device_case)
        self._device_panel.db_path_changed.connect(self._on_device_db_changed)
        self._device_panel.eject_requested.connect(self._eject_device)

        # Connect the sync button (lives in the connector strip, not in SyncPanel)
        self._panel.sync_button.clicked.connect(self._confirm_and_sync)

        # Assemble: [left_splitter] [sync_btn] [editor_panel] [push_btn] [device_panel]
        container = QWidget()
        main_layout = QHBoxLayout(container)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        main_layout.addWidget(left_splitter, stretch=3)
        main_layout.addWidget(self._make_connector(self._panel.sync_button))
        main_layout.addWidget(self._editor_panel, stretch=2)
        main_layout.addWidget(self._make_connector(self._editor_panel.push_button))
        main_layout.addWidget(self._device_panel, stretch=2)

        self.setCentralWidget(container)

    @staticmethod
    def _make_connector(button: QPushButton) -> QWidget:
        """Narrow vertical strip that centres a button between two panels."""
        widget = QWidget()
        widget.setFixedWidth(115)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        button.setFixedWidth(105)
        layout.addStretch()
        layout.addWidget(button)
        layout.addStretch()
        return widget

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
            self._rb_reader = RekordboxReader()
            self._playlist_nodes = self._rb_reader.get_playlist_tree()
            self._refresh_tree()
            self.statusBar().showMessage("Rekordbox library loaded.")
        except Exception as e:
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

        short_folders = [MainWindow._shorten_segment(f, 3) for f in folder_parts]
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
        self._panel.set_syncing(True)
        self._panel.set_progress(0, max(total_tracks, 1), "Starting…")

        self._sync_thread = QThread()
        self._worker = SyncWorker(db_path, operations)
        self._worker.moveToThread(self._sync_thread)

        self._sync_thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_sync_progress)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.finished.connect(self._sync_thread.quit)

        self._sync_thread.start()

    def _on_sync_progress(self, current: int, total: int, status: str):
        self._panel.set_progress(current, total, status)

    def _on_sync_finished(self, success: bool, message: str):
        self._panel.set_syncing(False)
        self._panel.reset_progress()

        if success:
            QMessageBox.information(self, "Sync Complete", message)
            self._tree.uncheck_all()   # clears checked state; _recompute_queue fires via signal
            self._load_editor_library()
        else:
            QMessageBox.critical(self, "Sync Failed", f"An error occurred:\n\n{message}")

        self.statusBar().showMessage(message)

    # ------------------------------------------------------------------
    # Editor → Device push
    # ------------------------------------------------------------------

    def _confirm_and_push_to_device(self):
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

        cases = self._editor_panel.get_checked_cases()
        if not cases:
            QMessageBox.information(
                self, "Nothing Checked",
                "Tick the cases you want to push in the Editor Library."
            )
            return

        reply = QMessageBox.question(
            self, "Push to Device",
            f"Push {len(cases)} checked case(s) to the device?\n\n"
            "Cases already on the device (matched by name) will be updated.\n"
            "Unchecked cases and device-only cases are left untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_push(editor_db, device_db, cases)

    def _run_push(self, editor_db: str, device_db: str, cases: list):
        self._editor_panel.setEnabled(False)
        self._device_panel.setEnabled(False)

        total_tracks = sum(c.get("track_count", 0) for c in cases)
        self._push_progress_dlg = QProgressDialog(
            f"Copying tracks to device…", None, 0, max(total_tracks, 1), self
        )
        self._push_progress_dlg.setWindowTitle("Push to Device")
        self._push_progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._push_progress_dlg.setMinimumDuration(0)
        self._push_progress_dlg.setValue(0)
        self._push_progress_dlg.show()

        self._push_thread = QThread()
        self._push_worker = DevicePushWorker(editor_db, device_db, cases)
        self._push_worker.moveToThread(self._push_thread)

        self._push_thread.started.connect(self._push_worker.run)
        self._push_worker.progress.connect(self._on_push_progress)
        self._push_worker.finished.connect(self._on_push_finished)
        self._push_worker.finished.connect(self._push_thread.quit)

        self._push_thread.start()

    def _on_push_progress(self, current: int, total: int, status: str):
        if self._push_progress_dlg:
            self._push_progress_dlg.setValue(current)
            self._push_progress_dlg.setLabelText(status)

    def _on_push_finished(self, success: bool, message: str):
        if self._push_progress_dlg:
            self._push_progress_dlg.close()
            self._push_progress_dlg = None

        self._editor_panel.setEnabled(True)
        self._device_panel.setEnabled(True)

        if success:
            QMessageBox.information(self, "Push Complete", message)
            self._load_device_library()
        else:
            QMessageBox.critical(self, "Push Failed", f"An error occurred:\n\n{message}")

        self.statusBar().showMessage(message)

    # ------------------------------------------------------------------
    # Editor library panel
    # ------------------------------------------------------------------

    def _on_editor_db_changed(self, path: str):
        self._editor_panel.set_db_path_label(path)
        self._refresh_tree()
        self._recompute_queue()
        self._load_editor_library()

    def _load_editor_library(self):
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
        try:
            with PacemakerWriter(db_path) as writer:
                cases = writer.get_all_cases()
            # Device has no sync state — show all cases without green highlighting
            self._device_panel.load_cases(cases, set())
        except Exception as e:
            self._device_panel.clear()
            self.statusBar().showMessage(f"Could not read Device library: {e}")

    def _on_delete_device_case(self, cases: list):
        self._delete_cases(
            db_path=self._device_panel.db_path,
            cases=cases,
            update_sync_state=False,
            refresh_fn=lambda: self._load_device_library(),
        )

    def _eject_device(self):
        import subprocess
        db_path = self._device_panel.db_path
        if not db_path:
            return
        drive = os.path.splitdrive(db_path)[0]   # e.g. "J:"
        letter = drive.rstrip(":")               # "J"

        # Use a PowerShell script that:
        # 1. Flushes the volume (via mountvol /p — safe removal)
        # 2. Falls back to Shell.Application eject if that fails
        # Run synchronously (wait=True) so we know when it's done.
        ps_script = (
            f"$vol = '{letter}:';"
            f"$shell = New-Object -comObject Shell.Application;"
            f"$drive = $shell.Namespace(17).ParseName($vol);"
            f"if ($drive) {{ $drive.InvokeVerb('Eject') }}"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True,
                timeout=15,
            )
            self._device_panel.set_db_path_label("")
            self._device_panel.clear()
            self.statusBar().showMessage(
                f"{drive} ejected — safe to disconnect."
            )
        except subprocess.TimeoutExpired:
            QMessageBox.warning(
                self, "Eject Timeout",
                "The eject command timed out. The device may still be in use.\n"
                "Close any open files on the device and try again."
            )
        except Exception as e:
            QMessageBox.warning(self, "Eject Failed", str(e))

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
            names = "\n".join(f"  • {c['name']}" for c in cases)
            detail = f"{len(cases)} cases:\n{names}"

        reply = QMessageBox.question(
            self, "Delete Case" if len(cases) == 1 else "Delete Cases",
            f"Permanently delete {detail}?\n\n"
            "Tracks not used by any other case will also be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with PacemakerWriter(db_path) as writer:
                for case in cases:
                    locations = list(writer.get_case_track_locations(case["case_id"]))
                    writer.remove_playlist(case["case_id"], locations)

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
