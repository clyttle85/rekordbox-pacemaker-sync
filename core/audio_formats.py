"""Which audio files the app can import, shared by every import route and device tool."""

from __future__ import annotations

import os
import sys

# Change this set to allow or block a format everywhere.
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".wav", ".aiff"}

# Recognised as audio so the user can be told why they weren't imported. Older versions
# accepted these, so the device tools still have to handle any already on a Pacemaker.
UNSUPPORTED_EXTENSIONS = {".aif", ".ogg", ".oga", ".opus", ".wma"}

ALL_AUDIO_EXTENSIONS = SUPPORTED_EXTENSIONS | UNSUPPORTED_EXTENSIONS


def extension(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def is_supported(path: str) -> bool:
    return extension(path) in SUPPORTED_EXTENSIONS


def normalise_location(path: str) -> str:
    """
    One spelling per file, so the same file isn't imported as two tracks. Rekordbox
    stores Windows paths with forward slashes, so imported paths follow suit.
    """
    return path.replace("\\", "/") if sys.platform.startswith("win") else path
