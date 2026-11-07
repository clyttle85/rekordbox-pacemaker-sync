"""
M3U8 playlist import, and the audio file checks and tag reading behind every file import
(File → Import from M3U8 and drag-and-drop). Uses Mutagen.
"""

from __future__ import annotations

import os
import re

import mutagen
from mutagen.aac import AAC
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Tags
from mutagen.wave import WAVE

from core.audio_formats import extension, is_supported, normalise_location
from core.rekordbox_reader import TrackInfo

# What each extension's contents should turn out to be, and the format label to store
_EXPECTED_TYPES = {
    ".mp3": (MP3, "MP3"),
    ".flac": (FLAC, "FLAC"),
    ".m4a": (MP4, "MP4"),
    ".mp4": (MP4, "MP4"),
    ".aac": (AAC, "AAC"),
    ".wav": (WAVE, "WAV"),
    ".aiff": (AIFF, "AIFF"),
}

# TrackInfo field -> tag name, per tagging system
_ID3_KEYS = {
    "title": "TIT2", "artist": "TPE1", "album": "TALB", "album_artist": "TPE2",
    "composer": "TCOM", "genre": "TCON", "label": "TPUB", "producer": "TPE3",
    "remixer": "TPE4", "key": "TKEY", "year": "TDRC", "bpm": "TBPM",
    "track": "TRCK", "disc": "TPOS",
}
_VORBIS_KEYS = {
    "title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist",
    "composer": "composer", "genre": "genre", "label": "label", "producer": "producer",
    "remixer": "remixer", "key": "initialkey", "year": "date", "bpm": "bpm",
    "track": "tracknumber", "disc": "discnumber", "comments": "comment", "rating": "rating",
    "track_total": "tracktotal", "disc_total": "disctotal",
}
_MP4_KEYS = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "album_artist": "aART",
    "composer": "\xa9wrt", "genre": "\xa9gen", "label": "----:com.apple.iTunes:LABEL",
    "producer": "----:com.apple.iTunes:PRODUCER", "remixer": "----:com.apple.iTunes:REMIXER",
    "key": "----:com.apple.iTunes:initialkey", "year": "\xa9day", "bpm": "tmpo",
    "comments": "\xa9cmt", "track": "trkn", "disc": "disk",
}


class AudioFileError(ValueError):
    """An audio file that would cause problems if imported. str() says why, for the user."""


def _read_playlist_text(file_path: str) -> str:
    """Playlists come in several encodings. cp1252 and latin-1 are older Windows exports."""
    with open(file_path, "rb") as f:
        raw = f.read()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def parse_m3u8(file_path: str) -> list[dict]:
    """
    Parse an M3U8 file into a list of {file_path, duration, title} dicts. Relative entries
    are resolved against the playlist's own folder.
    """
    base_dir = os.path.dirname(os.path.abspath(file_path))
    entries = []
    current = {}
    for line in _read_playlist_text(file_path).splitlines():
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
            path = line if os.path.isabs(line) else os.path.normpath(os.path.join(base_dir, line))
            current["file_path"] = normalise_location(path)
            entries.append(current)
            current = {}
    return entries


def _text(value) -> str:
    """A tag value as text. Tags can be lists, frames, numbers, tuples or bytes."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    if hasattr(value, "text"):   # ID3 frame
        return _text(value.text)
    return str(value).strip() if value is not None else ""


def _safe_int(value) -> int:
    """The number in a tag: 128, "128.5" (rounded) or the 3 of "3/12"."""
    try:
        return round(float(_text(value).split("/")[0]))
    except (ValueError, TypeError, OverflowError):
        return 0


def _pair(value, total=None) -> tuple[int, int]:
    """(number, total) from "3/12" text, or an MP4 (3, 12) tuple, or separate tags."""
    if isinstance(value, list) and value and isinstance(value[0], tuple):
        first = value[0]
        return (int(first[0]), int(first[1]) if len(first) > 1 else 0)
    text = _text(value)
    if "/" in text:
        return _safe_int(text), _safe_int(text.split("/", 1)[1])
    return _safe_int(text), _safe_int(total)


def _year(value) -> str:
    match = re.search(r"\d{4}", _text(value))
    return match.group(0) if match else ""


def _id3_comment(tags) -> str:
    """ID3 stores comments under keys such as COMM::eng, so they can't be looked up directly."""
    for frame in tags.getall("COMM"):
        text = _text(frame)
        if text:
            return text
    return ""


def _id3_rating(tags) -> int:
    """POPM ratings run 0-255 and are keyed by email. The app shows 0-5."""
    for frame in tags.getall("POPM"):
        raw = getattr(frame, "rating", 0) or 0
        if raw > 0:
            return max(1, round(raw / 51))
    return 0


def _vorbis_rating(value) -> int:
    """Vorbis ratings have no standard scale: taggers use 0-5 or 0-100."""
    raw = _safe_int(value)
    return raw if raw <= 5 else min(5, round(raw / 20))


def _tag_values(tags) -> dict:
    """The raw values of a file's tags, keyed by TrackInfo field."""
    if tags is None:
        return {}
    if hasattr(tags, "getall"):   # ID3: MP3, WAV and AIFF
        values = {field: tags.get(key) for field, key in _ID3_KEYS.items()}
        values["comments"] = _id3_comment(tags)
        values["rating"] = _id3_rating(tags)
        return values
    keys = _MP4_KEYS if isinstance(tags, MP4Tags) else _VORBIS_KEYS
    return {field: tags.get(key) for field, key in keys.items()}


def read_track_metadata(file_path: str) -> TrackInfo:
    """
    Read an audio file's details, or raise AudioFileError if it would cause problems:
    empty, damaged, without any audio, or not really the format its extension claims.
    """
    if not os.path.exists(file_path):
        raise AudioFileError("file not found")
    size = os.path.getsize(file_path)
    if size == 0:
        raise AudioFileError("the file is empty")
    try:
        audio = mutagen.File(file_path)
    except Exception as e:
        raise AudioFileError(f"the file is damaged or can't be read ({type(e).__name__})") from e
    if audio is None:
        raise AudioFileError("this isn't a recognised audio file")

    ext = extension(file_path)
    expected = _EXPECTED_TYPES.get(ext)
    if expected and not isinstance(audio, expected[0]):
        raise AudioFileError(f"the file is named {ext} but contains a different format")
    length = getattr(audio.info, "length", 0) or 0
    if length <= 0:
        raise AudioFileError("the file has no playable audio")

    values = _tag_values(audio.tags)
    track_number, number_of_tracks = _pair(values.get("track"), values.get("track_total"))
    disc_number, number_of_discs = _pair(values.get("disc"), values.get("disc_total"))
    rating = values.get("rating")
    return TrackInfo(
        location=normalise_location(file_path),
        title=_text(values.get("title")) or os.path.splitext(os.path.basename(file_path))[0],
        artist=_text(values.get("artist")),
        album=_text(values.get("album")),
        album_artist=_text(values.get("album_artist")),
        composer=_text(values.get("composer")),
        genre=_text(values.get("genre")),
        label=_text(values.get("label")),
        producer=_text(values.get("producer")),
        remixer=_text(values.get("remixer")),
        key=_text(values.get("key")),
        year=_year(values.get("year")),
        comments=_text(values.get("comments")),
        bpm=_safe_int(values.get("bpm")),
        rating=rating if isinstance(rating, int) else _vorbis_rating(rating),
        track_number=track_number,
        number_of_tracks=number_of_tracks,
        disc_number=disc_number,
        number_of_discs=number_of_discs,
        bit_rate=int(getattr(audio.info, "bitrate", 0) or 0) // 1000,
        sample_rate=int(getattr(audio.info, "sample_rate", 0) or 0),
        play_time_secs=int(length),
        file_size=size,
        format=expected[1] if expected else ext.upper().lstrip("."),
    )


def load_m3u8_tracks(m3u8_path: str) -> tuple[list[TrackInfo], list[str], list[str]]:
    """
    Parse an M3U8 and return (tracks, problems, unsupported).
    problems: "path: reason" for each file that would cause trouble
    unsupported: paths of files in a format the app doesn't import
    """
    tracks = []
    problems = []
    unsupported = []
    for entry in parse_m3u8(m3u8_path):
        path = entry["file_path"]
        if not is_supported(path):
            unsupported.append(path)
            continue
        try:
            tracks.append(read_track_metadata(path))
        except AudioFileError as e:
            problems.append(f"{path}: {e}")
    return tracks, problems, unsupported
