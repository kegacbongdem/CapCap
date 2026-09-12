#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui/utils/preview_audio.py

Real-time PCM preview audio engine using PySide6 QAudioSink, windowed AudioReader,
and in-memory block mixing. Eliminates intermediate WAV files and FFmpeg CLI calls
during timeline preview, volume adjustments, and seeking.
"""

from __future__ import annotations

from collections import OrderedDict
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal

from PySide6.QtCore import (
    QCoreApplication,
    QMetaObject,
    QObject,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtMultimedia import (
    QAudioFormat,
    QAudioSink,
    QMediaDevices,
)

from app.audio_mixer import mix_pcm_block
from app.media_decode import AudioReader


class _LRUPcmCache:
    """Memory-bounded LRU cache for 16kHz mono float32 PCM blocks."""

    def __init__(self, max_bytes: int = 128 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._cache: OrderedDict[Tuple[str, int, int, int], np.ndarray] = OrderedDict()
        self._current_bytes: int = 0

    def get(self, key: Tuple[str, int, int, int]) -> Optional[np.ndarray]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: Tuple[str, int, int, int], block: np.ndarray) -> None:
        block_bytes = block.nbytes
        if block_bytes > self.max_bytes:
            return  # single block exceeds cache limit

        if key in self._cache:
            self._current_bytes -= self._cache[key].nbytes
            del self._cache[key]

        while self._current_bytes + block_bytes > self.max_bytes and self._cache:
            _, old_block = self._cache.popitem(last=False)
            self._current_bytes -= old_block.nbytes

        self._cache[key] = block
        self._current_bytes += block_bytes

    def clear(self) -> None:
        self._cache.clear()
        self._current_bytes = 0


class _PreviewAudioWorker(QObject):
    """Internal audio engine worker running on a dedicated QThread."""

    positionChanged = Signal(int)
    stateChanged = Signal(int)  # 0: Stopped, 1: Playing, 2: Paused
    errorOccurred = Signal(str)

    def __init__(self, sample_rate: int = 16000, block_size: int = 160):
        super().__init__()
        self.internal_sr = sample_rate
        self.block_size = block_size  # 160 samples = 10ms at 16kHz
        self.block_duration_ms = int(round(block_size * 1000.0 / sample_rate))

        self._sink: Optional[QAudioSink] = None
        self._io_device = None
        self._output_format: Optional[QAudioFormat] = None
        self._timer: Optional[QTimer] = None

        self._tracks: List[Dict[str, Any]] = []
        self._readers: Dict[str, AudioReader] = {}
        self._warps: List[Dict[str, Any]] = []

        self._pcm_cache = _LRUPcmCache(max_bytes=128 * 1024 * 1024)

        self._timeline_pos_ms: int = 0
        self._is_playing: bool = False
        self._playback_rate: float = 1.0
        self._generation_id: int = 0

        # Resampling state for output sink
        self._sink_sr: int = 48000
        self._sink_channels: int = 2
        self._sink_is_float: bool = True

    def init_sink(self) -> None:
        """Initialize QAudioSink with default audio output device format."""
        try:
            device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                self.errorOccurred.emit("No default audio output device available")
                return

            pref = device.preferredFormat()
            fmt = QAudioFormat()
            self._sink_sr = pref.sampleRate() if pref.sampleRate() > 0 else 48000
            fmt.setSampleRate(self._sink_sr)

            self._sink_channels = pref.channelCount() if pref.channelCount() > 0 else 2
            fmt.setChannelCount(self._sink_channels)

            if pref.sampleFormat() == QAudioFormat.SampleFormat.Float:
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
                self._sink_is_float = True
            else:
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
                self._sink_is_float = False

            self._output_format = fmt
            self._sink = QAudioSink(device, fmt, self)
            # Request small ~40ms output buffer for low latency
            bytes_per_sample = 4 if self._sink_is_float else 2
            buffer_target = int(self._sink_sr * self._sink_channels * bytes_per_sample * 0.04)
            self._sink.setBufferSize(max(buffer_target, 2048))

            self._io_device = self._sink.start()

            self._timer = QTimer(self)
            self._timer.setInterval(self.block_duration_ms)
            self._timer.timeout.connect(self._on_timer_tick)
        except Exception as exc:
            self.errorOccurred.emit(f"Failed to initialize audio sink: {exc}")

    @Slot(list, list)
    def set_tracks(self, tracks: List[Dict[str, Any]], warps: Optional[List[Dict[str, Any]]] = None) -> None:
        """Update active audio tracks and timeline warp markers."""
        self._warps = list(warps or [])
        new_tracks = []
        needed_paths = set()

        for raw in tracks:
            path = str(raw.get("path", "")).strip()
            if not path or not os.path.exists(path):
                continue

            track_id = str(raw.get("id") or path)
            needed_paths.add(path)

            vol = float(raw.get("volume", 100.0))
            muted = bool(raw.get("muted", False))
            target_gain = 0.0 if muted else max(0.0, min(2.0, vol / 100.0))

            # Maintain existing smooth gain if track was already present
            existing = next((t for t in self._tracks if t["id"] == track_id), None)
            cur_gain = existing["current_gain"] if existing else target_gain

            new_tracks.append({
                "id": track_id,
                "path": path,
                "start_ms": max(0, int(float(raw.get("start", 0.0)) * 1000.0)),
                "end_ms": max(0, int(float(raw.get("end", 0.0)) * 1000.0)),
                "source_start_ms": max(0, int(float(raw.get("source_start", 0.0)) * 1000.0)),
                "volume": vol,
                "muted": muted,
                "target_gain": target_gain,
                "current_gain": cur_gain,
                "loop": bool(raw.get("loop", False)),
                "is_original_video": bool(raw.get("is_original_video", False)),
            })

        # Close readers no longer needed
        stale_paths = set(self._readers.keys()) - needed_paths
        for p in stale_paths:
            try:
                self._readers[p].close()
            except Exception:
                pass
            del self._readers[p]

        # Ensure all active tracks have open readers
        for p in needed_paths:
            if p not in self._readers:
                try:
                    self._readers[p] = AudioReader(p, sample_rate=self.internal_sr)
                except Exception as exc:
                    self.errorOccurred.emit(f"Failed to open AudioReader for {p}: {exc}")

        self._tracks = new_tracks
        self._generation_id += 1

    @Slot(str, float, bool)
    def set_track_gain(self, track_id: str, gain: float, muted: bool) -> None:
        """Update gain and mute state for a track with smooth 5ms ramping."""
        for t in self._tracks:
            if t["id"] == track_id or t["path"] == track_id:
                t["muted"] = muted
                t["volume"] = gain * 100.0
                t["target_gain"] = 0.0 if muted else max(0.0, min(2.0, gain))
                break

    @Slot(int)
    def seek(self, timeline_ms: int) -> None:
        """Seek playback to timeline millisecond."""
        self._timeline_pos_ms = max(0, int(timeline_ms))
        self._generation_id += 1
        if self._sink and self._is_playing:
            self._sink.reset()
            self._io_device = self._sink.start()
        self.positionChanged.emit(self._timeline_pos_ms)

    @Slot()
    def play(self) -> None:
        """Start audio playback."""
        if not self._is_playing:
            self._is_playing = True
            if self._sink and self._sink.bytesFree() <= 0:
                self._io_device = self._sink.start()
            if self._timer and not self._timer.isActive():
                self._timer.start()
            self.stateChanged.emit(1)

    @Slot()
    def pause(self) -> None:
        """Pause audio playback."""
        if self._is_playing:
            self._is_playing = False
            if self._timer and self._timer.isActive():
                self._timer.stop()
            self.stateChanged.emit(2)

    @Slot()
    def stop(self) -> None:
        """Stop audio playback and return to start."""
        self._is_playing = False
        if self._timer and self._timer.isActive():
            self._timer.stop()
        self._timeline_pos_ms = 0
        if self._sink:
            self._sink.reset()
        self.positionChanged.emit(0)
        self.stateChanged.emit(0)

    @Slot(float)
    def set_rate(self, rate: float) -> None:
        """Set playback rate (e.g. 1.0, 1.25)."""
        self._playback_rate = max(0.25, min(4.0, float(rate)))

    def _is_time_frozen(self, t_sec: float) -> bool:
        """Return True if timestamp t_sec falls within a freeze frame warp."""
        for w in self._warps:
            wt = float(w.get("time", 0.0))
            wd = float(w.get("duration", 0.0))
            if wt <= t_sec < (wt + wd):
                return True
        return False

    def _on_timer_tick(self) -> None:
        """Mix and push PCM audio blocks to the sink."""
        if not self._is_playing or self._io_device is None or self._sink is None:
            return

        free_bytes = self._sink.bytesFree()
        # Compute output block byte size
        bytes_per_sample = 4 if self._sink_is_float else 2
        out_samples_per_block = int(round(self.block_size * (self._sink_sr / self.internal_sr)))
        out_bytes_per_block = out_samples_per_block * self._sink_channels * bytes_per_sample

        # If sink buffer has insufficient room, wait for next tick
        if free_bytes < out_bytes_per_block:
            return

        cur_t_sec = self._timeline_pos_ms / 1000.0
        cur_sample = int(self._timeline_pos_ms * self.internal_sr / 1000.0)

        blocks: List[np.ndarray] = []
        gains: List[float] = []

        is_frozen = self._is_time_frozen(cur_t_sec)

        for track in self._tracks:
            reader = self._readers.get(track["path"])
            if reader is None:
                continue

            # Original video audio is silenced during freeze frames
            if is_frozen and track["is_original_video"]:
                continue

            start_ms = track["start_ms"]
            end_ms = track["end_ms"]

            if self._timeline_pos_ms < start_ms:
                continue
            if end_ms > start_ms and self._timeline_pos_ms >= end_ms:
                continue

            # Compute track-relative sample offset
            offset_ms = self._timeline_pos_ms - start_ms + track["source_start_ms"]
            track_sample = int(offset_ms * self.internal_sr / 1000.0)

            # Check LRU cache
            cache_key = (track["path"], track_sample, self.block_size, self._generation_id)
            block = self._pcm_cache.get(cache_key)
            if block is None:
                block = reader.read(track_sample, self.block_size)
                self._pcm_cache.put(cache_key, block)

            # Apply smooth 5ms ramp toward target gain
            # 5ms ramp at 16kHz = 80 samples
            ramp_len = min(80, self.block_size)
            cur_g = track["current_gain"]
            target_g = track["target_gain"]

            if abs(cur_g - target_g) > 1e-4:
                # Interpolate
                gain_envelope = np.linspace(cur_g, target_g, ramp_len, dtype=np.float32)
                if ramp_len < self.block_size:
                    gain_envelope = np.concatenate([gain_envelope, np.full(self.block_size - ramp_len, target_g, dtype=np.float32)])
                track["current_gain"] = target_g
                block = block * gain_envelope
                gains.append(1.0)
            else:
                track["current_gain"] = target_g
                gains.append(target_g)

            blocks.append(block)

        # Mix down to mono 16kHz
        if blocks:
            mixed_16k = mix_pcm_block(blocks, gains)
        else:
            mixed_16k = np.zeros(self.block_size, dtype=np.float32)

        # Resample to output sink sample rate (e.g. 16k -> 48k)
        if self._sink_sr == self.internal_sr:
            out_mono = mixed_16k
        else:
            out_mono = scipy.signal.resample_poly(mixed_16k, self._sink_sr // 1000, self.internal_sr // 1000).astype(np.float32)

        # Expand channels (mono -> stereo if needed)
        if self._sink_channels == 2:
            out_audio = np.column_stack([out_mono, out_mono])
        else:
            out_audio = out_mono

        # Convert to target format bytes
        if self._sink_is_float:
            data_bytes = out_audio.astype(np.float32).tobytes()
        else:
            data_bytes = (out_audio * 32767.0).clip(-32768.0, 32767.0).astype(np.int16).tobytes()

        # Push to sink
        self._io_device.write(data_bytes)

        # Advance timeline position
        self._timeline_pos_ms += int(round(self.block_duration_ms * self._playback_rate))
        self.positionChanged.emit(self._timeline_pos_ms)

    @Slot()
    def close(self) -> None:
        """Release audio sink, timer, and readers."""
        self._is_playing = False
        if self._timer:
            self._timer.stop()
            self._timer = None

        if self._sink:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink = None
            self._io_device = None

        for reader in self._readers.values():
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
        self._pcm_cache.clear()


class PreviewAudioEngine(QObject):
    """Thread-safe public facade for real-time preview audio mixing and playback."""

    timelinePositionChanged = Signal(int)
    stateChanged = Signal(int)
    error = Signal(str)

    # Internal signals for safe cross-thread queued dispatching to worker
    _sig_set_tracks = Signal(list, list)
    _sig_set_track_gain = Signal(str, float, bool)
    _sig_seek = Signal(int)
    _sig_play = Signal()
    _sig_pause = Signal()
    _sig_stop = Signal()
    _sig_set_rate = Signal(float)
    _sig_close = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._worker_thread = QThread()
        self._worker = _PreviewAudioWorker()
        self._worker.moveToThread(self._worker_thread)

        self._worker.positionChanged.connect(self.timelinePositionChanged)
        self._worker.stateChanged.connect(self.stateChanged)
        self._worker.errorOccurred.connect(self.error)

        # Connect internal control signals
        self._sig_set_tracks.connect(self._worker.set_tracks)
        self._sig_set_track_gain.connect(self._worker.set_track_gain)
        self._sig_seek.connect(self._worker.seek)
        self._sig_play.connect(self._worker.play)
        self._sig_pause.connect(self._worker.pause)
        self._sig_stop.connect(self._worker.stop)
        self._sig_set_rate.connect(self._worker.set_rate)
        self._sig_close.connect(self._worker.close)

        self._worker_thread.started.connect(self._worker.init_sink)
        self._worker_thread.start()

        self._cached_position_ms: int = 0
        self.timelinePositionChanged.connect(self._update_cached_pos)

    def _update_cached_pos(self, pos_ms: int) -> None:
        self._cached_position_ms = pos_ms

    def set_tracks(self, tracks: List[Dict[str, Any]], warps: Optional[List[Dict[str, Any]]] = None) -> None:
        """Update active audio tracks snapshot."""
        self._sig_set_tracks.emit(tracks, warps or [])

    def set_track_gain(self, track_id: str, gain: float, muted: bool = False) -> None:
        """Update track volume without regenerating media files."""
        self._sig_set_track_gain.emit(str(track_id), float(gain), bool(muted))

    def seek(self, timeline_ms: int) -> None:
        """Seek audio playback to timeline millisecond."""
        self._cached_position_ms = int(timeline_ms)
        self._sig_seek.emit(int(timeline_ms))

    def play(self) -> None:
        """Start audio playback."""
        self._sig_play.emit()

    def pause(self) -> None:
        """Pause audio playback."""
        self._sig_pause.emit()

    def stop(self) -> None:
        """Stop audio playback."""
        self._cached_position_ms = 0
        self._sig_stop.emit()

    def set_rate(self, rate: float) -> None:
        """Set playback rate."""
        self._sig_set_rate.emit(float(rate))

    def timeline_position_ms(self) -> int:
        """Return current timeline position in milliseconds."""
        return self._cached_position_ms

    def close(self) -> None:
        """Clean up thread, worker, and audio sinks."""
        self._sig_close.emit()
        if self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(2000)

