#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app/media_decode.py

Native media decoding and windowed audio reading without subprocess CLI calls.
Supports SoundFile for audio files (WAV, FLAC, OGG, MP3) and PyAV for video
containers (MP4, MKV, WebM) or fallback audio streams.
"""

from __future__ import annotations

import io
import math
import os
from typing import Optional, Tuple, Union

import numpy as np
import scipy.signal
import soundfile as sf

try:
    import av
    import av.error
except ImportError:
    av = None


def _downmix_to_mono(pcm: np.ndarray) -> np.ndarray:
    """Ensure PCM array is 1D mono float32 normalized to [-1.0, 1.0]."""
    if pcm is None or len(pcm) == 0:
        return np.empty(0, dtype=np.float32)

    # Normalize integer types if any are received (e.g. int16, int32)
    if np.issubdtype(pcm.dtype, np.integer):
        max_val = float(np.iinfo(pcm.dtype).max)
        pcm = pcm.astype(np.float32) / max_val
    else:
        pcm = pcm.astype(np.float32, copy=False)

    if pcm.ndim == 1:
        return pcm
    if pcm.ndim == 2:
        # If shape is (samples, channels) e.g. SoundFile
        if pcm.shape[1] <= 8 and pcm.shape[0] >= pcm.shape[1]:
            return np.mean(pcm, axis=1, dtype=np.float32)
        # If shape is (channels, samples) e.g. PyAV planar
        if pcm.shape[0] <= 8:
            return np.mean(pcm, axis=0, dtype=np.float32)
        # Fallback average along smallest axis
        axis = 0 if pcm.shape[0] < pcm.shape[1] else 1
        return np.mean(pcm, axis=axis, dtype=np.float32)
    return pcm.flatten().astype(np.float32)


def _resample_audio(pcm: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample 1D float32 audio using polyphase filtering."""
    if orig_sr <= 0 or target_sr <= 0:
        raise ValueError(f"Invalid sample rates: orig_sr={orig_sr}, target_sr={target_sr}")
    if orig_sr == target_sr:
        return pcm.astype(np.float32, copy=False)
    if len(pcm) == 0:
        return np.empty(0, dtype=np.float32)

    gcd = math.gcd(orig_sr, target_sr)
    up = target_sr // gcd
    down = orig_sr // gcd
    resampled = scipy.signal.resample_poly(pcm, up, down).astype(np.float32)
    return resampled


def decode_audio(
    source: Union[str, bytes, os.PathLike],
    *,
    sample_rate: int = 16000,
) -> Tuple[np.ndarray, int]:
    """Decode audio from bytes or file path into a 1D mono float32 array.

    Only for individual sentences or short clips. For long tracks, use
    AudioReader to avoid loading entire files into RAM.
    """
    if source is None:
        raise ValueError("Source cannot be None")

    if isinstance(source, bytes):
        if len(source) == 0:
            raise ValueError("Empty audio source bytes")
        bio = io.BytesIO(source)
        try:
            data, sr = sf.read(bio, dtype="float32")
            mono = _downmix_to_mono(data)
            return _resample_audio(mono, sr, sample_rate), sample_rate
        except Exception as sf_err:
            bio.seek(0)
            if av is None:
                raise ValueError(f"Failed to decode audio bytes with SoundFile: {sf_err}") from sf_err
            try:
                container = av.open(bio)
                if not container.streams.audio:
                    raise ValueError("No audio streams found in container")
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="fltp", rate=sample_rate)
                frames = []
                for frame in container.decode(stream):
                    for rf in resampler.resample(frame):
                        arr = rf.to_ndarray()
                        mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                        frames.append(mono)
                for rf in resampler.resample(None):
                    arr = rf.to_ndarray()
                    mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                    frames.append(mono)
                container.close()
                if not frames:
                    return np.empty(0, dtype=np.float32), sample_rate
                return np.concatenate(frames), sample_rate
            except Exception as av_err:
                raise ValueError(f"Failed to decode audio bytes: {sf_err}; {av_err}") from av_err
    else:
        path_str = os.fspath(source)
        if not os.path.exists(path_str):
            raise FileNotFoundError(f"Audio file not found: {path_str}")
        if os.path.getsize(path_str) == 0:
            raise ValueError(f"Audio file is empty: {path_str}")

        try:
            data, sr = sf.read(path_str, dtype="float32")
            mono = _downmix_to_mono(data)
            return _resample_audio(mono, sr, sample_rate), sample_rate
        except Exception as sf_err:
            if av is None:
                raise ValueError(f"Failed to open audio file {path_str} with SoundFile: {sf_err}") from sf_err
            try:
                container = av.open(path_str)
                if not container.streams.audio:
                    raise ValueError(f"No audio streams found in {path_str}")
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="fltp", rate=sample_rate)
                frames = []
                for frame in container.decode(stream):
                    for rf in resampler.resample(frame):
                        arr = rf.to_ndarray()
                        mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                        frames.append(mono)
                for rf in resampler.resample(None):
                    arr = rf.to_ndarray()
                    mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                    frames.append(mono)
                container.close()
                if not frames:
                    return np.empty(0, dtype=np.float32), sample_rate
                return np.concatenate(frames), sample_rate
            except Exception as av_err:
                raise ValueError(f"Failed to decode audio file {path_str}: {sf_err}; {av_err}") from av_err


class AudioReader:
    """Windowed audio reader for timeline playback and mixing without loading entire files into RAM.

    Reads windows [start_sample : start_sample + sample_count] at the target sample_rate.
    Automatically pads zeros for out-of-bounds requests (negative offsets or past EOF).
    """

    def __init__(self, path: Union[str, os.PathLike], *, sample_rate: int = 16000):
        self.path = os.fspath(path)
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Audio file not found: {self.path}")

        self.sample_rate = sample_rate
        self._is_closed = False
        self._backend: str = "soundfile"
        self._sf_file: Optional[sf.SoundFile] = None
        self._av_container = None
        self._av_stream = None

        # Attempt opening with SoundFile first
        try:
            self._sf_file = sf.SoundFile(self.path)
            self._orig_sr = self._sf_file.samplerate
            self._channels = self._sf_file.channels
            self._total_orig_frames = len(self._sf_file)
            self.total_samples = int(round(self._total_orig_frames * (self.sample_rate / self._orig_sr)))
            self._backend = "soundfile"
        except Exception:
            if av is None:
                raise ValueError(f"Cannot open {self.path} with SoundFile and PyAV is unavailable")
            # Fallback to PyAV
            self._av_container = av.open(self.path)
            if not self._av_container.streams.audio:
                self._av_container.close()
                raise ValueError(f"No audio stream found in {self.path}")
            self._av_stream = self._av_container.streams.audio[0]
            self._orig_sr = self._av_stream.codec_context.sample_rate or self.sample_rate
            self._channels = self._av_stream.codec_context.channels or 1

            if self._av_container.duration:
                dur_sec = float(self._av_container.duration / 1_000_000.0)
            elif self._av_stream.duration and self._av_stream.time_base:
                dur_sec = float(self._av_stream.duration * self._av_stream.time_base)
            else:
                dur_sec = 0.0
            self.total_samples = int(round(dur_sec * self.sample_rate))
            self._backend = "pyav"
            self._current_sample = 0
            self._av_buffer = np.empty(0, dtype=np.float32)
            self._av_buffer_start = 0
            self._av_resampler = None

    @property
    def duration_seconds(self) -> float:
        return self.total_samples / float(self.sample_rate)

    def read(self, start_sample: int, sample_count: int) -> np.ndarray:
        """Read a window of `sample_count` samples beginning at `start_sample`.

        Returns a 1D float32 numpy array of length `sample_count`.
        Pads with zeros for ranges before 0 or after EOF.
        """
        if self._is_closed:
            raise RuntimeError("AudioReader is closed")
        if sample_count <= 0:
            return np.empty(0, dtype=np.float32)

        output = np.zeros(sample_count, dtype=np.float32)

        # Out-of-bounds checks
        if start_sample >= self.total_samples or start_sample + sample_count <= 0:
            return output

        # Calculate valid in-file range
        valid_start = max(0, start_sample)
        valid_end = min(self.total_samples, start_sample + sample_count)
        valid_count = valid_end - valid_start

        if valid_count <= 0:
            return output

        out_offset = valid_start - start_sample

        if self._backend == "soundfile":
            samples = self._read_soundfile(valid_start, valid_count)
        else:
            samples = self._read_pyav(valid_start, valid_count)

        copied_count = min(len(samples), valid_count)
        output[out_offset : out_offset + copied_count] = samples[:copied_count]
        return output

    def _read_soundfile(self, start_sample: int, sample_count: int) -> np.ndarray:
        assert self._sf_file is not None

        if self._orig_sr == self.sample_rate:
            self._sf_file.seek(start_sample)
            data = self._sf_file.read(sample_count, dtype="float32")
            return _downmix_to_mono(data)

        # Scale sample positions to original sample rate
        gcd = math.gcd(self.sample_rate, self._orig_sr)
        up = self.sample_rate // gcd
        down = self._orig_sr // gcd

        orig_start = int(math.floor(start_sample * (self._orig_sr / self.sample_rate)))
        orig_end = int(math.ceil((start_sample + sample_count) * (self._orig_sr / self.sample_rate)))

        # Add filter margin to avoid FIR edge transients (Issue 5: continuous resampling)
        margin = int(math.ceil(10 * max(up, down)))
        m_start = max(0, orig_start - margin)
        m_end = min(self._total_orig_frames, orig_end + margin)
        m_count = max(0, m_end - m_start)

        if m_count <= 0:
            return np.zeros(sample_count, dtype=np.float32)

        self._sf_file.seek(m_start)
        data = self._sf_file.read(m_count, dtype="float32")
        mono = _downmix_to_mono(data)
        resampled = scipy.signal.resample_poly(mono, up, down).astype(np.float32)

        target_offset = int(round(start_sample - (m_start * self.sample_rate / self._orig_sr)))
        target_offset = max(0, target_offset)

        if target_offset < len(resampled):
            slice_data = resampled[target_offset : target_offset + sample_count]
            if len(slice_data) < sample_count:
                pad = np.zeros(sample_count - len(slice_data), dtype=np.float32)
                return np.concatenate([slice_data, pad])
            return slice_data
        return np.zeros(sample_count, dtype=np.float32)

    def _read_pyav(self, start_sample: int, sample_count: int) -> np.ndarray:
        assert self._av_container is not None and self._av_stream is not None

        buf_end = self._av_buffer_start + len(self._av_buffer)
        if self._av_buffer_start <= start_sample and start_sample + sample_count <= buf_end:
            offset = start_sample - self._av_buffer_start
            if offset > 32000:
                trim = offset - 16000
                self._av_buffer = self._av_buffer[trim:]
                self._av_buffer_start += trim
                offset -= trim
            return self._av_buffer[offset : offset + sample_count]

        # Determine target seek timestamp
        target_time_sec = max(0.0, start_sample / float(self.sample_rate))
        stream_tb = self._av_stream.time_base or (1 / self._orig_sr)

        needs_seek = (
            self._av_resampler is None
            or start_sample < self._av_buffer_start
            or start_sample > buf_end + 16000
        )
        if needs_seek:
            target_pts = int(target_time_sec / stream_tb)
            self._av_container.seek(target_pts, stream=self._av_stream, backward=True)
            self._av_resampler = av.AudioResampler(format="fltp", rate=self.sample_rate)
            self._av_buffer = np.empty(0, dtype=np.float32)
            self._av_buffer_start = None

        needed_end = start_sample + sample_count
        for frame in self._av_container.decode(self._av_stream):
            pts_s = float(frame.pts * stream_tb) if frame.pts is not None else target_time_sec
            dur_s = float(frame.samples) / float(frame.sample_rate)
            if self._av_buffer_start is None:
                if pts_s + dur_s < target_time_sec - 0.05:
                    continue
                self._av_buffer_start = int(round(pts_s * self.sample_rate))

            for rf in self._av_resampler.resample(frame):
                arr = rf.to_ndarray()
                mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                self._av_buffer = np.concatenate([self._av_buffer, mono]) if len(self._av_buffer) else mono

            if self._av_buffer_start is not None and self._av_buffer_start + len(self._av_buffer) >= needed_end:
                break

        if self._av_buffer_start is None or len(self._av_buffer) == 0:
            return np.zeros(sample_count, dtype=np.float32)

        offset = start_sample - self._av_buffer_start
        if offset < 0:
            pad_len = -offset
            valid_len = min(len(self._av_buffer), sample_count - pad_len)
            valid = self._av_buffer[:valid_len] if valid_len > 0 else np.empty(0, dtype=np.float32)
            res = np.concatenate([np.zeros(pad_len, dtype=np.float32), valid])
            if len(res) < sample_count:
                res = np.concatenate([res, np.zeros(sample_count - len(res), dtype=np.float32)])
            return res[:sample_count]

        slice_data = self._av_buffer[offset : offset + sample_count]
        if len(slice_data) < sample_count:
            pad = np.zeros(sample_count - len(slice_data), dtype=np.float32)
            return np.concatenate([slice_data, pad])
        return slice_data

    def close(self) -> None:
        """Release container and underlying file handles."""
        self._is_closed = True
        self._av_buffer = np.empty(0, dtype=np.float32)
        self._av_resampler = None
        if self._sf_file is not None:
            try:
                self._sf_file.close()
            except Exception:
                pass
            self._sf_file = None

        if self._av_container is not None:
            try:
                self._av_container.close()
            except Exception:
                pass
            self._av_container = None
            self._av_stream = None

    def __enter__(self) -> AudioReader:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
