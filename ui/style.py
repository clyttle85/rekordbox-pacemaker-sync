"""
Dark theme stylesheet — Rekordbox-inspired.

Apply palette first: apply_dark_palette(app)
Then stylesheet:    app.setStyleSheet(DARK_STYLESHEET)
"""

import os as _os
import tempfile as _tempfile
from PyQt6.QtGui import QPalette, QColor

# ── Write branch-arrow SVGs to a temp dir so QSS url() can load them ─────────
# Qt on Windows does not support data: URIs in QSS url() for branch images.
_icon_dir = _os.path.join(_tempfile.gettempdir(), "rbpm_icons")
_os.makedirs(_icon_dir, exist_ok=True)

_branch_right_path = _os.path.join(_icon_dir, "branch_right.svg").replace("\\", "/")
_branch_down_path  = _os.path.join(_icon_dir, "branch_down.svg").replace("\\", "/")
_check_path        = _os.path.join(_icon_dir, "check.svg").replace("\\", "/")
_dash_path         = _os.path.join(_icon_dir, "dash.svg").replace("\\", "/")

with open(_branch_right_path.replace("/", _os.sep), "w") as _f:
    _f.write('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
             '<polygon points="2,1 7,4 2,7" fill="#aaaaaa"/></svg>')
with open(_branch_down_path.replace("/", _os.sep), "w") as _f:
    _f.write('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
             '<polygon points="1,2 7,2 4,7" fill="#aaaaaa"/></svg>')
with open(_check_path.replace("/", _os.sep), "w") as _f:
    _f.write('<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11">'
             '<polyline points="2,5 5,8 9,2" fill="none" stroke="white" stroke-width="2"/></svg>')
with open(_dash_path.replace("/", _os.sep), "w") as _f:
    _f.write('<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11">'
             '<line x1="2" y1="5.5" x2="9" y2="5.5" stroke="white" stroke-width="2"/></svg>')

ACCENT      = "#e8631a"   # Rekordbox orange
BG_MAIN     = "#1e1e1e"   # deepest background
BG_PANEL    = "#272727"   # panels, toolbars
BG_INPUT    = "#181818"   # list / table cells
BG_ALT      = "#212121"   # alternating rows
BG_HOVER    = "#333333"
BG_SELECTED = "#1a4a78"   # selection (dark blue like RB)
FG_PRIMARY  = "#d8d8d8"   # main text
FG_SECONDARY= "#777777"   # dim text
FG_HEADER   = "#ffffff"
BORDER      = "#383838"
BORDER_FOCUS= "#555555"


def apply_dark_palette(app) -> None:
    """Set a QPalette so Fusion-style widgets (checkboxes, spinners) use dark colours."""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(BG_MAIN))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(FG_PRIMARY))
    pal.setColor(QPalette.ColorRole.Base,            QColor(BG_INPUT))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(BG_ALT))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(BG_PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(FG_PRIMARY))
    pal.setColor(QPalette.ColorRole.Text,            QColor(FG_PRIMARY))
    pal.setColor(QPalette.ColorRole.Button,          QColor(BG_HOVER))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(FG_PRIMARY))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link,            QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(BG_SELECTED))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(FG_HEADER))
    pal.setColor(QPalette.ColorRole.Mid,             QColor("#808080"))  # Fusion draws arrows/borders from this
    pal.setColor(QPalette.ColorRole.Dark,            QColor(BG_MAIN))
    pal.setColor(QPalette.ColorRole.Shadow,          QColor("#000000"))
    # Disabled
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor("#555555"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#555555"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#555555"))
    app.setPalette(pal)

DARK_STYLESHEET = f"""
/* ── Global ─────────────────────────────────────────────── */
QMainWindow, QDialog, QWidget {{
    background-color: {BG_MAIN};
    color: {FG_PRIMARY};
    font-family: "Segoe UI", Arial, sans-serif;
    font-size: 12px;
}}

/* ── Menu bar ────────────────────────────────────────────── */
QMenuBar {{
    background-color: {BG_PANEL};
    color: {FG_PRIMARY};
    border-bottom: 1px solid {BORDER};
}}
QMenuBar::item:selected {{
    background-color: {BG_HOVER};
}}
QMenu {{
    background-color: {BG_PANEL};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER};
}}
QMenu::item:selected {{
    background-color: {BG_SELECTED};
}}

/* ── Status bar ──────────────────────────────────────────── */
QStatusBar {{
    background-color: {BG_PANEL};
    color: {FG_SECONDARY};
    border-top: 1px solid {BORDER};
}}

/* ── Labels ──────────────────────────────────────────────── */
QLabel {{
    color: {FG_PRIMARY};
    background-color: transparent;
}}

/* ── Buttons ─────────────────────────────────────────────── */
QPushButton {{
    background-color: {BG_HOVER};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 4px 10px;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: #444444;
    border-color: {BORDER_FOCUS};
}}
QPushButton:pressed {{
    background-color: {BG_MAIN};
}}
QPushButton:disabled {{
    color: #555555;
    border-color: #2a2a2a;
}}

/* ── Input / line edit ────────────────────────────────────── */
QLineEdit {{
    background-color: {BG_INPUT};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: {BG_SELECTED};
}}
QLineEdit:focus {{
    border-color: {BORDER_FOCUS};
}}

/* ── List widget (case lists) ─────────────────────────────── */
QListWidget {{
    background-color: {BG_INPUT};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER};
    alternate-background-color: {BG_ALT};
    outline: none;
}}
QListWidget::item {{
    padding: 4px 6px;
    border: none;
}}
QListWidget::item:selected {{
    background-color: {BG_SELECTED};
    color: {FG_HEADER};
}}
QListWidget::item:hover {{
    background-color: {BG_HOVER};
}}

/* ── Tree widget (Rekordbox playlist tree) ─────────────────── */
QTreeWidget, QTreeView {{
    background-color: {BG_INPUT};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER};
    alternate-background-color: {BG_ALT};
    outline: none;
}}
QTreeWidget::item, QTreeView::item {{
    padding: 3px 2px;
}}
QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {BG_SELECTED};
    color: {FG_HEADER};
}}
QTreeWidget::item:hover, QTreeView::item:hover {{
    background-color: {BG_HOVER};
}}
/* Branch area background */
QTreeWidget::branch, QTreeView::branch {{
    background-color: {BG_INPUT};
}}
/* Expand/collapse arrows — visible on dark background */
QTreeWidget::branch:has-children:!has-siblings:closed,
QTreeWidget::branch:closed:has-children:has-siblings,
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    image: url("{_branch_right_path}");
}}
QTreeWidget::branch:open:has-children:!has-siblings,
QTreeWidget::branch:open:has-children:has-siblings,
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    image: url("{_branch_down_path}");
}}
/* Tristate checkboxes in tree */
QTreeWidget::indicator {{
    width: 13px;
    height: 13px;
    border-radius: 2px;
}}
QTreeWidget::indicator:unchecked {{
    background-color: {BG_PANEL};
    border: 1px solid #666666;
}}
QTreeWidget::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    image: url("{_check_path}");
}}
QTreeWidget::indicator:indeterminate {{
    background-color: {BG_SELECTED};
    border: 1px solid {BG_SELECTED};
    image: url("{_dash_path}");
}}
/* Checkboxes in list widgets (Editor/Device cases) */
QListWidget::indicator {{
    width: 13px;
    height: 13px;
    border-radius: 2px;
}}
QListWidget::indicator:unchecked {{
    background-color: {BG_PANEL};
    border: 1px solid #666666;
}}
QListWidget::indicator:checked {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT};
    image: url("{_check_path}");
}}

/* ── Table widget (track list) ────────────────────────────── */
QTableWidget {{
    background-color: {BG_INPUT};
    color: {FG_PRIMARY};
    border: 1px solid {BORDER};
    alternate-background-color: {BG_ALT};
    gridline-color: {BORDER};
    outline: none;
    selection-background-color: {BG_SELECTED};
}}
QTableWidget::item {{
    padding: 2px 4px;
    border: none;
}}
QTableWidget::item:selected {{
    background-color: {BG_SELECTED};
    color: {FG_HEADER};
}}
QHeaderView::section {{
    background-color: {BG_PANEL};
    color: {FG_SECONDARY};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
    font-weight: bold;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QHeaderView::section:hover {{
    background-color: {BG_HOVER};
    color: {FG_PRIMARY};
}}
QHeaderView::section:pressed {{
    background-color: {BG_MAIN};
}}

/* ── Splitter ────────────────────────────────────────────── */
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical   {{ height: 1px; }}

/* ── Scroll bars ─────────────────────────────────────────── */
QScrollBar:vertical {{
    background-color: {BG_MAIN};
    width: 8px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: #555555;
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar::handle:vertical:hover {{ background-color: #777777; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background-color: {BG_MAIN};
    height: 8px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: #555555;
    border-radius: 4px;
    min-width: 20px;
}}
QScrollBar::handle:horizontal:hover {{ background-color: #777777; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

/* ── Frames / separators ─────────────────────────────────── */
QFrame[frameShape="4"],  /* HLine */
QFrame[frameShape="5"]   /* VLine */ {{
    color: {BORDER};
}}

/* ── Sliders (player bar) ─────────────────────────────────── */
QSlider::groove:horizontal {{
    background-color: #444444;
    height: 3px;
    border-radius: 1px;
}}
QSlider::handle:horizontal {{
    background-color: {FG_PRIMARY};
    width: 10px;
    height: 10px;
    margin: -4px 0;
    border-radius: 5px;
}}
QSlider::sub-page:horizontal {{
    background-color: {ACCENT};
    border-radius: 1px;
}}
QSlider::groove:horizontal:disabled {{
    background-color: #333333;
}}
QSlider::handle:horizontal:disabled {{
    background-color: #555555;
}}

/* ── Progress dialog ─────────────────────────────────────── */
QProgressDialog {{
    background-color: {BG_PANEL};
}}
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: 3px;
    text-align: center;
    color: {FG_PRIMARY};
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 2px;
}}

/* ── Message box ─────────────────────────────────────────── */
QMessageBox {{
    background-color: {BG_PANEL};
}}

/* ── Input dialog ────────────────────────────────────────── */
QInputDialog {{
    background-color: {BG_PANEL};
}}

/* ── File dialog ─────────────────────────────────────────── */
QFileDialog {{
    background-color: {BG_PANEL};
}}

/* ── Check boxes ─────────────────────────────────────────── */
QCheckBox {{
    color: {FG_PRIMARY};
    spacing: 6px;
}}
"""
