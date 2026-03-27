"""
M3U8 playlist import — reads an M3U8 file and extracts track metadata
using Mutagen. Used by the File → Import from M3U8 feature.
"""

from __future__ import annotations

import os
import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.mp4 import MP4
from mutagen.asf import ASF

from core.rekordbox_reader import TrackInfo


def parse_m3u8(file_path: str) -> list[dict]:
    """Parse an M3U8 file into a list of {file_path, duration, title} dicts."""
    entries = []
    current = {}
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#EXTINF:"):
                info = line[len("#EXTINF:"):]
                if "," in info:
                    duration_str, title = info.split(",", 1)
                    try:
                        current["duration"] = float(duration_str.strip())
                    except ValueError:
                        current["duration"] = 0.0
                    current["title"] = title.strip()
            elif line and not line.startswith("#"):
                current["file_path"] = line
                entries.append(current)
                current = {}
    return entries


def _get_tag(tags, key: str, default: str = "") -> str:
    if tags is None:
        return default
    val = tags.get(key)
    if val is None:
        return default
    if isinstance(val, list):
        return str(val[0]) if val else default
    return str(val)


def _safe_int(value: str) -> int:
    try:
        return int(str(value).split("/")[0])
    except (ValueError, TypeError):
        return 0


def read_track_metadata(file_path: str) -> TrackInfo:
    """Read ID3/metadata tags from an audio file using Mutagen."""
    audio = mutagen.File(file_path)
    if audio is None:
        raise ValueError(f"Could not read audio file: {file_path}")

    if isinstance(audio, MP3):
        bit_rate = audio.info.bitrate // 1000
        sample_rate = audio.info.sample_rate
        play_time = int(audio.info.length)
        fmt = "MP3"
    elif isinstance(audio, FLAC):
        bit_rate = 0
        sample_rate = audio.info.sample_rate
        play_time = int(audio.info.length)
        fmt = "FLAC"
    elif isinstance(audio, MP4):
        bit_rate = 0
        sample_rate = audio.info.sample_rate
        play_time = int(audio.info.length)
        fmt = "MP4"
    elif isinstance(audio, ASF):
        bit_rate = 0
        sample_rate = audio.info.sample_rate
        play_time = int(audio.info.length)
        fmt = "ASF"
    else:
        bit_rate = 0
        sample_rate = 0
        play_time = 0
        fmt = os.path.splitext(file_path)[1].upper().lstrip(".")

    tags = audio.tags
    return TrackInfo(
        location=file_path,
        title=_get_tag(tags, "TIT2"),
        artist=_get_tag(tags, "TPE1"),
        album=_get_tag(tags, "TALB"),
        album_artist=_get_tag(tags, "TPE2"),
        composer=_get_tag(tags, "TCOM"),
        genre=_get_tag(tags, "TCON"),
        label=_get_tag(tags, "TPUB"),
        producer=_get_tag(tags, "TPE3"),
        remixer=_get_tag(tags, "TPE4"),
        key=_get_tag(tags, "TKEY"),
        year=_get_tag(tags, "TDRC"),
        comments=_get_tag(tags, "COMM"),
        bpm=_safe_int(_get_tag(tags, "TBPM")),
        rating=_safe_int(_get_tag(tags, "POPM")),
        track_number=_safe_int(_get_tag(tags, "TRCK")),
        number_of_tracks=_safe_int(_get_tag(tags, "TRCK")),
        disc_number=_safe_int(_get_tag(tags, "TPOS")),
        number_of_discs=_safe_int(_get_tag(tags, "TPOS")),
        bit_rate=bit_rate,
        sample_rate=sample_rate,
        play_time_secs=play_time,
        file_size=os.path.getsize(file_path) if os.path.exists(file_path) else 0,
        format=fmt,
    )


def load_m3u8_tracks(m3u8_path: str) -> tuple[list[TrackInfo], list[str]]:
    """
    Parse an M3U8 and return (tracks, errors).
    tracks: list of TrackInfo with metadata read from file
    errors: list of file paths that failed to load
    """
    entries = parse_m3u8(m3u8_path)
    tracks = []
    errors = []
    for entry in entries:
        fp = entry.get("file_path", "")
        if not fp or not os.path.exists(fp):
            errors.append(fp)
            continue
        try:
            track = read_track_metadata(fp)
            tracks.append(track)
        except Exception:
            errors.append(fp)
    return tracks, errors
