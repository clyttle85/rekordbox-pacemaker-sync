"""
Reusable panel for displaying a Pacemaker database (Editor or Device).
Shows all cases with track counts. Supports optional browse button (for Device
panel where path isn't fixed) and optional Push to Device button (for Editor panel).
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QFrame,
    QFileDialog, QSizePolicy, QSplitter, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QPainter, QPen


def _make_case_icon() -> QIcon:
    """Small playlist icon: three horizontal lines, 14×12 px."""
    pix = QPixmap(14, 12)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    pen = QPen(QColor("#888888"), 1.5)
    p.setPen(pen)
    for y in (2, 5, 8):
        p.drawLine(0, y, 13, y)
    p.end()
    return QIcon(pix)


def _fmt_bytes(b: int) -> str:
    """Format byte count as human-readable GB/MB string."""
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b / 1_000_000:.0f} MB"
    return f"{b / 1_000:.0f} KB"


class PacemakerLibraryPanel(QWidget):
    """Displays cases in a Pacemaker music.db and exposes actions as signals."""

    delete_requested = pyqtSignal(list)   # list of {"case_id": int, "name": str}
    rename_requested = pyqtSignal(list)   # list of {"case_id": int, "name": str}
    eject_requested  = pyqtSignal()
    refresh_requested = pyqtSignal()
    push_requested = pyqtSignal()         # Editor → Device
    db_path_changed = pyqtSignal(str)     # emitted when Browse is used
    case_selected = pyqtSignal(int)       # case_id, or -1 when nothing selected
    play_track_requested = pyqtSignal(list, int)  # (list[TrackInfo], start_index)

    def __init__(
        self,
        title: str = "Pacemaker Library",
        show_browse: bool = False,
        show_push_button: bool = False,
        show_rename: bool = False,
        show_checkboxes: bool = False,
        show_eject: bool = False,
        show_tracklist: bool = False,
        show_storage: bool = False,      # Device panel: used/total bar
        show_selection_size: bool = False,  # Editor panel: selected cases size
        parent=None,
    ):
        super().__init__(parent)
        self._title = title
        self._show_browse = show_browse
        self._show_push_button = show_push_button
        self._show_rename = show_rename
        self._show_checkboxes = show_checkboxes
        self._show_eject = show_eject
        self._show_tracklist = show_tracklist
        self._show_storage = show_storage
        self._show_selection_size = show_selection_size
        self._cases: list[dict] = []
        self._managed_case_ids: set[int] = set()
        self._db_path: str = ""
        self._current_tracks: list = []   # TrackInfo list for the selected case
        self._device_total_bytes: int = 0   # reported by set_storage_info()
        self._case_icon = _make_case_icon()
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # Title
        header = QLabel(self._title)
        header.setFont(QFont(self.font().family(), weight=QFont.Weight.Bold))
        layout.addWidget(header)

        # Path row
        path_row = QHBoxLayout()
        self._path_label = QLabel("Not connected.")
        self._path_label.setStyleSheet("color: grey; font-size: 10px;")
        self._path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        path_row.addWidget(self._path_label, stretch=1)
        if self._show_browse:
            self._browse_btn = QPushButton("Browse…")
            self._browse_btn.setFixedWidth(70)
            self._browse_btn.clicked.connect(self._browse_db)
            path_row.addWidget(self._browse_btn)
        layout.addLayout(path_row)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self._count_label = QLabel("No database loaded.")
        self._count_label.setWordWrap(True)
        layout.addWidget(self._count_label)

        # Storage bar (device panel)
        if self._show_storage:
            self._storage_bar = QProgressBar()
            self._storage_bar.setRange(0, 100)
            self._storage_bar.setValue(0)
            self._storage_bar.setFixedHeight(6)
            self._storage_bar.setTextVisible(False)
            self._storage_bar.setStyleSheet(
                "QProgressBar { background: #333; border: none; border-radius: 3px; }"
                "QProgressBar::chunk { background: #e8631a; border-radius: 3px; }"
            )
            self._storage_lbl = QLabel("")
            self._storage_lbl.setStyleSheet("color: #888; font-size: 10px;")
            layout.addWidget(self._storage_bar)
            layout.addWidget(self._storage_lbl)

        # Selected size label (editor panel)
        if self._show_selection_size:
            self._sel_size_lbl = QLabel("")
            self._sel_size_lbl.setStyleSheet("color: #888; font-size: 10px;")
            layout.addWidget(self._sel_size_lbl)

        if self._show_checkboxes:
            check_row = QHBoxLayout()
            check_row.addWidget(QLabel("Check:"))
            self._check_all_btn = QPushButton("All")
            self._check_all_btn.clicked.connect(self._check_all_items)
            self._check_none_btn = QPushButton("None")
            self._check_none_btn.clicked.connect(self._uncheck_all_items)
            check_row.addWidget(self._check_all_btn)
            check_row.addWidget(self._check_none_btn)
            check_row.addStretch()
            layout.addLayout(check_row)

        self._list = QListWidget()
        self._list.setIconSize(QSize(14, 12))
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        if self._show_checkboxes:
            self._list.itemChanged.connect(self._on_check_changed)
        if self._show_rename:
            self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        if self._show_tracklist:
            # Build track list table
            self._track_table = QTableWidget(0, 5)
            self._track_table.setHorizontalHeaderLabels(["#", "Title", "Artist", "Time", "BPM"])
            self._track_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            self._track_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self._track_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self._track_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
            self._track_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
            self._track_table.setColumnWidth(0, 30)
            self._track_table.setColumnWidth(3, 52)
            self._track_table.setColumnWidth(4, 46)
            self._track_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self._track_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self._track_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._track_table.verticalHeader().setVisible(False)
            self._track_table.setAlternatingRowColors(True)
            self._track_table.itemDoubleClicked.connect(self._on_track_double_clicked)

            splitter = QSplitter(Qt.Orientation.Vertical)
            splitter.addWidget(self._list)
            splitter.addWidget(self._track_table)
            splitter.setSizes([180, 220])
            layout.addWidget(splitter, stretch=1)
        else:
            self._track_table = None
            layout.addWidget(self._list, stretch=1)

        # Legend
        legend_row = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet("color: #4caf50;")
        legend_row.addWidget(dot)
        legend_row.addWidget(QLabel("Managed by this app"))
        legend_row.addStretch()
        layout.addLayout(legend_row)

        # Buttons
        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh_requested)
        btn_row.addWidget(self._refresh_btn)
        if self._show_eject:
            self._eject_btn = QPushButton("⏏ Eject")
            self._eject_btn.clicked.connect(self.eject_requested)
            btn_row.addWidget(self._eject_btn)
        btn_row.addStretch()
        if self._show_push_button:
            # Not added to btn_row here — placed externally by MainWindow
            self._push_btn = QPushButton("Push to\nDevice →")
            self._push_btn.setFixedHeight(50)
            font = self._push_btn.font()
            font.setBold(True)
            self._push_btn.setFont(font)
            self._push_btn.setEnabled(False)
            self._push_btn.clicked.connect(self.push_requested)
        if self._show_rename:
            self._rename_btn = QPushButton("Rename…")
            self._rename_btn.setEnabled(False)
            self._rename_btn.clicked.connect(self._on_rename_clicked)
            btn_row.addWidget(self._rename_btn)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self._delete_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def push_button(self) -> "QPushButton":
        """The Push to Device button (only valid when show_push_button=True)."""
        return self._push_btn

    def set_storage_info(self, used_bytes: int, total_bytes: int) -> None:
        """Update the storage bar in the device panel."""
        if not self._show_storage:
            return
        self._device_total_bytes = total_bytes
        if total_bytes > 0:
            pct = min(int(used_bytes * 100 / total_bytes), 100)
            self._storage_bar.setValue(pct)
            self._storage_lbl.setText(
                f"{_fmt_bytes(used_bytes)} of {_fmt_bytes(total_bytes)} used"
            )
        else:
            self._storage_bar.setValue(0)
            self._storage_lbl.setText("")

    def set_db_path_label(self, path: str) -> None:
        """Update the displayed path without emitting db_path_changed."""
        self._db_path = path
        if path:
            self._path_label.setText(path)
            self._path_label.setToolTip(path)
            self._path_label.setStyleSheet("color: #888; font-size: 10px;")
        else:
            self._path_label.setText("Not connected.")
            self._path_label.setStyleSheet("color: grey; font-size: 10px;")

    def load_cases(self, cases: list[dict], managed_case_ids: set[int]) -> None:
        """
        Populate the case list.

        cases            — list of {"case_id": int, "name": str, "track_count": int}
        managed_case_ids — case IDs tracked in sync state (shown in green)
        """
        self._cases = cases
        self._managed_case_ids = managed_case_ids
        self._list.blockSignals(True)
        self._list.clear()

        for case in cases:
            managed = case["case_id"] in managed_case_ids
            track_word = "track" if case["track_count"] == 1 else "tracks"
            text = f"  {case['name']}  ({case['track_count']} {track_word})"
            item = QListWidgetItem(self._case_icon, text)
            item.setData(Qt.ItemDataRole.UserRole, case["case_id"])
            if managed:
                item.setForeground(QColor("#4caf50"))
            if self._show_checkboxes:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(item)

        self._list.blockSignals(False)

        count = len(cases)
        self._count_label.setText(f"{count} case{'s' if count != 1 else ''} on device.")
        self._delete_btn.setEnabled(False)
        self._delete_btn.setText("Delete")

    def clear(self) -> None:
        self._cases = []
        self._managed_case_ids = set()
        self._list.clear()
        self._count_label.setText("No database loaded.")
        self._delete_btn.setEnabled(False)
        self._delete_btn.setText("Delete")
        if self._show_rename:
            self._rename_btn.setEnabled(False)
        if self._show_storage:
            self._storage_bar.setValue(0)
            self._storage_lbl.setText("")
        if self._show_selection_size:
            self._sel_size_lbl.setText("")
        if self._track_table:
            self._track_table.setRowCount(0)
            self._current_tracks = []

    def load_tracks(self, tracks: list) -> None:
        """
        Populate the track table with TrackInfo objects for the selected case.
        Only has effect when show_tracklist=True.
        """
        if not self._track_table:
            return
        self._current_tracks = tracks
        self._track_table.setRowCount(0)
        for i, t in enumerate(tracks):
            self._track_table.insertRow(i)
            secs = int(t.play_time_secs or 0)
            duration = f"{secs // 60}:{secs % 60:02d}"
            bpm = str(int(t.bpm)) if t.bpm else ""
            for col, val in enumerate([str(i + 1), t.title or "", t.artist or "", duration, bpm]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._track_table.setItem(i, col, item)

    def get_checked_cases(self) -> "list[dict]":
        """Return all cases whose checkbox is ticked (only valid when show_checkboxes=True)."""
        checked_ids = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_ids.add(item.data(Qt.ItemDataRole.UserRole))
        return [c for c in self._cases if c["case_id"] in checked_ids]

    def set_checked_cases(self, case_ids: "set[int]") -> None:
        """Programmatically set checkbox state for all cases (only valid when show_checkboxes=True)."""
        if not self._show_checkboxes:
            return
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            item = self._list.item(i)
            cid = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if cid in case_ids else Qt.CheckState.Unchecked
            )
        self._list.blockSignals(False)
        if self._show_selection_size:
            self._update_selection_size()

    def selected_case(self) -> "dict | None":
        """Return the single selected case, or None."""
        selected = self.selected_cases()
        return selected[0] if len(selected) == 1 else None

    def selected_cases(self) -> "list[dict]":
        """Return all currently selected cases."""
        selected_ids = {
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._list.selectedItems()
        }
        return [c for c in self._cases if c["case_id"] in selected_ids]

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _check_all_items(self) -> None:
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Checked)
        self._list.blockSignals(False)
        if self._show_selection_size:
            self._update_selection_size()

    def _uncheck_all_items(self) -> None:
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        if self._show_selection_size:
            self._update_selection_size()

    def _on_check_changed(self, item: "QListWidgetItem") -> None:
        """Push button state is managed externally; we just update the selection size label."""
        if self._show_selection_size:
            self._update_selection_size()

    def _update_selection_size(self) -> None:
        checked = self.get_checked_cases()
        total = sum(c.get("file_size_bytes", 0) for c in checked)
        if checked and total > 0:
            self._sel_size_lbl.setText(
                f"{len(checked)} case{'s' if len(checked) != 1 else ''} selected  —  {_fmt_bytes(total)}"
            )
        elif checked:
            self._sel_size_lbl.setText(
                f"{len(checked)} case{'s' if len(checked) != 1 else ''} selected"
            )
        else:
            self._sel_size_lbl.setText("")

    def _on_selection_changed(self):
        count = len(self._list.selectedItems())
        self._delete_btn.setEnabled(count > 0)
        self._delete_btn.setText(f"Delete ({count})" if count > 1 else "Delete")
        if self._show_rename:
            self._rename_btn.setEnabled(count >= 1)
        # Notify listeners so they can fetch + load tracks for this case
        case = self.selected_case()
        self.case_selected.emit(case["case_id"] if case else -1)

    def _on_track_double_clicked(self, item: "QTableWidgetItem") -> None:
        row = item.row()
        if self._current_tracks and 0 <= row < len(self._current_tracks):
            self.play_track_requested.emit(list(self._current_tracks), row)

    def _on_item_double_clicked(self, item: "QListWidgetItem") -> None:
        case_id = item.data(Qt.ItemDataRole.UserRole)
        for c in self._cases:
            if c["case_id"] == case_id:
                self.rename_requested.emit([c])
                break

    def _on_rename_clicked(self):
        cases = self.selected_cases()
        if cases:
            self.rename_requested.emit(cases)

    def _on_delete_clicked(self):
        cases = self.selected_cases()
        if cases:
            self.delete_requested.emit(cases)

    def _browse_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {self._title} music.db", "", "SQLite Database (*.db)"
        )
        if path:
            self.set_db_path_label(path)
            self.db_path_changed.emit(path)
