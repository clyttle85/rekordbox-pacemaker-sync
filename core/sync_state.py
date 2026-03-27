"""
Manages persistent sync state — which Rekordbox playlists have been synced
to the Pacemaker and what their corresponding case IDs are.

State file is stored as JSON alongside the Pacemaker music.db.
"""

from __future__ import annotations

import json
import os
from datetime import datetime


STATE_FILE_NAME = "rb_pacemaker_sync_state.json"


def _state_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(db_path), STATE_FILE_NAME)


def load(db_path: str) -> dict:
    """Load sync state for a given Pacemaker DB. Returns empty dict if none exists."""
    path = _state_path(db_path)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(db_path: str, state: dict) -> None:
    """Persist sync state alongside the Pacemaker DB."""
    path = _state_path(db_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def get_synced_playlist_ids(db_path: str) -> set:
    """Return the set of Rekordbox playlist IDs currently synced to this DB."""
    return set(load(db_path).keys())


def record_sync(db_path: str, rb_playlist_id: str, playlist_name: str,
                case_id: int, track_locations: list[str]) -> None:
    """Record or update a successful sync for a playlist."""
    state = load(db_path)
    state[rb_playlist_id] = {
        "pacemaker_case_id": case_id,
        "playlist_name": playlist_name,
        "last_synced": datetime.now().isoformat(),
        "track_locations": track_locations,
    }
    save(db_path, state)


def remove_sync(db_path: str, rb_playlist_id: str) -> None:
    """Remove a playlist from the sync state (called after deletion from Pacemaker)."""
    state = load(db_path)
    state.pop(rb_playlist_id, None)
    save(db_path, state)


def get_entry(db_path: str, rb_playlist_id: str) -> dict | None:
    """Return the sync state entry for a specific playlist, or None."""
    return load(db_path).get(rb_playlist_id)
