"""
Backs up a Pacemaker's music.db to the computer before the app changes it.

Backups live on the computer, not the Pacemaker: they use none of the device's space, and
they survive the device being unplugged or failing, which is when they're needed. Only the
database is copied, not the audio, so each backup is small.
"""

from __future__ import annotations

import os
import sqlite3
import time

KEEP_BACKUPS = 10


def backup_device_db(device_db: str, backup_dir: str, keep: int = KEEP_BACKUPS) -> str:
    """Returns the backup's path. Raises OSError or sqlite3.Error if it can't be made."""
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(backup_dir, f"pacemaker_music_{stamp}.db")
    suffix = 2
    while os.path.exists(path):
        path = os.path.join(backup_dir, f"pacemaker_music_{stamp}_{suffix}.db")
        suffix += 1

    source = sqlite3.connect(device_db)
    try:
        target = sqlite3.connect(path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    backups = sorted(
        (os.path.join(backup_dir, name) for name in os.listdir(backup_dir)
         if name.startswith("pacemaker_music_") and name.endswith(".db")),
        key=os.path.getmtime,
    )
    for old in backups[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass
    return path
