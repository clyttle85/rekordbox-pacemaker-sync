"""
Creates a fresh, empty Pacemaker Editor music.db from scratch.

On Windows this is only needed if someone never installed the real Tonium
Editor software (which is what originally created the file this app has
always just detected and added to). On macOS there is no equivalent legacy
Editor install at all, so this is required there.

The schema below was captured verbatim from a real Editor music.db's
`sqlite_master` table. This app only reads/writes `cases`, `casetracks` and
`tracks`, but the rest (`mixes`, `miximages`, `mixtracks`, `trackmap`) are
included too since the Pacemaker device firmware may expect them to exist.
"""

from __future__ import annotations

import sqlite3

_SCHEMA_STATEMENTS = [
    """CREATE TABLE cases (
        case_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(255),
        date_created NUMBER(20),
        times_played NUMBER(10),
        structured_ct BLOB,
        image_id INTEGER,
        genre VARCHAR(255),
        year NUMBER(4),
        rating NUMBER(1),
        producer VARCHAR(255),
        remixer VARCHAR(255),
        play_time_secs NUMBER(10),
        last_played NUMBER(20),
        date_modified NUMBER(20),
        comments VARCHAR(255),
        creator_id VARCHAR(255)
    )""",
    """CREATE TABLE casetracks (
        case_id INTEGER,
        track_id INTEGER
    )""",
    """CREATE TABLE mixes (
        mix_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR(255),
        date_created INTEGER,
        times_played NUMBER(10),
        structured_ct BLOB,
        mix_image INTEGER,
        genre VARCHAR(255),
        year NUMBER(4),
        rating NUMBER(1),
        producer VARCHAR(255),
        remixer VARCHAR(255),
        play_time_secs NUMBER(10),
        last_played NUMBER(20),
        date_modified NUMBER(20),
        comments VARCHAR(255),
        creator_id VARCHAR(255),
        preview_markers VARCHAR(255)
    )""",
    """CREATE TABLE miximages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        data BLOB
    )""",
    """CREATE TABLE mixtracks (
        mix_id INTEGER,
        track_id INTEGER,
        dup_title VARCHAR(255),
        dup_artist VARCHAR(255),
        dup_album VARCHAR(255),
        dup_play_time_secs NUMBER(10),
        track_index INTEGER,
        cue_point NUMBER(20)
    )""",
    """CREATE TABLE trackmap (
        ext_db VARCHAR(255),
        ext_id INTEGER PRIMARY KEY,
        int_id INTEGER,
        tag_hash INTEGER,
        location_hash INTEGER
    )""",
    """CREATE TABLE tracks (
        track_id INTEGER PRIMARY KEY AUTOINCREMENT,
        artist VARCHAR(255),
        album_artist VARCHAR(255),
        title VARCHAR(255),
        composer VARCHAR(255),
        album VARCHAR(255),
        track_number NUMBER(4),
        year NUMBER(4),
        genre VARCHAR(255),
        is_part_of_c NUMBER(1),
        location VARCHAR(255),
        format VARCHAR(32),
        bit_rate NUMBER(10),
        sample_rate NUMBER(10),
        file_size NUMBER(10),
        play_time_secs NUMBER(10),
        rating NUMBER(1),
        comments VARCHAR(255),
        date_added NUMBER(20),
        last_played NUMBER(20),
        times_played NUMBER(10),
        cue_point NUMBER(20),
        rc_mixes NUMBER(10),
        bpm REAL,
        label VARCHAR(255),
        track_flags NUMBER(10) DEFAULT 0,
        global_id VARCHAR(255),
        loop_in NUMBER(20),
        loop_out NUMBER(20),
        structured_ct VARCHAR(511),
        ind_title VARCHAR(32),
        ind_artist VARCHAR(32),
        ind_album VARCHAR(32),
        ind_genre VARCHAR(32),
        ind_bpm INTEGER,
        discid VARCHAR(255),
        producer VARCHAR(255),
        remixer VARCHAR(255),
        key VARCHAR(4),
        number_of_tracks NUMBER(4),
        disc_number NUMBER(4),
        number_of_discs NUMBER(4),
        date_modified NUMBER(20),
        modified_by_ed VARCHAR(32),
        analyzed_by_ed VARCHAR(32),
        analysis_ver INTEGER
    )""",
    "CREATE INDEX casetracks_idx ON casetracks (track_id)",
    "CREATE INDEX mixtracks_idx ON mixtracks (track_id)",
    "CREATE INDEX tracks_location_idx ON tracks (location)",
]


def create_empty_editor_db(path: str) -> None:
    """Create a fresh, empty Editor music.db with the full Pacemaker schema."""
    conn = sqlite3.connect(path)
    try:
        cursor = conn.cursor()
        for statement in _SCHEMA_STATEMENTS:
            cursor.execute(statement)
        conn.commit()
    finally:
        conn.close()
