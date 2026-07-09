"""
Application-wide debug logger.

Usage:
    from core.logger import log
    log.debug("message")
    log.info("message")
    log.warning("message")
    log.error("message", exc_info=True)

Logging is disabled by default.  Call enable(path) to activate it and
disable() to turn it off again.  The menu in MainWindow drives this.
"""

from __future__ import annotations
import logging
import os

_LOG_NAME = "rb_pm_sync"
_FILE_HANDLER: logging.FileHandler | None = None

log = logging.getLogger(_LOG_NAME)
log.setLevel(logging.DEBUG)
log.addHandler(logging.NullHandler())   # silent by default


def enabled() -> bool:
    return _FILE_HANDLER is not None


def enable(log_path: str) -> str:
    """Start logging to *log_path*.  Returns the resolved path."""
    global _FILE_HANDLER
    if _FILE_HANDLER is not None:
        disable()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(handler)
    _FILE_HANDLER = handler
    log.info("=== Logging started  (log file: %s) ===", log_path)
    return log_path


def disable() -> None:
    global _FILE_HANDLER
    if _FILE_HANDLER is None:
        return
    log.info("=== Logging stopped ===")
    log.removeHandler(_FILE_HANDLER)
    _FILE_HANDLER.close()
    _FILE_HANDLER = None
