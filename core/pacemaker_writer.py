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

import sqlite3
import os
from typing import Callable, Optional

from core.rekordbox_reader import TrackInfo

CREATOR_ID = "Tonium;Editor;2.0.2.14170;1117277940118978560"
CASE_YEAR = 2024


class PacemakerWriter:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()

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

    def clear_case_tracks(self, case_id: int) -> None:
        """Remove all casetracks entries for a case (leaves tracks table intact)."""
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM casetracks WHERE case_id = ?", (case_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Track operations
    # ------------------------------------------------------------------

    def insert_or_get_track(self, track: TrackInfo) -> int:
        """
        Return the track_id for a track. Inserts it if it doesn't exist yet.
        Deduplication is by file location.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT track_id FROM tracks WHERE location = ?", (track.location,))
        row = cursor.fetchone()
        if row:
            return row["track_id"]

        date_added = int(os.path.getmtime(track.location)) if os.path.exists(track.location) else 0

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
        tracks = []
        for row in cursor.fetchall():
            tracks.append(TrackInfo(
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
            ))
        return tracks

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
