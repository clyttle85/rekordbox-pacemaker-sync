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
import re
import string
import sys

from core.audio_formats import ALL_AUDIO_EXTENSIONS, extension
from core.db_schema import create_empty_editor_db

_IS_MAC = sys.platform == "darwin"
_IS_WINDOWS = sys.platform.startswith("win")


def default_editor_db_path() -> str:
    """Where the Editor music.db lives (or should be created) on this OS."""
    if _IS_MAC:
        return os.path.join(
            os.path.expanduser("~"), "Library", "Application Support",
            "RekordboxPacemakerSync", "music.db"
        )
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Tonium", "Pacemaker", "music.db")


def device_backup_dir() -> str:
    """Where backups of Pacemaker databases go: on the computer, beside the Editor DB."""
    return os.path.join(os.path.dirname(default_editor_db_path()), "PacemakerBackups")


def find_editor_db() -> str:
    """Return the Pacemaker Editor database path if it exists, else empty string."""
    candidate = default_editor_db_path()
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
    candidate = default_editor_db_path()
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


def find_orphaned_device_files(music_root: str, db_locations) -> list[str]:
    """
    Audio files under music_root that no database location refers to, plus .part files
    left by an interrupted copy.

    Files are matched by name alone, ignoring upper/lower case. Every device file name is
    unique, and this keeps files that older-format locations (full paths, different drive
    letters, different case) still refer to. When unsure, a file is kept.
    """
    referenced = {re.split(r"[\\/]", loc or "")[-1].casefold() for loc in db_locations}
    orphans: list[str] = []
    for dirpath, _dirs, files in os.walk(music_root):
        for name in files:
            if name.endswith(".part") or (
                extension(name) in ALL_AUDIO_EXTENSIONS and name.casefold() not in referenced
            ):
                orphans.append(os.path.join(dirpath, name))
    return orphans


def is_on_device(path: str, device_db_path: str) -> bool:
    """True if `path` lives under the same device root as `device_db_path`."""
    root = os.path.normcase(os.path.abspath(device_root(device_db_path)))
    return os.path.normcase(os.path.abspath(path)).startswith(root)
