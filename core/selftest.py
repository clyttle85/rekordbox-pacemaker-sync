"""
Checks run by `main.py --selftest`, mainly by CI against the built app bundle.

The aim is to catch what only breaks once packaged: modules PyInstaller missed
and native libraries that didn't get bundled. So these actually use SQLCipher,
pyrekordbox and QtMultimedia rather than only importing them.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile


def check_rekordbox_stack() -> None:
    """Create an encrypted master.db with pyrekordbox's real key and read it back."""
    from sqlalchemy.orm import Session
    from core import rekordbox_reader as reader

    if not reader.PYREKORDBOX_AVAILABLE:
        raise RuntimeError(f"pyrekordbox failed to import: {reader._IMPORT_ERROR}")

    # Whichever module the supported pyrekordbox version keeps its tables in
    tables = sys.modules[reader.DjmdContent.__module__]
    folder = tempfile.mkdtemp()
    try:
        path = os.path.join(folder, "master.db")
        open(path, "wb").close()

        db = reader.MasterDatabase(path=path)
        tables.Base.metadata.create_all(db.engine)
        # Nearly every column is NOT NULL despite the models' default=None
        blanks = {
            c.key: c.type.python_type()
            for c in tables.DjmdPlaylist.__table__.columns
            if not c.nullable and c.default is None and c.type.python_type in (int, str, float)
        }
        blanks.update(
            ID="1", Seq=1, Name="Selftest", Attribute=reader.PLAYLIST_TYPE, ParentID="root",
        )
        with Session(db.engine) as session:
            session.add(tables.DjmdPlaylist(**blanks))
            session.commit()
        db.close()

        with open(path, "rb") as f:
            if f.read(15) == b"SQLite format 3":
                raise RuntimeError("master.db was not encrypted: SQLCipher is not in use")

        with reader.RekordboxReader(path) as rb:
            names = [node.name for node in rb.get_playlist_tree()]
        if names != ["Selftest"]:
            raise RuntimeError(f"Unexpected playlists read back: {names}")
    finally:
        # Best effort: Windows can't delete a file a connection pool still holds open
        shutil.rmtree(folder, ignore_errors=True)


def check_multimedia() -> None:
    """Fail if the Qt Multimedia backend used for track previews wasn't bundled."""
    from PyQt6.QtMultimedia import QMediaPlayer

    if not QMediaPlayer().isAvailable():
        raise RuntimeError("No Qt Multimedia backend is available")


def run_app_checks() -> None:
    """Run every check. Needs a QApplication; any failure raises."""
    check_rekordbox_stack()
    check_multimedia()
