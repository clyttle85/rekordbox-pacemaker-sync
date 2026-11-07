from __future__ import annotations

import sys
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QPalette, QColor
from ui.main_window import MainWindow
from ui.style import DARK_STYLESHEET, apply_dark_palette

SELFTEST_FLAG = "--selftest"
# Long enough for the 2-second device poll timer to fire at least once
SELFTEST_RUN_MS = 5000


def _selftest(app: QApplication) -> int:
    """Hidden CI check: exercise the bundled libraries, open the window, then exit."""
    import traceback
    from core.selftest import run_app_checks

    # A modal dialog would hang a headless run. CI machines have no Rekordbox
    # library, so the "not found" warning is expected here.
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda _parent, title, text, *_a, **_k: print(
            f"[selftest] {title}: {text}", flush=True
        )))

    try:
        run_app_checks()
        window = MainWindow()
        window.show()
    except Exception:
        traceback.print_exc()
        return 1
    QTimer.singleShot(SELFTEST_RUN_MS, app.quit)
    code = app.exec()
    print(f"[selftest] finished with exit code {code}", flush=True)
    return code


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Rekordbox → Pacemaker Sync")
    # Fusion style respects QSS/QPalette for checkboxes, tree arrows, sliders
    app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(DARK_STYLESHEET)
    if SELFTEST_FLAG in sys.argv:
        sys.exit(_selftest(app))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
