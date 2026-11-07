"""
Reads playlists and tracks from the Rekordbox master.db via pyrekordbox.
Handles auto-discovery of the database path and provides a simple
tree structure for the UI to consume.
"""

from __future__ import annotations
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from core.logger import log as _log

_IMPORT_ERROR: str = ""
try:
    # pyrekordbox 0.5 renamed pyrekordbox.db6 to pyrekordbox.masterdb and requires
    # Python 3.11+, so which branch runs depends on the interpreter: Windows builds
    # on Python 3.9 stay on 0.4.x. See requirements.txt for the supported range.
    try:
        from pyrekordbox.masterdb import MasterDatabase
        from pyrekordbox.masterdb.models import DjmdContent
    except ModuleNotFoundError as _e:
        if _e.name != "pyrekordbox.masterdb":
            raise
        from pyrekordbox import Rekordbox6Database as MasterDatabase
        from pyrekordbox.db6.tables import DjmdContent
    PYREKORDBOX_AVAILABLE = True
except Exception as _e:
    PYREKORDBOX_AVAILABLE = False
    _IMPORT_ERROR = f"{type(_e).__name__}: {_e}"


# Attribute values from pyrekordbox PlaylistType enum
PLAYLIST_TYPE = 0
FOLDER_TYPE = 1
SMART_PLAYLIST_TYPE = 4

# Rekordbox special playlist IDs to skip
SPECIAL_IDS = {"100000", "200000"}


class RekordboxError(Exception):
    """A problem opening the Rekordbox library, with a message written for end users."""
    title = "Couldn't Read Rekordbox Library"


class RekordboxUnavailableError(RekordboxError):
    title = "Rekordbox Support Failed to Load"


class RekordboxNotFoundError(RekordboxError):
    title = "Rekordbox Library Not Found"


class RekordboxUnlockError(RekordboxError):
    title = "Couldn't Unlock Rekordbox Library"


def _default_master_db() -> str:
    """Rekordbox's standard master.db location, wherever the app itself is installed."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library")
    else:
        base = os.environ.get("APPDATA", "")
    return os.path.join(base, "Pioneer", "rekordbox", "master.db")


def _unavailable_error(detail: object) -> RekordboxUnavailableError:
    hint = "" if getattr(sys, "frozen", False) else (
        "\n\nTry: pip install -r requirements.txt"
    )
    return RekordboxUnavailableError(
        f"The Rekordbox reader (pyrekordbox) could not be loaded.\n\n"
        f"Error: {detail}{hint}"
    )


def _open_master_db(db_path: Optional[str]):
    """
    Open master.db, or raise a RekordboxError explaining why it can't be.

    pyrekordbox decrypts the database with a key built into the package, so there
    is no per-machine setup step: this works first time on any Rekordbox 6/7.
    """
    if not PYREKORDBOX_AVAILABLE:
        raise _unavailable_error(_IMPORT_ERROR)

    # With no explicit path, let pyrekordbox find the library from the Rekordbox
    # install. If that fails, still try the default location before giving up.
    candidates = [db_path] if db_path else [None, _default_master_db()]
    not_found: Exception | None = None
    for candidate in candidates:
        try:
            return MasterDatabase(path=candidate) if candidate else MasterDatabase()
        except (FileNotFoundError, KeyError) as e:
            not_found = e
        except ImportError as e:   # sqlcipher3 missing or its library failed to load
            raise _unavailable_error(f"{type(e).__name__}: {e}") from e
        except Exception as e:
            raise RekordboxError(
                f"Your Rekordbox library was found, but could not be opened.\n\nDetails: {e}"
            ) from e
    raise RekordboxNotFoundError(
        "No Rekordbox library was found on this computer.\n\n"
        "Install Rekordbox 6 or 7 and open it once so it creates your library, "
        f"then reopen this app.\n\nDetails: {not_found}"
    ) from not_found


@dataclass
class PlaylistNode:
    """Represents a folder or playlist in the Rekordbox hierarchy."""
    id: str
    name: str
    is_folder: bool
    children: list["PlaylistNode"] = field(default_factory=list)
    track_count: int = 0


@dataclass
class TrackInfo:
    """All metadata needed to insert a track into the Pacemaker DB."""
    location: str
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    composer: str = ""
    genre: str = ""
    label: str = ""
    producer: str = ""
    remixer: str = ""
    key: str = ""
    year: str = ""
    comments: str = ""
    bpm: int = 0
    rating: int = 0
    track_number: int = 0
    number_of_tracks: int = 0
    disc_number: int = 0
    number_of_discs: int = 0
    bit_rate: int = 0
    sample_rate: int = 0
    play_time_secs: int = 0
    file_size: int = 0
    format: str = ""


class RekordboxReader:
    def __init__(self, db_path: Optional[str] = None):
        self._db = _open_master_db(db_path)
        self._check_readable()

    def _check_readable(self) -> None:
        """Connections are lazy, so a wrong key would otherwise only fail on first use."""
        from sqlalchemy.exc import DatabaseError
        try:
            self._db.get_playlist().first()
        except DatabaseError as e:
            self._db.close()
            if "not a database" in str(e):
                raise RekordboxUnlockError(
                    "Your Rekordbox library was found, but could not be unlocked. A newer "
                    "version of Rekordbox may have changed how it protects the library: "
                    "check for an update to this app.\n\n"
                    f"Details: {e.orig}"
                ) from e
            raise RekordboxError(
                "Your Rekordbox library was found, but could not be read. If Rekordbox "
                "is open, close it and reopen this app.\n\n"
                f"Details: {e.orig}"
            ) from e

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def get_playlist_tree(self) -> list[PlaylistNode]:
        """
        Returns the top-level playlist/folder nodes.
        Each folder node has its children populated recursively.
        Uses the Children relationship on DjmdPlaylist for correct nesting.
        """
        all_playlists = self._db.get_playlist().all()

        # Find roots: playlists whose ParentID is None or points to a special/missing ID
        all_ids = {str(pl.ID) for pl in all_playlists}
        roots = [
            pl for pl in all_playlists
            if (pl.ParentID is None or str(pl.ParentID) not in all_ids)
            and str(pl.ID) not in SPECIAL_IDS
        ]

        return [self._build_node(pl) for pl in sorted(roots, key=lambda p: p.Seq or 0)]

    def _build_node(self, pl) -> PlaylistNode:
        is_folder = pl.Attribute == FOLDER_TYPE
        node = PlaylistNode(
            id=str(pl.ID),
            name=pl.Name or "(unnamed)",
            is_folder=is_folder,
        )

        if is_folder:
            # Use the pre-built Children relationship from SQLAlchemy
            children = [
                c for c in (pl.Children or [])
                if str(c.ID) not in SPECIAL_IDS
            ]
            node.children = [
                self._build_node(c)
                for c in sorted(children, key=lambda p: p.Seq or 0)
            ]
        else:
            node.track_count = len(self._playlist_contents(pl))

        return node

    def _playlist_contents(self, pl) -> list:
        """A playlist's DjmdContent rows, in playlist order."""
        if pl.is_smart_playlist:
            # Intelligent playlists have no stored songs: Rekordbox evaluates their
            # rules on demand. They have no fixed order either, so sort by title.
            try:
                contents = self._db.get_playlist_contents(pl).all()
            except Exception as e:
                _log.error("Could not evaluate intelligent playlist %r: %s", pl.Name, e, exc_info=True)
                return []
            return sorted(contents, key=lambda c: (c.Title or "").casefold())

        # get_playlist_contents() loses track order for normal playlists: it uses
        # ID.in_() with no ORDER BY. pl.Songs is the join table with TrackNo.
        songs = sorted(pl.Songs or [], key=lambda s: s.TrackNo or 0)
        return [song.Content for song in songs if song.Content is not None]

    def get_waveform_data(self, location: str) -> list[tuple[int, int]] | None:
        """
        Return 400 (height, color) tuples from the PWAV preview waveform for a track,
        identified by its PC file path (FolderPath in Rekordbox).
        height: 0-31, color: 0-7 (0=white/silent, 2=blue, 5=red, etc.)
        Returns None if the track or ANLZ file cannot be found.
        """
        try:
            from pyrekordbox import AnlzFile
            import os as _os

            # Exact match first
            result = self._db.get_content(FolderPath=location)
            content = result.one_or_none() if hasattr(result, "one_or_none") else result

            # Case-insensitive fallback (Windows paths are case-insensitive;
            # SQLite filter_by does an exact match so "C:\Users\Opera\..." won't
            # match "C:\Users\opera\..." stored in the DB).
            if content is None:
                try:
                    import sqlalchemy
                    content = self._db.query(DjmdContent).filter(
                        sqlalchemy.func.lower(DjmdContent.FolderPath)
                        == location.lower()
                    ).first()
                except Exception:
                    pass

            if content is None:
                return None

            dat_path = self._db.get_anlz_path(content, "DAT")
            if not dat_path:
                return None
            if not _os.path.exists(str(dat_path)):
                return None
            anlz = AnlzFile.parse_file(str(dat_path))
            pwav = anlz.get_tag("PWAV")
            if pwav is None:
                return None
            return [(v & 0x1F, (v >> 5) & 0x7) for v in pwav.content.entries]
        except Exception:
            return None

    def get_playlist_tracks(self, playlist_id: str) -> list[TrackInfo]:
        """Return ordered list of TrackInfo for all tracks in a playlist."""
        import os
        result = self._db.get_playlist(ID=playlist_id)
        # pyrekordbox 0.4.4 and 0.5.0 both return the object directly when ID= is given
        if hasattr(result, "one_or_none"):
            pl = result.one_or_none()
        else:
            pl = result
        if pl is None:
            return []

        tracks = []
        for content in self._playlist_contents(pl):
            if not content.FolderPath:
                continue

            bpm = 0
            if content.BPM is not None:
                try:
                    # Rekordbox stores BPM as integer × 100 (e.g. 13601 = 136.01 BPM)
                    bpm = round(int(content.BPM) / 100)
                except (ValueError, TypeError):
                    bpm = 0

            rating = 0
            if content.Rating is not None:
                try:
                    # Rekordbox stores rating as 0/51/102/153/204/255 → map to 0-5
                    raw = int(content.Rating)
                    rating = round(raw / 51) if raw > 0 else 0
                except (ValueError, TypeError):
                    rating = 0

            play_time = 0
            if content.Length is not None:
                try:
                    play_time = int(float(str(content.Length)))
                except (ValueError, TypeError):
                    play_time = 0

            bit_rate = 0
            if content.BitRate is not None:
                try:
                    # Rekordbox stores kbps. Anything this large must be bits per second.
                    raw_rate = int(content.BitRate)
                    bit_rate = raw_rate // 1000 if raw_rate >= 10000 else raw_rate
                except (ValueError, TypeError):
                    bit_rate = 0

            sample_rate = 0
            if content.SampleRate is not None:
                try:
                    sample_rate = int(content.SampleRate)
                except (ValueError, TypeError):
                    sample_rate = 0

            file_size = 0
            if content.FileSize is not None:
                try:
                    file_size = int(content.FileSize)
                except (ValueError, TypeError):
                    file_size = 0

            fmt = os.path.splitext(content.FolderPath)[1].upper().lstrip(".")

            tracks.append(TrackInfo(
                location=content.FolderPath,
                title=content.Title or "",
                artist=content.ArtistName or "",
                album=content.AlbumName or "",
                album_artist=content.AlbumArtistName or "",
                composer=content.ComposerName or "",
                genre=content.GenreName or "",
                label=content.LabelName or "",
                producer="",
                remixer="",
                key=content.KeyName or "",
                year=str(content.ReleaseYear) if content.ReleaseYear else "",
                comments=content.Commnt or "",
                bpm=bpm,
                rating=rating,
                track_number=0,
                number_of_tracks=0,
                disc_number=0,
                number_of_discs=0,
                bit_rate=bit_rate,
                sample_rate=sample_rate,
                play_time_secs=play_time,
                file_size=file_size,
                format=fmt,
            ))

        return tracks
