import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor
from ui.main_window import MainWindow
from ui.style import DARK_STYLESHEET, apply_dark_palette


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Rekordbox → Pacemaker Sync")
    # Fusion style respects QSS/QPalette for checkboxes, tree arrows, sliders
    app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(DARK_STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
