"""
Reusable panel for displaying a Pacemaker database (Editor or Device).
Shows all cases with track counts. Supports optional browse button (for Device
panel where path isn't fixed) and optional Push to Device button (for Editor panel).
"""

from __future__ import annotations
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QFrame,
    QFileDialog, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor


class PacemakerLibraryPanel(QWidget):
    """Displays cases in a Pacemaker music.db and exposes actions as signals."""

    delete_requested = pyqtSignal(list)   # list of {"case_id": int, "name": str}
    rename_requested = pyqtSignal(list)   # list of {"case_id": int, "name": str}
    eject_requested  = pyqtSignal()
    refresh_requested = pyqtSignal()
    push_requested = pyqtSignal()         # Editor → Device
    db_path_changed = pyqtSignal(str)     # emitted when Browse is used

    def __init__(
        self,
        title: str = "Pacemaker Library",
        show_browse: bool = False,
        show_push_button: bool = False,
        show_rename: bool = False,
        show_checkboxes: bool = False,
        show_eject: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._title = title
        self._show_browse = show_browse
        self._show_push_button = show_push_button
        self._show_rename = show_rename
        self._show_checkboxes = show_checkboxes
        self._show_eject = show_eject
        self._cases: list[dict] = []
        self._managed_case_ids: set[int] = set()
        self._db_path: str = ""
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

        if self._show_checkboxes:
            check_row = QHBoxLayout()
            check_row.addWidget(QLabel("Check:"))
            self._check_all_btn = QPushButton("All")
            self._check_all_btn.setFixedWidth(36)
            self._check_all_btn.clicked.connect(self._check_all_items)
            self._check_none_btn = QPushButton("None")
            self._check_none_btn.setFixedWidth(44)
            self._check_none_btn.clicked.connect(self._uncheck_all_items)
            check_row.addWidget(self._check_all_btn)
            check_row.addWidget(self._check_none_btn)
            check_row.addStretch()
            layout.addLayout(check_row)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        if self._show_checkboxes:
            self._list.itemChanged.connect(self._on_check_changed)
        if self._show_rename:
            self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
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
            text = f"{case['name']}  ({case['track_count']} {track_word})"
            item = QListWidgetItem(text)
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
        if self._show_push_button:
            self._push_btn.setEnabled(False)
            self._push_btn.setText("Push to\nDevice →")

    def clear(self) -> None:
        self._cases = []
        self._managed_case_ids = set()
        self._list.clear()
        self._count_label.setText("No database loaded.")
        self._delete_btn.setEnabled(False)
        self._delete_btn.setText("Delete")
        if self._show_rename:
            self._rename_btn.setEnabled(False)
        if self._show_push_button:
            self._push_btn.setEnabled(False)

    def get_checked_cases(self) -> "list[dict]":
        """Return all cases whose checkbox is ticked (only valid when show_checkboxes=True)."""
        checked_ids = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_ids.add(item.data(Qt.ItemDataRole.UserRole))
        return [c for c in self._cases if c["case_id"] in checked_ids]

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
        self._update_push_button()

    def _uncheck_all_items(self) -> None:
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._update_push_button()

    def _on_check_changed(self, item: "QListWidgetItem") -> None:
        self._update_push_button()

    def _update_push_button(self) -> None:
        if not self._show_push_button:
            return
        count = len(self.get_checked_cases())
        self._push_btn.setEnabled(count > 0)
        self._push_btn.setText(
            f"Push ({count}) to\nDevice →" if count > 0 else "Push to\nDevice →"
        )

    def _on_selection_changed(self):
        count = len(self._list.selectedItems())
        self._delete_btn.setEnabled(count > 0)
        self._delete_btn.setText(f"Delete ({count})" if count > 1 else "Delete")
        if self._show_rename:
            self._rename_btn.setEnabled(count >= 1)

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
