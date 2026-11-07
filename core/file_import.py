"""
Drag-and-drop file import: expands audio files and folders dropped from the file manager
and reads their details with Mutagen (via m3u8_reader's file-level reader).
"""

from __future__ import annotations

import os

from core.audio_formats import ALL_AUDIO_EXTENSIONS, extension, is_supported, normalise_location
from core.m3u8_reader import AudioFileError, read_track_metadata
from core.rekordbox_reader import TrackInfo


def expand_dropped_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    """
    Expand dropped file/folder paths into (supported, unsupported) audio file paths.
    Folders are walked recursively. Files that aren't audio are ignored.
    """
    found: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for root, _dirs, names in os.walk(path):
                found.extend(os.path.join(root, name) for name in names)
        else:
            found.append(path)
    audio = [normalise_location(p) for p in found if extension(p) in ALL_AUDIO_EXTENSIONS]
    return [p for p in audio if is_supported(p)], [p for p in audio if not is_supported(p)]


def read_tracks_from_files(files: list[str]) -> tuple[list[TrackInfo], list[str]]:
    """Read each file's details. Returns (tracks, problems), each problem being "path: reason"."""
    tracks: list[TrackInfo] = []
    problems: list[str] = []
    for path in files:
        try:
            tracks.append(read_track_metadata(path))
        except AudioFileError as e:
            problems.append(f"{path}: {e}")
    return tracks, problems
