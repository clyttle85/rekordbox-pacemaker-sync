"""
Right panel: Pacemaker DB path, sync queue summary, progress bar, and sync button.
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QListWidget, QListWidgetItem, QProgressBar,
    QFileDialog, QSizePolicy, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor


class SyncQueueItem:
    def __init__(self, playlist_id: str, name: str, track_count: int,
                 action: str, was_synced: bool):
        self.playlist_id = playlist_id
        self.name = name
        self.track_count = track_count
        self.action = action       # "add", "update", "remove"
        self.was_synced = was_synced


ACTION_COLORS = {
    "add":    "#4caf50",   # green
    "update": "#2196f3",   # blue
    "remove": "#f44336",   # red
    "none":   "#888888",   # grey
}

ACTION_LABELS = {
    "add":    "ADD",
    "update": "UPDATE",
    "remove": "REMOVE",
}


class SyncPanel(QWidget):
    db_path_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db_path: str = ""
        self._queue: list[SyncQueueItem] = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- DB path row ---
        db_label = QLabel("Pacemaker Database:")
        db_label.setFont(QFont(self.font().family(), weight=QFont.Weight.Bold))
        layout.addWidget(db_label)

        db_row = QHBoxLayout()
        self._db_edit = QLineEdit()
        self._db_edit.setPlaceholderText("Path to music.db ...")
        self._db_edit.setReadOnly(True)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._browse_db)
        db_row.addWidget(self._db_edit)
        db_row.addWidget(self._browse_btn)
        layout.addLayout(db_row)

        # --- Divider ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # --- Queue header ---
        queue_label = QLabel("Sync Queue")
        queue_label.setFont(QFont(self.font().family(), weight=QFont.Weight.Bold))
        layout.addWidget(queue_label)

        self._summary_label = QLabel("No playlists selected.")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        # --- Queue list ---
        self._queue_list = QListWidget()
        self._queue_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._queue_list, stretch=1)

        # --- Progress bar ---
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_label)

        # --- Sync button (not added to this layout — placed externally by MainWindow) ---
        self._sync_btn = QPushButton("Sync to\nEditor Library")
        self._sync_btn.setFixedHeight(50)
        font = self._sync_btn.font()
        font.setBold(True)
        self._sync_btn.setFont(font)
        self._sync_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def sync_button(self) -> QPushButton:
        return self._sync_btn

    def set_db_path(self, path: str) -> None:
        self._db_path = path
        self._db_edit.setText(path)
        self._refresh_sync_button()
        self.db_path_changed.emit(path)

    def update_queue(self, items: list[SyncQueueItem]) -> None:
        """Refresh the queue list and summary from a list of SyncQueueItems."""
        self._queue = items
        self._queue_list.clear()

        to_add = [i for i in items if i.action == "add"]
        to_update = [i for i in items if i.action == "update"]
        to_remove = [i for i in items if i.action == "remove"]

        parts = []
        if to_add:
            parts.append(f"{len(to_add)} to add")
        if to_update:
            parts.append(f"{len(to_update)} to update")
        if to_remove:
            parts.append(f"{len(to_remove)} to remove")

        if parts:
            total_tracks = sum(i.track_count for i in items if i.action != "remove")
            self._summary_label.setText(
                ", ".join(parts) + f"  ·  {total_tracks} tracks total"
            )
        else:
            self._summary_label.setText("No changes to sync.")

        for qi in items:
            color = ACTION_COLORS.get(qi.action, "#888888")
            badge = ACTION_LABELS.get(qi.action, "")
            text = f"[{badge}]  {qi.name}  ({qi.track_count} tracks)"
            list_item = QListWidgetItem(text)
            list_item.setForeground(QColor(color))
            self._queue_list.addItem(list_item)

        self._refresh_sync_button()

    def set_progress(self, value: int, total: int, status: str = "") -> None:
        if total > 0:
            pct = int((value / total) * 100)
            self._progress.setVisible(True)
            self._progress.setValue(pct)
        self._status_label.setText(status)

    def reset_progress(self) -> None:
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status_label.setText("")

    def set_syncing(self, syncing: bool) -> None:
        """Disable controls during sync."""
        self._sync_btn.setEnabled(not syncing)
        self._browse_btn.setEnabled(not syncing)
        if syncing:
            self._sync_btn.setText("Syncing…")
        else:
            self._sync_btn.setText("Sync to\nEditor Library")
            self._refresh_sync_button()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _browse_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Pacemaker music.db", "", "SQLite Database (*.db)"
        )
        if path:
            self.set_db_path(path)

    def _refresh_sync_button(self):
        has_db = bool(self._db_path)
        has_changes = any(i.action in ("add", "update", "remove") for i in self._queue)
        self._sync_btn.setEnabled(has_db and has_changes)
