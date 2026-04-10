"""
Left panel: Rekordbox playlist tree with checkboxes.

- Folders are non-checkable; checking a folder checks all its children.
- Previously synced playlists are pre-checked (loaded from sync state).
- Emits a signal when the selection changes so the sync panel can update.
- Rekordbox-inspired appearance: grey folder icons, white playlist text,
  grey right-aligned track count column.
"""

from __future__ import annotations
import json
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem, QHeaderView
from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QPainter, QPen, QBrush

from core.rekordbox_reader import PlaylistNode

_FOLDER_COLOR  = QColor("#777777")
_PLAYLIST_COLOR = QColor("#d8d8d8")
_COUNT_COLOR   = QColor("#555555")


def _make_folder_icon() -> QIcon:
    """Small grey folder icon — 16×13 px."""
    pix = QPixmap(16, 13)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    col = QColor("#666666")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(col))
    p.drawRoundedRect(0, 3, 16, 10, 1, 1)   # body
    p.drawRoundedRect(0, 1, 7, 5, 1, 1)     # tab
    p.end()
    return QIcon(pix)


def _make_playlist_icon() -> QIcon:
    """Three horizontal lines — playlist symbol, 16×13 px."""
    pix = QPixmap(16, 13)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    pen = QPen(QColor("#777777"), 1.5)
    p.setPen(pen)
    for y in (3, 6, 9):
        p.drawLine(1, y, 14, y)
    p.end()
    return QIcon(pix)


class PlaylistTreeWidget(QTreeWidget):
    selection_changed = pyqtSignal()  # emitted whenever any checkbox changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self._folder_icon   = _make_folder_icon()
        self._playlist_icon = _make_playlist_icon()

        self.setColumnCount(2)
        header = self.header()
        header.hide()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 186)
        self.setColumnWidth(1, 32)

        self.setAnimated(True)
        self.setIconSize(QSize(14, 12))
        self._settings = QSettings("rekordbox-pacemaker-sync", "PlaylistTree")
        self._updating = False  # guard against recursive itemChanged signals
        self.itemChanged.connect(self._on_item_changed)
        self.itemExpanded.connect(self._on_item_expanded)
        self.itemCollapsed.connect(self._on_item_collapsed)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Keep column 0 filling the available width; column 1 stays fixed at 32px."""
        count_w = 32
        name_w = max(self.viewport().width() - count_w, 40)
        self.setColumnWidth(0, name_w)
        self.setColumnWidth(1, count_w)

    def load_tree(self, nodes: list[PlaylistNode], synced_ids: set[str]) -> None:
        """Populate the tree from a list of PlaylistNodes."""
        self._updating = True
        self.clear()
        for node in nodes:
            item = self._build_item(node, synced_ids)
            self.addTopLevelItem(item)
        self._restore_expansion()
        self._updating = False

    def _build_item(self, node: PlaylistNode, synced_ids: set[str]) -> QTreeWidgetItem:
        item = QTreeWidgetItem()
        item.setData(0, Qt.ItemDataRole.UserRole, node.id)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, node.is_folder)

        if node.is_folder:
            item.setText(0, node.name)
            item.setIcon(0, self._folder_icon)
            item.setForeground(0, _FOLDER_COLOR)
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            for child_node in node.children:
                item.addChild(self._build_item(child_node, synced_ids))
        else:
            item.setText(0, node.name)
            item.setText(1, str(node.track_count) if node.track_count else "")
            item.setIcon(0, self._playlist_icon)
            item.setForeground(0, _PLAYLIST_COLOR)
            item.setForeground(1, _COUNT_COLOR)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = node.id in synced_ids
            item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

        return item

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._updating = True
        is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if is_folder:
            state = item.checkState(0)
            if state != Qt.CheckState.PartiallyChecked:
                self._set_children_check_state(item, state)
        self._updating = False
        self.selection_changed.emit()

    def _set_children_check_state(self, parent: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for i in range(parent.childCount()):
            child = parent.child(i)
            is_folder = child.data(0, Qt.ItemDataRole.UserRole + 1)
            child.setCheckState(0, state)
            if is_folder:
                self._set_children_check_state(child, state)

    def get_checked_playlist_ids(self) -> list[str]:
        """Return IDs of all checked (non-folder) playlists."""
        ids = []
        self._collect_checked(self.invisibleRootItem(), ids)
        return ids

    def uncheck_all(self) -> None:
        """Uncheck every playlist item in the tree."""
        self._updating = True
        self._set_children_check_state(self.invisibleRootItem(), Qt.CheckState.Unchecked)
        self._updating = False
        self.selection_changed.emit()

    def _collect_checked(self, parent: QTreeWidgetItem, ids: list[str]) -> None:
        for i in range(parent.childCount()):
            item = parent.child(i)
            is_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if is_folder:
                self._collect_checked(item, ids)
            elif item.checkState(0) == Qt.CheckState.Checked:
                ids.append(item.data(0, Qt.ItemDataRole.UserRole))

    # ------------------------------------------------------------------
    # Expansion state persistence
    # ------------------------------------------------------------------

    def _expanded_ids(self) -> set:
        raw = self._settings.value("expanded_folders", "[]")
        try:
            return set(json.loads(raw))
        except Exception:
            return set()

    def _save_expanded_ids(self, ids: set) -> None:
        self._settings.setValue("expanded_folders", json.dumps(list(ids)))

    def _restore_expansion(self) -> None:
        """Expand only the folders whose IDs were saved; everything else stays collapsed."""
        expanded = self._expanded_ids()
        self._apply_expansion(self.invisibleRootItem(), expanded)

    def _apply_expansion(self, parent: QTreeWidgetItem, expanded: set) -> None:
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item.data(0, Qt.ItemDataRole.UserRole + 1):  # is folder
                if item.data(0, Qt.ItemDataRole.UserRole) in expanded:
                    item.setExpanded(True)
                self._apply_expansion(item, expanded)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if self._updating or not item.data(0, Qt.ItemDataRole.UserRole + 1):
            return
        ids = self._expanded_ids()
        ids.add(item.data(0, Qt.ItemDataRole.UserRole))
        self._save_expanded_ids(ids)

    def _on_item_collapsed(self, item: QTreeWidgetItem) -> None:
        if self._updating or not item.data(0, Qt.ItemDataRole.UserRole + 1):
            return
        ids = self._expanded_ids()
        ids.discard(item.data(0, Qt.ItemDataRole.UserRole))
        self._save_expanded_ids(ids)
