"""
Drag-and-drop file import — reads audio files/folders dropped from Windows
Explorer and extracts track metadata using Mutagen (via m3u8_reader's
file-level reader, which is not actually M3U8-specific).
"""

from __future__ import annotations

import os

from core.m3u8_reader import read_track_metadata
from core.rekordbox_reader import TrackInfo

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".mp4", ".wma", ".aac", ".ogg", ".wav", ".aif", ".aiff",
}


def expand_dropped_paths(paths: list[str]) -> list[str]:
    """
    Expand a list of dropped file/folder paths into a flat list of audio
    file paths. Folders are walked recursively; non-audio files are dropped.
    """
    files: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, names in os.walk(path):
                for name in names:
                    if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                        files.append(os.path.join(root, name))
        elif os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS:
            files.append(path)
    return files


def read_tracks_from_paths(paths: list[str]) -> tuple[list[TrackInfo], list[str]]:
    """
    Expand and read metadata for a list of dropped file/folder paths.
    Returns (tracks, errors) — errors is the list of files that failed to read.
    """
    files = expand_dropped_paths(paths)
    tracks: list[TrackInfo] = []
    errors: list[str] = []
    for fp in files:
        try:
            tracks.append(read_track_metadata(fp))
        except Exception:
            errors.append(fp)
    return tracks, errors
