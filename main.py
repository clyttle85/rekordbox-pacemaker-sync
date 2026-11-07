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

    # A modal dialog would hang a headless run, so print any message instead.
    for name in ("information", "warning", "critical"):
        setattr(QMessageBox, name, staticmethod(lambda _parent, title, text, *_a, **_k: print(
            f"[selftest] {title}: {text!a}", flush=True   # !a: Windows consoles aren't UTF-8
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


def _install_crash_handler() -> None:
    """
    PyQt6 ends the app on any error it can't hand to a Python caller. Show it instead, so an
    unexpected problem doesn't lose the user's session. --selftest doesn't use this, so that
    errors still fail the CI smoke test.
    """
    import traceback
    from core.logger import log

    def handle(exc_type, exc, tb):
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        print(details, file=sys.stderr, flush=True)
        log.error("Unexpected error:\n%s", details)
        QMessageBox.critical(
            None, "Something Went Wrong",
            f"An unexpected problem occurred, but the app is still running.\n\n{exc}\n\n"
            "If it keeps happening, turn on Debug → Enable Debug Logging and report the log file."
        )

    sys.excepthook = handle


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Rekordbox → Pacemaker Sync")
    # Fusion style respects QSS/QPalette for checkboxes, tree arrows, sliders
    app.setStyle("Fusion")
    apply_dark_palette(app)
    app.setStyleSheet(DARK_STYLESHEET)
    if SELFTEST_FLAG in sys.argv:
        sys.exit(_selftest(app))
    _install_crash_handler()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
