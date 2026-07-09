"""
Reads playlists and tracks from the Rekordbox master.db via pyrekordbox.
Handles auto-discovery of the database path and provides a simple
tree structure for the UI to consume.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

_IMPORT_ERROR: str = ""
try:
    from pyrekordbox import Rekordbox6Database as MasterDatabase
    from pyrekordbox.db6.tables import DjmdPlaylist
    PYREKORDBOX_AVAILABLE = True
except Exception as _e:
    PYREKORDBOX_AVAILABLE = False
    _IMPORT_ERROR = str(_e)


# Attribute values from pyrekordbox PlaylistType enum
PLAYLIST_TYPE = 0
FOLDER_TYPE = 1
SMART_PLAYLIST_TYPE = 4

# Rekordbox special playlist IDs to skip
SPECIAL_IDS = {"100000", "200000"}


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
        if not PYREKORDBOX_AVAILABLE:
            raise RuntimeError(
                f"pyrekordbox could not be imported.\n\n"
                f"Error: {_IMPORT_ERROR}\n\n"
                f"Try: pip install pyrekordbox sqlcipher3-wheels"
            )
        self._db = MasterDatabase(path=db_path) if db_path else MasterDatabase()

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
            node.track_count = len(pl.Songs) if pl.Songs else 0

        return node

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
                    from pyrekordbox.db6.tables import DjmdContent
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
        # pyrekordbox 0.4.4 returns the object directly when a filter arg is given
        if hasattr(result, "one_or_none"):
            pl = result.one_or_none()
        else:
            pl = result
        if pl is None:
            return []

        # get_playlist_contents() loses track order — it uses ID.in_() with no ORDER BY.
        # pl.Songs is the DjmdSongPlaylist join table which has TrackNo for ordering.
        songs = sorted(pl.Songs or [], key=lambda s: s.TrackNo or 0)
        tracks = []
        for song in songs:
            content = song.Content
            if content is None:
                continue
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
                    bit_rate = int(content.BitRate) // 1000
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

            # Derive format from file extension
            ext = os.path.splitext(content.FolderPath)[1].upper().lstrip(".")
            fmt = ext if ext in ("MP3", "FLAC", "MP4", "M4A", "AAC", "WAV", "AIFF") else "MP3"

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
