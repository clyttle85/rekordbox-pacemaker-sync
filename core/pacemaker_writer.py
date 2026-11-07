"""
Writes playlists and tracks to the Tonium Pacemaker music.db (SQLite).

Handles:
- Creating cases (playlists)
- Inserting tracks (deduplication by location)
- Linking tracks to cases via casetracks
- Deleting cases and orphaned tracks (for sync removal)
- Diffing existing cases against new track lists (for sync updates)
"""

from __future__ import annotations

import secrets
import sqlite3
import os
import time
from typing import Callable, Optional

from core.rekordbox_reader import TrackInfo

CREATOR_ID = "Tonium;Editor;2.0.2.14170;1117277940118978560"
CASE_YEAR = 2024

# TrackInfo fields a re-import or sync brings up to date -> the columns holding them
_DETAIL_COLUMNS = {
    "title": ("title", "ind_title"),
    "artist": ("artist", "ind_artist"),
    "album": ("album", "ind_album"),
    "album_artist": ("album_artist",),
    "composer": ("composer",),
    "genre": ("genre", "ind_genre"),
    "label": ("label",),
    "producer": ("producer",),
    "remixer": ("remixer",),
    "key": ("key",),
    "year": ("year",),
    "comments": ("comments",),
    "bpm": ("bpm", "ind_bpm"),
    "rating": ("rating",),
    "track_number": ("track_number",),
    "number_of_tracks": ("number_of_tracks",),
    "disc_number": ("disc_number",),
    "number_of_discs": ("number_of_discs",),
    "bit_rate": ("bit_rate",),
    "sample_rate": ("sample_rate",),
    "play_time_secs": ("play_time_secs",),
    "file_size": ("file_size",),
    "format": ("format",),
}


def _same_value(old, new) -> bool:
    return old == new or str(old) == str(new)


def track_details_differ(new: TrackInfo, existing: TrackInfo) -> bool:
    """
    True if new has details that existing lacks or disagrees with. Blank values never
    count: not every source knows every detail, so they don't erase what's there.
    """
    return any(
        getattr(new, field) and not _same_value(getattr(existing, field), getattr(new, field))
        for field in _DETAIL_COLUMNS
    )


class PacemakerWriter:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()

    def library_id(self) -> int:
        """
        A random ID for this database, issued the first time it's asked for. It lives in
        the SQLite header's application_id, so it adds no tables to a database the Tonium
        Editor shares, and a recreated database gets a new one.
        """
        current = self._conn.execute("PRAGMA application_id").fetchone()[0]
        if current:
            return current
        new_id = secrets.randbits(31) | 1
        self._conn.execute(f"PRAGMA application_id = {new_id}")
        self._conn.commit()
        return new_id

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Case operations
    # ------------------------------------------------------------------

    def create_case(self, name: str) -> int:
        """Insert a new case and return its case_id."""
        cursor = self._conn.cursor()
        cursor.execute("""
            INSERT INTO cases (name, date_created, genre, year, creator_id, times_played, image_id)
            VALUES (?, strftime('%s', 'now'), 'Various', ?, ?, 0, 0)
        """, (name, CASE_YEAR, CREATOR_ID))
        self._conn.commit()
        return cursor.lastrowid

    def rename_case(self, case_id: int, new_name: str) -> None:
        """Rename an existing case."""
        cursor = self._conn.cursor()
        cursor.execute("UPDATE cases SET name = ? WHERE case_id = ?", (new_name, case_id))
        self._conn.commit()

    def case_name_exists(self, name: str, ignore_case_id: Optional[int] = None) -> bool:
        """True if another case already has this name, ignoring upper/lower case."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT case_id, name FROM cases")
        wanted = name.casefold()
        return any(
            (row["name"] or "").casefold() == wanted and row["case_id"] != ignore_case_id
            for row in cursor.fetchall()
        )

    def unique_case_name(self, name: str) -> str:
        """Return name, or name_2, name_3… if a case already has that name."""
        candidate = name
        suffix = 2
        while self.case_name_exists(candidate):
            candidate = f"{name}_{suffix}"
            suffix += 1
        return candidate

    def delete_case(self, case_id: int) -> None:
        """Delete a case and its casetracks entries. Does NOT delete tracks."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM casetracks WHERE case_id = ?", (case_id,))
        cursor.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        self._conn.commit()

    def get_case_track_locations(self, case_id: int) -> set[str]:
        """Return the set of file locations for all tracks in a case."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT t.location FROM tracks t
            JOIN casetracks ct ON ct.track_id = t.track_id
            WHERE ct.case_id = ?
        """, (case_id,))
        return {row["location"] for row in cursor.fetchall()}

    def get_ordered_case_track_locations(self, case_id: int) -> list[str]:
        """Return a case's track locations in running order, including any repeats."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT t.location FROM tracks t
            JOIN casetracks ct ON ct.track_id = t.track_id
            WHERE ct.case_id = ?
            ORDER BY ct.rowid
        """, (case_id,))
        return [row["location"] for row in cursor.fetchall()]

    def clear_case_tracks(self, case_id: int) -> None:
        """Remove all casetracks entries for a case (leaves tracks table intact)."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM casetracks WHERE case_id = ?", (case_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Track operations
    # ------------------------------------------------------------------

    def _refresh_track_details(self, row: sqlite3.Row, track: TrackInfo) -> None:
        """Update a track's details in place. Its ID is kept, so it stays in its cases."""
        updates: dict = {}
        for field, columns in _DETAIL_COLUMNS.items():
            value = getattr(track, field)
            if value and not _same_value(row[columns[0]], value):
                updates.update({column: value for column in columns})
        if updates:
            updates["date_modified"] = int(time.time())
            assignments = ", ".join(f"{column} = ?" for column in updates)
            self._conn.execute(
                f"UPDATE tracks SET {assignments} WHERE track_id = ?",
                (*updates.values(), row["track_id"]),
            )
            self._conn.commit()

    def insert_or_get_track(self, track: TrackInfo, date_source: Optional[str] = None) -> int:
        """
        Return the track_id for a track. Inserts it if it doesn't exist yet, and brings
        its details up to date if it does. Deduplication is by file location.

        date_source is the file to take its dates from, for when track.location isn't a
        path on this computer (device tracks hold firmware paths).
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM tracks WHERE location = ?", (track.location,))
        row = cursor.fetchone()
        if row:
            self._refresh_track_details(row, track)
            return row["track_id"]

        dated_file = date_source or track.location
        date_added = int(os.path.getmtime(dated_file)) if os.path.exists(dated_file) else 0

        values = (
            track.title, track.location, track.bit_rate, track.sample_rate,
            track.file_size, track.play_time_secs, track.format, track.artist,
            track.album_artist, track.composer, track.album, track.track_number,
            track.year, track.genre,
            0,                  # is_part_of_c
            date_added,         # date_added
            -1,                 # last_played
            0,                  # times_played
            -1,                 # cue_point
            0,                  # rc_mixes
            track.bpm, track.label,
            2,                  # track_flags
            None,               # global_id
            -1,                 # loop_in
            -1,                 # loop_out
            None,               # structured_ct
            track.title,        # ind_title
            track.artist,       # ind_artist
            track.album,        # ind_album
            track.genre,        # ind_genre
            track.bpm,          # ind_bpm
            None,               # discid
            track.producer, track.remixer, track.key,
            track.number_of_tracks, track.disc_number, track.number_of_discs,
            date_added,         # date_modified
            "2.0.2.14170",      # modified_by_ed
            "2.0.2.14170",      # analyzed_by_ed
            1,                  # analysis_ver
            track.rating, track.comments,
        )

        cursor.execute("""
            INSERT INTO tracks (
                title, location, bit_rate, sample_rate, file_size, play_time_secs,
                format, artist, album_artist, composer, album, track_number, year, genre,
                is_part_of_c, date_added, last_played, times_played, cue_point, rc_mixes,
                bpm, label, track_flags, global_id, loop_in, loop_out, structured_ct,
                ind_title, ind_artist, ind_album, ind_genre, ind_bpm, discid,
                producer, remixer, key, number_of_tracks, disc_number, number_of_discs,
                date_modified, modified_by_ed, analyzed_by_ed, analysis_ver, rating, comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
        """, values)
        self._conn.commit()
        return cursor.lastrowid

    def link_track_to_case(self, case_id: int, track_id: int) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            "INSERT INTO casetracks (case_id, track_id) VALUES (?, ?)",
            (case_id, track_id)
        )
        self._conn.commit()

    def delete_orphan_tracks(self, locations: list[str]) -> list[str]:
        """
        Delete tracks at the given locations only if they are not
        referenced by any other case.
        Returns the list of locations that were actually deleted.
        """
        deleted: list[str] = []
        cursor = self._conn.cursor()
        for loc in locations:
            cursor.execute("SELECT track_id FROM tracks WHERE location = ?", (loc,))
            row = cursor.fetchone()
            if not row:
                continue
            track_id = row["track_id"]
            cursor.execute(
                "SELECT COUNT(*) FROM casetracks WHERE track_id = ?", (track_id,)
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute("DELETE FROM tracks WHERE track_id = ?", (track_id,))
                deleted.append(loc)
        self._conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # High-level sync operations
    # ------------------------------------------------------------------

    def sync_playlist(
        self,
        case_id: int,
        tracks: list[TrackInfo],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> list[str]:
        """
        Replace the contents of an existing case with the given tracks.
        Returns the list of track locations that were synced.
        """
        old_locations = self.get_case_track_locations(case_id)
        self.clear_case_tracks(case_id)

        locations = []
        for i, track in enumerate(tracks):
            track_id = self.insert_or_get_track(track)
            self.link_track_to_case(case_id, track_id)
            locations.append(track.location)
            if progress_callback:
                progress_callback(i + 1, len(tracks))

        # Clean up tracks that are no longer in any case
        removed = old_locations - set(locations)
        if removed:
            self.delete_orphan_tracks(list(removed))

        return locations

    def add_playlist(
        self,
        name: str,
        tracks: list[TrackInfo],
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> tuple[int, list[str]]:
        """
        Create a new case and populate it with tracks.
        Returns (case_id, list of track locations).
        """
        case_id = self.create_case(name)
        locations = []
        for i, track in enumerate(tracks):
            track_id = self.insert_or_get_track(track)
            self.link_track_to_case(case_id, track_id)
            locations.append(track.location)
            if progress_callback:
                progress_callback(i + 1, len(tracks))
        return case_id, locations

    def remove_playlist(self, case_id: int, track_locations: list[str]) -> list[str]:
        """
        Delete a case and remove any tracks that are now orphaned.
        Returns the list of track locations that were actually deleted from the DB.
        """
        self.delete_case(case_id)
        return self.delete_orphan_tracks(track_locations)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_trackinfo(row: sqlite3.Row) -> TrackInfo:
        """Build a TrackInfo from a `tracks` table row (requires SELECT t.*)."""
        return TrackInfo(
            location=row["location"] or "",
            title=row["title"] or "",
            artist=row["artist"] or "",
            album=row["album"] or "",
            album_artist=row["album_artist"] or "",
            composer=row["composer"] or "",
            genre=row["genre"] or "",
            label=row["label"] or "",
            producer=row["producer"] or "",
            remixer=row["remixer"] or "",
            key=row["key"] or "",
            year=str(row["year"]) if row["year"] else "",
            comments=row["comments"] or "",
            bpm=row["bpm"] or 0,
            rating=row["rating"] or 0,
            track_number=row["track_number"] or 0,
            number_of_tracks=row["number_of_tracks"] or 0,
            disc_number=row["disc_number"] or 0,
            number_of_discs=row["number_of_discs"] or 0,
            bit_rate=row["bit_rate"] or 0,
            sample_rate=row["sample_rate"] or 0,
            play_time_secs=row["play_time_secs"] or 0,
            file_size=row["file_size"] or 0,
            format=row["format"] or "",
        )

    def get_case_tracks_as_trackinfo(self, case_id: int) -> list[TrackInfo]:
        """
        Read all tracks for a case from this DB and return them as TrackInfo objects.
        Preserves track order via casetracks insertion order.
        Used when pushing cases from Editor DB to Device DB.
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT t.* FROM tracks t
            JOIN casetracks ct ON ct.track_id = t.track_id
            WHERE ct.case_id = ?
            ORDER BY ct.rowid
        """, (case_id,))
        return [self._row_to_trackinfo(row) for row in cursor.fetchall()]

    def get_all_tracks_as_trackinfo(self) -> list[tuple[TrackInfo, int]]:
        """
        Read every track in the database as (TrackInfo, case_count) pairs.
        case_count == 0 means the track is orphaned (in no case).
        Used for the Editor Library's whole-library "all tracks" view.
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT t.*, COUNT(ct.case_id) AS case_count
            FROM tracks t
            LEFT JOIN casetracks ct ON ct.track_id = t.track_id
            GROUP BY t.track_id
            ORDER BY t.title COLLATE NOCASE
        """)
        return [
            (self._row_to_trackinfo(row), row["case_count"])
            for row in cursor.fetchall()
        ]

    def get_track_by_location(self, location: str) -> Optional[TrackInfo]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM tracks WHERE location = ?", (location,))
        row = cursor.fetchone()
        return self._row_to_trackinfo(row) if row else None

    def get_case_ids_for_location(self, location: str) -> set[int]:
        """Every case that holds the track at this location."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT ct.case_id FROM casetracks ct
            JOIN tracks t ON t.track_id = ct.track_id
            WHERE t.location = ?
        """, (location,))
        return {row["case_id"] for row in cursor.fetchall()}

    def get_case_track_sizes(self) -> dict[int, dict[str, int]]:
        """{case_id: {location: file_size}} for every case."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT ct.case_id, t.location, t.file_size FROM casetracks ct
            JOIN tracks t ON t.track_id = ct.track_id
        """)
        sizes: dict[int, dict[str, int]] = {}
        for row in cursor.fetchall():
            sizes.setdefault(row["case_id"], {})[row["location"]] = row["file_size"] or 0
        return sizes

    def delete_all_unreferenced_tracks(self) -> list[str]:
        """Delete every track in no case. Returns their locations."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT location FROM tracks
            WHERE track_id NOT IN (SELECT track_id FROM casetracks)
        """)
        return self.delete_orphan_tracks([row["location"] for row in cursor.fetchall()])

    def get_track_id_by_location(self, location: str) -> Optional[int]:
        """Return the track_id for a track at this file location, or None."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT track_id FROM tracks WHERE location = ?", (location,))
        row = cursor.fetchone()
        return row["track_id"] if row else None

    def is_track_in_case(self, case_id: int, track_id: int) -> bool:
        """Return True if this track is already linked to this case."""
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM casetracks WHERE case_id = ? AND track_id = ? LIMIT 1",
            (case_id, track_id)
        )
        return cursor.fetchone() is not None

    def unlink_track_from_case(self, case_id: int, track_id: int) -> None:
        """Remove a single track from a case without deleting the track itself."""
        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM casetracks WHERE case_id = ? AND track_id = ?",
            (case_id, track_id)
        )
        self._conn.commit()

    def reorder_case_tracks(self, case_id: int, track_ids_in_order: list[int]) -> None:
        """
        Rewrite a case's track order to match the given sequence of track_ids
        (order is implied by casetracks insertion order — see
        get_case_tracks_as_trackinfo's `ORDER BY ct.rowid`). Does not change
        which tracks are in the case, only their order. A track_id listed twice is
        in the case twice.
        """
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM casetracks WHERE case_id = ?", (case_id,))
        cursor.executemany(
            "INSERT INTO casetracks (case_id, track_id) VALUES (?, ?)",
            [(case_id, track_id) for track_id in track_ids_in_order]
        )
        self._conn.commit()

    def find_track_id(self, track: TrackInfo) -> Optional[int]:
        """
        Find a track already in this DB by title + artist + duration (±1 sec).
        Returns track_id if found, None otherwise.
        Used when pushing cases to the device without copying files.
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT track_id FROM tracks
            WHERE title = ?
              AND artist = ?
              AND ABS(play_time_secs - ?) <= 1
            LIMIT 1
        """, (track.title, track.artist, track.play_time_secs))
        row = cursor.fetchone()
        return row["track_id"] if row else None

    def get_all_cases(self) -> list[dict]:
        """
        Return all cases in the database with their track counts and total file size.
        Each entry: {"case_id": int, "name": str, "track_count": int, "file_size_bytes": int}
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT c.case_id, c.name,
                   COUNT(ct.track_id) AS track_count,
                   COALESCE(SUM(t.file_size), 0) AS file_size_bytes
            FROM cases c
            LEFT JOIN casetracks ct ON ct.case_id = c.case_id
            LEFT JOIN tracks t ON t.track_id = ct.track_id
            GROUP BY c.case_id, c.name
            ORDER BY c.name COLLATE NOCASE
        """)
        return [
            {
                "case_id": row["case_id"],
                "name": row["name"],
                "track_count": row["track_count"],
                "file_size_bytes": row["file_size_bytes"],
            }
            for row in cursor.fetchall()
        ]

    def get_cases_for_track(self, track_id: int) -> list[str]:
        """Return the names of all cases that contain this track."""
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT c.name FROM cases c
            JOIN casetracks ct ON ct.case_id = c.case_id
            WHERE ct.track_id = ?
            ORDER BY c.name COLLATE NOCASE
        """, (track_id,))
        return [row["name"] for row in cursor.fetchall()]

    def get_all_tracks_with_case_count(self) -> list[dict]:
        """
        Return every track in the DB with how many cases reference it.
        case_count == 0 means the track is orphaned (in tracks table but no case).
        """
        cursor = self._conn.cursor()
        cursor.execute("""
            SELECT t.track_id, t.title, t.artist, t.bpm, t.play_time_secs,
                   t.location, t.file_size,
                   COUNT(ct.case_id) AS case_count
            FROM tracks t
            LEFT JOIN casetracks ct ON ct.track_id = t.track_id
            GROUP BY t.track_id
            ORDER BY t.title COLLATE NOCASE
        """)
        return [dict(row) for row in cursor.fetchall()]

    def fix_bpm_values(self) -> int:
        """
        One-time migration: divides by 100 any bpm/ind_bpm stored as BPM×100
        (i.e. values > 1000, e.g. 14000 → 140).  Returns the number of rows fixed.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tracks WHERE bpm > 1000")
        count = cursor.fetchone()[0]
        if count:
            cursor.execute("""
                UPDATE tracks SET
                    bpm     = ROUND(CAST(bpm AS REAL) / 100),
                    ind_bpm = ROUND(CAST(ind_bpm AS REAL) / 100)
                WHERE bpm > 1000
            """)
            self._conn.commit()
        return count

    def get_total_track_bytes(self) -> int:
        """Return the sum of file_size for all unique tracks in the database."""
        cursor = self._conn.cursor()
        cursor.execute("SELECT COALESCE(SUM(file_size), 0) AS total FROM tracks")
        return cursor.fetchone()["total"]
