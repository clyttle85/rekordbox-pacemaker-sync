"""
Auto-detection of Pacemaker Editor and Device database paths, and the
cross-platform path helpers built on top of a device music.db's location.

Editor DB:
  Windows — %APPDATA%\\Tonium\\Pacemaker\\music.db
  macOS   — ~/Library/Application Support/RekordboxPacemakerSync/music.db
            (this app's own location — there's no equivalent legacy Tonium
            Editor install on Mac to mirror, so it owns this path itself)

Device DB:  <device root>/.Pacemaker/music.db  (hidden folder on the device)
  Windows — <device root> is a drive letter, e.g. "J:\\"
  macOS   — <device root> is a mount point under /Volumes, e.g. "/Volumes/PACEMAKER"
"""

from __future__ import annotations

import os
import string
import sys

from core.db_schema import create_empty_editor_db

_IS_MAC = sys.platform == "darwin"
_IS_WINDOWS = sys.platform.startswith("win")


def _editor_db_candidate() -> str:
    """Where the Editor music.db lives (or should be created) on this OS."""
    if _IS_MAC:
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support",
            "RekordboxPacemakerSync", "music.db"
        )
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Tonium", "Pacemaker", "music.db")


def find_editor_db() -> str:
    """Return the Pacemaker Editor database path if it exists, else empty string."""
    candidate = _editor_db_candidate()
    return candidate if os.path.isfile(candidate) else ""


def ensure_editor_db() -> str:
    """
    Return the Editor database path, creating a fresh empty one (with the
    full Pacemaker schema) at the per-platform default location if none
    exists yet. Applies on both Windows and Mac — a Windows install that
    never had the real Tonium Editor run on it also benefits.
    """
    existing = find_editor_db()
    if existing:
        return existing
    candidate = _editor_db_candidate()
    os.makedirs(os.path.dirname(candidate), exist_ok=True)
    create_empty_editor_db(candidate)
    return candidate


def find_device_db() -> str:
    """
    Scan for a connected Pacemaker device and return its music.db path, or
    empty string if none is found.

    Windows — scans drive letters A-Z for <letter>:\\.Pacemaker\\music.db
    macOS   — scans /Volumes/* for <mount>/.Pacemaker/music.db
    """
    if _IS_MAC:
        volumes = "/Volumes"
        try:
            mounts = os.listdir(volumes)
        except OSError:
            return ""
        for name in mounts:
            candidate = os.path.join(volumes, name, ".Pacemaker", "music.db")
            if os.path.isfile(candidate):
                return candidate
        return ""

    for letter in string.ascii_uppercase:
        root = letter + ":" + os.sep
        if not os.path.exists(root):
            continue
        candidate = os.path.join(root, ".Pacemaker", "music.db")
        if os.path.isfile(candidate):
            return candidate
    return ""


# ---------------------------------------------------------------------------
# Device-root path helpers — used throughout the app wherever a path needs to
# be derived from a device music.db's location (the Music folder, the sync
# map file, ownership checks, the storage label). Pure path-structure logic:
# every device_db is always "<device root>/.Pacemaker/music.db", so climbing
# two directories up from it gives the device root on either OS without any
# platform branching.
# ---------------------------------------------------------------------------

def device_root(device_db_path: str) -> str:
    """<device root>/.Pacemaker/music.db -> <device root>."""
    return os.path.dirname(os.path.dirname(device_db_path))


def device_music_root(device_db_path: str) -> str:
    """<device root>/.Pacemaker/music.db -> <device root>/.Pacemaker/Music."""
    return os.path.join(device_root(device_db_path), ".Pacemaker", "Music")


def is_on_device(path: str, device_db_path: str) -> bool:
    """True if `path` lives under the same device root as `device_db_path`."""
    root = os.path.normcase(os.path.abspath(device_root(device_db_path)))
    return os.path.normcase(os.path.abspath(path)).startswith(root)
