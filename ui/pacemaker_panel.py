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
    QProgressBar, QTabWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QTimer
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

    delete_requested = pyqtSignal(list)        # list of {"case_id": int, "name": str}
    rename_requested = pyqtSignal(list)        # list of {"case_id": int, "name": str}  (Rename button)
    inline_rename_committed = pyqtSignal(int, str)  # (case_id, new_name) — double-click inline edit
    eject_requested  = pyqtSignal()
    refresh_requested = pyqtSignal()
    push_requested = pyqtSignal()              # Editor → Device
    db_path_changed = pyqtSignal(str)          # emitted when Browse is used
    case_selected = pyqtSignal(int)            # case_id, or -1 when nothing selected
    play_track_requested = pyqtSignal(list, int)  # (list[TrackInfo], start_index)
    track_tab_play_requested = pyqtSignal(dict)   # track dict — double-click in Tracks tab
    track_tab_selected = pyqtSignal(int)          # track_id selected in Tracks tab (-1 = none)

    def __init__(
        self,
        title: str = "Pacemaker Library",
        show_browse: bool = False,
        show_push_button: bool = False,
        show_rename: bool = False,
        show_checkboxes: bool = False,
        show_eject: bool = False,
        show_tracklist: bool = False,
        show_storage: bool = False,
        show_selection_size: bool = False,
        show_tabs: bool = False,          # Device panel: Cases + Tracks tabs
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
        self._show_tabs = show_tabs
        self._cases: list[dict] = []
        self._managed_case_ids: set[int] = set()
        self._db_path: str = ""
        self._current_tracks: list = []   # TrackInfo list for the selected case
        self._device_total_bytes: int = 0   # reported by set_storage_info()
        self._case_icon = _make_case_icon()
        # Inline rename state
        self._editing_item = None
        self._editing_case_id: int = -1
        self._editing_original_case_name: str = ""
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
        # Unified handler covers both checkbox changes and inline text edits
        self._list.itemChanged.connect(self._on_list_item_changed)
        # Detect when an inline edit is cancelled (Escape) via the delegate signal
        self._list.itemDelegate().closeEditor.connect(self._on_close_editor)
        if self._show_rename:
            self._list.itemDoubleClicked.connect(self._on_item_double_clicked)

        if self._show_tracklist:
            # Case list + per-case track list split view
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

        elif self._show_tabs:
            # Cases tab + all-tracks tab
            tracks_tab_widget = self._build_all_tracks_tab()
            self._tab_widget = QTabWidget()
            self._tab_widget.addTab(self._list, "Cases")
            self._tab_widget.addTab(tracks_tab_widget, "Tracks")
            self._track_table = None
            layout.addWidget(self._tab_widget, stretch=1)

        else:
            self._track_table = None
            self._all_tracks_table = None
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
    # All-tracks table (Tracks tab)
    # ------------------------------------------------------------------

    def _build_all_tracks_tab(self) -> QWidget:
        """Build the Tracks tab: a cases banner above the track table."""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(2)

        # Banner: shows which cases the selected track appears in
        self._track_cases_banner = QLabel("")
        self._track_cases_banner.setStyleSheet(
            "font-size: 10px; color: #aaaaaa; padding: 2px 4px;"
            "background: #252525; border-bottom: 1px solid #333;"
        )
        self._track_cases_banner.setWordWrap(True)
        self._track_cases_banner.setVisible(False)
        vbox.addWidget(self._track_cases_banner)

        # Track table
        t = QTableWidget(0, 5)
        t.setHorizontalHeaderLabels(["Title", "Artist", "BPM", "Time", "Cases"])
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        t.setColumnWidth(0, 200)
        t.setColumnWidth(1, 160)
        t.setColumnWidth(2, 40)
        t.setColumnWidth(3, 44)
        t.setColumnWidth(4, 36)
        t.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(22)
        t.setAlternatingRowColors(True)
        t.setSortingEnabled(True)
        t.itemDoubleClicked.connect(self._on_track_tab_double_clicked)
        t.itemSelectionChanged.connect(self._on_track_tab_selection_changed)
        self._all_tracks_table = t
        vbox.addWidget(t, stretch=1)
        return container

    def load_all_device_tracks(self, tracks: "list[dict]") -> None:
        """Populate the Tracks tab. tracks is from get_all_tracks_with_case_count()."""
        if not self._show_tabs or self._all_tracks_table is None:
            return
        tbl = self._all_tracks_table
        tbl.setSortingEnabled(False)
        tbl.setRowCount(0)
        orphan_color = QColor("#888888")
        for i, t in enumerate(tracks):
            tbl.insertRow(i)
            secs = int(t.get("play_time_secs") or 0)
            duration = f"{secs // 60}:{secs % 60:02d}"
            bpm_val = int(t.get("bpm") or 0)
            if bpm_val > 1000:
                bpm_val = round(bpm_val / 100)
            bpm = str(bpm_val) if bpm_val else ""
            case_count = int(t.get("case_count") or 0)
            is_orphan = case_count == 0
            for col, val in enumerate([
                t.get("title") or "",
                t.get("artist") or "",
                bpm,
                duration,
                str(case_count),
            ]):
                item = QTableWidgetItem(val)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if is_orphan:
                    item.setForeground(orphan_color)
                if col in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Store the full track dict in col-0 item for use on selection/dblclick
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, t)
                tbl.setItem(i, col, item)
        tbl.setSortingEnabled(True)
        # Update tab label with count
        orphan_count = sum(1 for t in tracks if int(t.get("case_count") or 0) == 0)
        label = f"Tracks ({len(tracks)})"
        if orphan_count:
            label += f"  ·  {orphan_count} orphaned"
        self._tab_widget.setTabText(1, label)

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
        if self._show_tabs:
            self._tab_widget.setTabText(0, f"Cases ({count})")
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

    def _on_list_item_changed(self, item: "QListWidgetItem") -> None:
        """Unified itemChanged handler: covers inline text edits and checkbox changes."""
        if self._editing_item is item:
            # User committed an inline rename
            new_name = item.text().strip()
            self._finish_inline_edit(new_name, committed=True)
            return
        # Otherwise it's a checkbox state change
        if self._show_selection_size:
            self._update_selection_size()

    def _finish_inline_edit(self, new_name: str, committed: bool) -> None:
        if self._editing_item is None:
            return
        item = self._editing_item
        case_id = self._editing_case_id
        original_name = self._editing_original_case_name
        self._editing_item = None
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if committed and new_name and new_name != original_name:
            # Update local copy so the restored text uses the new name
            for c in self._cases:
                if c["case_id"] == case_id:
                    c["name"] = new_name
                    break
            self._restore_item_formatted_text(item, case_id)
            self.inline_rename_committed.emit(case_id, new_name)
        else:
            self._restore_item_formatted_text(item, case_id)

    def _restore_item_formatted_text(self, item: "QListWidgetItem", case_id: int) -> None:
        case = next((c for c in self._cases if c["case_id"] == case_id), None)
        if not case:
            return
        track_word = "track" if case["track_count"] == 1 else "tracks"
        managed = case_id in self._managed_case_ids
        self._list.blockSignals(True)
        item.setText(f"  {case['name']}  ({case['track_count']} {track_word})")
        if managed:
            item.setForeground(QColor("#4caf50"))
        self._list.blockSignals(False)

    def _on_close_editor(self, editor, hint) -> None:
        """Fires for both commit and cancel. If still in editing state after the
        event loop ticks, itemChanged didn't fire → it was a cancel (Escape)."""
        if self._editing_item is not None:
            QTimer.singleShot(0, self._maybe_restore_after_cancel)

    def _maybe_restore_after_cancel(self) -> None:
        """Called one event-loop tick after closeEditor. If _editing_item is still
        set, the edit was cancelled rather than committed."""
        if self._editing_item is not None:
            self._finish_inline_edit("", committed=False)

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
        case = next((c for c in self._cases if c["case_id"] == case_id), None)
        if not case:
            return
        self._editing_item = item
        self._editing_case_id = case_id
        self._editing_original_case_name = case["name"]
        # Strip the " (N tracks)" suffix so the user only edits the name
        self._list.blockSignals(True)
        item.setText(case["name"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self._list.blockSignals(False)
        self._list.editItem(item)

    def _on_rename_clicked(self):
        cases = self.selected_cases()
        if cases:
            self.rename_requested.emit(cases)

    def _on_delete_clicked(self):
        cases = self.selected_cases()
        if cases:
            self.delete_requested.emit(cases)

    def set_track_cases_banner(self, case_names: "list[str]") -> None:
        """Update the 'In cases:' banner above the Tracks tab table."""
        if not self._show_tabs or not hasattr(self, "_track_cases_banner"):
            return
        if case_names:
            self._track_cases_banner.setText("In:  " + "  /  ".join(case_names))
            self._track_cases_banner.setVisible(True)
        else:
            self._track_cases_banner.setText("")
            self._track_cases_banner.setVisible(False)

    def _on_track_tab_double_clicked(self, item: "QTableWidgetItem") -> None:
        row = item.row()
        col0 = self._all_tracks_table.item(row, 0)
        if col0:
            track_dict = col0.data(Qt.ItemDataRole.UserRole)
            if track_dict:
                self.track_tab_play_requested.emit(track_dict)

    def _on_track_tab_selection_changed(self) -> None:
        selected = self._all_tracks_table.selectedItems()
        if not selected:
            self.track_tab_selected.emit(-1)
            return
        row = selected[0].row()
        col0 = self._all_tracks_table.item(row, 0)
        if col0:
            track_dict = col0.data(Qt.ItemDataRole.UserRole)
            if track_dict:
                track_id = int(track_dict.get("track_id") or -1)
                self.track_tab_selected.emit(track_id)
                return
        self.track_tab_selected.emit(-1)

    def _browse_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {self._title} music.db", "", "SQLite Database (*.db)"
        )
        if path:
            self.set_db_path_label(path)
            self.db_path_changed.emit(path)
