"""
Auto-detection of Pacemaker Editor and Device database paths.

Editor DB: %APPDATA%\Tonium\Pacemaker\music.db
Device DB:  <drive>\.Pacemaker\music.db   (hidden folder on the device)
"""

from __future__ import annotations

import os
import string


def find_editor_db() -> str:
    """Return the Pacemaker Editor database path if it exists, else empty string."""
    appdata = os.environ.get("APPDATA", "")
    candidate = os.path.join(appdata, "Tonium", "Pacemaker", "music.db")
    return candidate if os.path.isfile(candidate) else ""


def find_device_db() -> str:
    """
    Scan all drive letters for a connected Pacemaker device.
    The device stores its database at <drive>\\.Pacemaker\\music.db (hidden folder).
    Returns the path if found, else empty string.
    """
    for letter in string.ascii_uppercase:
        root = letter + ":" + os.sep
        if not os.path.exists(root):
            continue
        candidate = os.path.join(root, ".Pacemaker", "music.db")
        if os.path.isfile(candidate):
            return candidate
    return ""
