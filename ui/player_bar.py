"""
Persistent audio player bar shown at the top of the main window.

Contains:
  - Track info (title / artist)
  - Transport controls (prev / play / next)
  - WaveformWidget: draws PWAV preview waveform, doubles as scrub bar
  - Elapsed / total time
  - Volume slider
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QTimer, QThread, QObject
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSlider, QSizePolicy, QFrame,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

try:
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    _MULTIMEDIA_OK = True
except ImportError:
    _MULTIMEDIA_OK = False

from core.rekordbox_reader import TrackInfo

# Waveform color palette (PWAV color codes 0-7)
_WAVE_COLORS = [
    QColor("#888888"),  # 0 white/silent
    QColor("#ff88aa"),  # 1 pink
    QColor("#5599ff"),  # 2 blue (most common)
    QColor("#88ccff"),  # 3 bright blue
    QColor("#44ddcc"),  # 4 cyan
    QColor("#e8631a"),  # 5 orange/outro
    QColor("#ffdd44"),  # 6 yellow
    QColor("#44cc44"),  # 7 green
]
_WAVE_DIM = QColor("#2a2a2a")    # background
_POS_LINE  = QColor("#ffffff")   # playback position line


def _fmt(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 60}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Async waveform loader (runs in a thread, emits result)
# ---------------------------------------------------------------------------

class _WaveformLoader(QObject):
    done = pyqtSignal(object)  # list[tuple[int,int]] or None

    def __init__(self, rb_reader, location: str):
        super().__init__()
        self._reader = rb_reader
        self._location = location

    def run(self):
        data = None
        if self._reader:
            data = self._reader.get_waveform_data(self._location)
        self.done.emit(data)


# ---------------------------------------------------------------------------
# Waveform display widget
# ---------------------------------------------------------------------------

class WaveformWidget(QWidget):
    """Draws a 400-column PWAV preview waveform. Click or drag to seek."""

    seek_requested = pyqtSignal(float)   # 0.0 – 1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[tuple[int, int]] = []   # [(height, color), ...]
        self._position: float = 0.0              # 0.0 – 1.0
        self._dragging = False
        self.setMinimumHeight(36)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_waveform(self, data: list[tuple[int, int]] | None) -> None:
        self._data = data or []
        self.update()

    def set_position(self, fraction: float) -> None:
        self._position = max(0.0, min(1.0, fraction))
        self.update()

    def clear(self) -> None:
        self._data = []
        self._position = 0.0
        self.update()

    def paintEvent(self, _) -> None:
        w, h = self.width(), self.height()
        p = QPainter(self)
        p.fillRect(0, 0, w, h, _WAVE_DIM)

        if self._data:
            n = len(self._data)
            bar_w = max(w / n, 1.0)
            max_h = h * 0.9
            mid = h / 2
            p.setPen(Qt.PenStyle.NoPen)
            for i, (height, color_idx) in enumerate(self._data):
                bar_h = (height / 31.0) * max_h
                x = int(i * bar_w)
                bw = max(int(bar_w), 1)
                col = _WAVE_COLORS[color_idx % len(_WAVE_COLORS)]
                p.setBrush(QBrush(col))
                p.drawRect(x, int(mid - bar_h / 2), bw, max(int(bar_h), 1))

        # Playback position line
        px = int(self._position * w)
        p.setPen(QPen(_POS_LINE, 1))
        p.drawLine(px, 0, px, h)
        p.end()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._emit_seek(ev.position().x())

    def mouseMoveEvent(self, ev) -> None:
        if self._dragging:
            self._emit_seek(ev.position().x())

    def mouseReleaseEvent(self, ev) -> None:
        self._dragging = False

    def _emit_seek(self, x: float) -> None:
        frac = max(0.0, min(1.0, x / max(self.width(), 1)))
        self.seek_requested.emit(frac)


# ---------------------------------------------------------------------------
# Player bar
# ---------------------------------------------------------------------------

class PlayerBar(QWidget):
    """Top-of-window audio player bar with waveform display."""

    track_changed = pyqtSignal(int)   # index into current playlist

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks: list[TrackInfo] = []
        self._current: int = -1
        self._seeking: bool = False
        self._rb_reader = None         # set by MainWindow after load
        self._waveform_thread = None
        self._waveform_worker = None

        if _MULTIMEDIA_OK:
            self._player = QMediaPlayer()
            self._audio_out = QAudioOutput()
            self._player.setAudioOutput(self._audio_out)
            self._audio_out.setVolume(0.7)
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.playbackStateChanged.connect(self._on_state)
            self._player.mediaStatusChanged.connect(self._on_media_status)
        else:
            self._player = None
            self._audio_out = None

        self._build_ui()
        self.setFixedHeight(70)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_rekordbox_reader(self, reader) -> None:
        """Provide the RekordboxReader so waveforms can be loaded."""
        self._rb_reader = reader

    def load_and_play(self, tracks: list[TrackInfo], start_index: int = 0) -> None:
        self._tracks = tracks
        self._play_index(start_index)

    def stop(self) -> None:
        """Stop playback without clearing the loaded track."""
        if self._player:
            self._player.stop()

    def highlight_index(self, index: int) -> None:
        self._current = index
        self._update_info()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        top = QFrame(self)
        top.setFrameShape(QFrame.Shape.HLine)
        top.setFrameShadow(QFrame.Shadow.Sunken)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(top)

        inner = QHBoxLayout()
        inner.setContentsMargins(8, 4, 8, 4)
        inner.setSpacing(8)

        # --- Track info ---
        info_widget = QWidget()
        info_widget.setFixedWidth(260)
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(1)
        self._title_lbl = QLabel("—")
        self._title_lbl.setStyleSheet("font-weight: bold; font-size: 11px;")
        self._title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._artist_lbl = QLabel("")
        self._artist_lbl.setStyleSheet("color: #888; font-size: 10px;")
        self._artist_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        info_layout.addWidget(self._title_lbl)
        info_layout.addWidget(self._artist_lbl)
        inner.addWidget(info_widget)

        # --- Transport ---
        self._prev_btn = QPushButton("⏮")
        self._prev_btn.setFixedSize(32, 32)
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._play_prev)
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedSize(40, 40)
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_play)
        self._next_btn = QPushButton("⏭")
        self._next_btn.setFixedSize(32, 32)
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._play_next)
        for btn in (self._prev_btn, self._play_btn, self._next_btn):
            inner.addWidget(btn)

        # --- Waveform (replaces scrub slider) ---
        wave_col = QVBoxLayout()
        wave_col.setSpacing(2)
        wave_col.setContentsMargins(0, 0, 0, 0)

        self._waveform = WaveformWidget()
        self._waveform.seek_requested.connect(self._on_waveform_seek)
        wave_col.addWidget(self._waveform)

        self._time_lbl = QLabel("0:00 / 0:00")
        self._time_lbl.setStyleSheet("font-size: 10px; color: #888;")
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wave_col.addWidget(self._time_lbl)

        inner.addLayout(wave_col, stretch=1)

        # --- Volume ---
        vol_lbl = QLabel("🔊")
        vol_lbl.setStyleSheet("font-size: 12px;")
        inner.addWidget(vol_lbl)
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(70)
        self._vol_slider.setFixedWidth(80)
        self._vol_slider.valueChanged.connect(self._on_volume_changed)
        inner.addWidget(self._vol_slider)

        if not _MULTIMEDIA_OK:
            inner.addWidget(QLabel("⚠ QtMultimedia not available"))

        root.addLayout(inner)

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _play_index(self, index: int) -> None:
        if not self._tracks or not (0 <= index < len(self._tracks)):
            return
        self._current = index
        track = self._tracks[index]
        self._update_info()
        self._waveform.clear()
        if self._player:
            self._player.setSource(QUrl.fromLocalFile(track.location))
            self._player.play()
        for btn in (self._play_btn, self._prev_btn, self._next_btn):
            btn.setEnabled(True)
        self.track_changed.emit(index)
        self._load_waveform(track.location)

    def _load_waveform(self, location: str) -> None:
        if not self._rb_reader:
            return
        # Run in a thread so DB access doesn't block the UI
        self._waveform_thread = QThread()
        self._waveform_worker = _WaveformLoader(self._rb_reader, location)
        self._waveform_worker.moveToThread(self._waveform_thread)
        self._waveform_thread.started.connect(self._waveform_worker.run)
        self._waveform_worker.done.connect(self._on_waveform_loaded)
        self._waveform_worker.done.connect(self._waveform_thread.quit)
        self._waveform_thread.start()

    def _on_waveform_loaded(self, data) -> None:
        self._waveform.set_waveform(data)

    def _play_next(self) -> None:
        if self._tracks and self._current < len(self._tracks) - 1:
            self._play_index(self._current + 1)

    def _play_prev(self) -> None:
        if self._player and self._player.position() > 3000:
            self._player.setPosition(0)
        elif self._current > 0:
            self._play_index(self._current - 1)
        elif self._player:
            self._player.setPosition(0)

    def _toggle_play(self) -> None:
        if not self._player:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    # ------------------------------------------------------------------
    # UI updates
    # ------------------------------------------------------------------

    def _update_info(self) -> None:
        if 0 <= self._current < len(self._tracks):
            t = self._tracks[self._current]
            self._title_lbl.setText(t.title or "Unknown")
            self._artist_lbl.setText(t.artist or "")
        else:
            self._title_lbl.setText("—")
            self._artist_lbl.setText("")

    # ------------------------------------------------------------------
    # Player signal handlers
    # ------------------------------------------------------------------

    def _on_position(self, pos: int) -> None:
        dur = self._player.duration() if self._player else 0
        self._time_lbl.setText(f"{_fmt(pos)} / {_fmt(dur)}")
        if dur > 0:
            self._waveform.set_position(pos / dur)

    def _on_duration(self, dur: int) -> None:
        pass  # waveform uses fraction, no range to set

    def _on_state(self, state) -> None:
        self._play_btn.setText(
            "⏸" if state == QMediaPlayer.PlaybackState.PlayingState else "▶"
        )

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._play_next()

    def _on_waveform_seek(self, fraction: float) -> None:
        if self._player:
            dur = self._player.duration()
            if dur > 0:
                self._player.setPosition(int(fraction * dur))

    def _on_volume_changed(self, value: int) -> None:
        if self._audio_out:
            self._audio_out.setVolume(value / 100.0)
