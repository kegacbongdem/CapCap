#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_media_audio.py

Unit tests for native audio decoding and windowed audio reading without subprocess CLI.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.media_decode import AudioReader, decode_audio


class TestMediaAudioDecode(unittest.TestCase):
    """Test native audio decoding from in-memory bytes and short sources."""

    def test_decode_wav_bytes_no_subprocess(self):
        """Ensure decode_audio decodes WAV bytes in-memory with zero subprocess calls."""
        source = io.BytesIO()
        test_samples = np.full(1600, 0.25, dtype=np.float32)
        sf.write(source, test_samples, 16000, format="WAV", subtype="FLOAT")
        wav_bytes = source.getvalue()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(wav_bytes, sample_rate=16000)

        self.assertEqual(rate, 16000)
        self.assertEqual(pcm.shape, (1600,))
        self.assertEqual(pcm.dtype, np.float32)
        np.testing.assert_allclose(pcm, 0.25, atol=1e-6)

    def test_decode_stereo_to_mono(self):
        """Ensure stereo audio is correctly downmixed to mono."""
        source = io.BytesIO()
        left = np.full((1000, 1), 0.4, dtype=np.float32)
        right = np.full((1000, 1), 0.2, dtype=np.float32)
        stereo = np.hstack([left, right])
        sf.write(source, stereo, 16000, format="WAV", subtype="FLOAT")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(source.getvalue(), sample_rate=16000)

        self.assertEqual(rate, 16000)
        self.assertEqual(pcm.shape, (1000,))
        # Expected mono is (0.4 + 0.2) / 2 = 0.3
        np.testing.assert_allclose(pcm, 0.3, atol=1e-5)

    def test_decode_resampling(self):
        """Ensure audio resamples accurately from 44100 Hz to 16000 Hz."""
        source = io.BytesIO()
        # 1 second of 44100 Hz audio
        samples_44k = (np.sin(2 * np.pi * 440 * np.linspace(0, 1.0, 44100, endpoint=False))).astype(np.float32)
        sf.write(source, samples_44k, 44100, format="WAV", subtype="FLOAT")

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(source.getvalue(), sample_rate=16000)

        self.assertEqual(rate, 16000)
        # Should be roughly 16000 samples (+/- small filter edge)
        self.assertTrue(abs(len(pcm) - 16000) <= 20)

    def test_decode_corrupted_input(self):
        """Ensure corrupted audio raises ValueError or RuntimeError without crashing."""
        corrupted = b"NOT_A_VALID_AUDIO_FILE_HEADER_12345"
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            decode_audio(corrupted)

    def test_decode_empty_input(self):
        """Ensure empty input raises ValueError."""
        with self.assertRaises(ValueError):
            decode_audio(b"")

    def test_decode_mp3_no_subprocess(self):
        """Ensure MP3 audio can be decoded without subprocess calls."""
        source = io.BytesIO()
        test_samples = (np.sin(2 * np.pi * 440 * np.linspace(0, 0.5, 8000, endpoint=False))).astype(np.float32)
        sf.write(source, test_samples, 16000, format="MP3")
        mp3_bytes = source.getvalue()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")):
            pcm, rate = decode_audio(mp3_bytes, sample_rate=16000)

        self.assertEqual(rate, 16000)
        self.assertTrue(len(pcm) >= 7900)  # codec padding / delay allowance
        self.assertEqual(pcm.dtype, np.float32)

    def test_decode_packed_integer_pcm16_matroska(self):
        """Ensure decode_audio decodes packed integer s16 stereo Matroska to float32 mono."""
        import av
        import fractions

        mkv_path = os.path.join(PROJECT_ROOT, "temp", "test_s16_decode.mkv")
        os.makedirs(os.path.dirname(mkv_path), exist_ok=True)
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=16000)
        stream.time_base = fractions.Fraction(1, 16000)

        left = np.full(1600, 0.25 * 32767, dtype=np.int16)
        right = np.full(1600, 0.25 * 32767, dtype=np.int16)
        interleaved = np.empty(3200, dtype=np.int16)
        interleaved[0::2] = left
        interleaved[1::2] = right
        frame = av.AudioFrame.from_ndarray(interleaved.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = 16000
        frame.time_base = stream.time_base
        for p in stream.encode(frame):
            container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with open(mkv_path, "rb") as f:
                raw_bytes = f.read()
            pcm, rate = decode_audio(raw_bytes, sample_rate=16000)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(pcm), 1600)
            self.assertEqual(pcm.dtype, np.float32)
            np.testing.assert_allclose(pcm, 0.25, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass


class TestAudioReader(unittest.TestCase):
    """Test bounded windowed AudioReader."""

    def setUp(self):
        self.temp_dir = os.path.join(PROJECT_ROOT, "temp", "test_audio_reader")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.wav_path = os.path.join(self.temp_dir, "test_tone.wav")

        # Create a 5-second 16kHz test sine wave with continuous index ramp
        self.sr = 16000
        self.total_samples = 5 * self.sr  # 80,000 samples
        # Float ramp values from 0.0 to 1.0 for exact index checking
        self.ground_truth = (np.linspace(0.0, 1.0, self.total_samples, endpoint=False)).astype(np.float32)
        sf.write(self.wav_path, self.ground_truth, self.sr, format="WAV", subtype="FLOAT")

    def tearDown(self):
        if os.path.exists(self.wav_path):
            try:
                os.remove(self.wav_path)
            except OSError:
                pass

    def test_sequential_window_reading(self):
        """Test reading contiguous blocks sequentially."""
        block_size = 1600  # 100ms
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            for i in range(10):
                start = i * block_size
                block = reader.read(start, block_size)
                self.assertEqual(len(block), block_size)
                expected = self.ground_truth[start : start + block_size]
                np.testing.assert_allclose(block, expected, atol=1e-6)

    def test_random_seek_and_alignment(self):
        """Test random forward and backward seek and exact sample alignment."""
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            # Seek far forward
            start = 32000
            block = reader.read(start, 800)
            self.assertEqual(len(block), 800)
            np.testing.assert_allclose(block, self.ground_truth[start : start + 800], atol=1e-6)

            # Seek backward
            start_back = 4800
            block_back = reader.read(start_back, 1600)
            self.assertEqual(len(block_back), 1600)
            np.testing.assert_allclose(block_back, self.ground_truth[start_back : start_back + 1600], atol=1e-6)

    def test_eof_padding(self):
        """Test that reading past EOF pads with zeros."""
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            # Start 500 samples before EOF, request 1500 samples
            start = self.total_samples - 500
            block = reader.read(start, 1500)
            self.assertEqual(len(block), 1500)

            # First 500 should match ground truth
            np.testing.assert_allclose(block[:500], self.ground_truth[start:], atol=1e-6)
            # Remaining 1000 must be zero
            np.testing.assert_allclose(block[500:], 0.0, atol=1e-7)

    def test_negative_start_sample(self):
        """Test that start_sample < 0 pads beginning with zeros."""
        with AudioReader(self.wav_path, sample_rate=self.sr) as reader:
            # Start at -500, read 1000 samples
            block = reader.read(-500, 1000)
            self.assertEqual(len(block), 1000)
            # First 500 are zero
            np.testing.assert_allclose(block[:500], 0.0, atol=1e-7)
            # Next 500 match ground truth [0:500]
            np.testing.assert_allclose(block[500:], self.ground_truth[:500], atol=1e-6)

    def test_multiple_open_close_cycles(self):
        """Ensure repeated opening and closing does not fail or leak file handles."""
        for _ in range(50):
            reader = AudioReader(self.wav_path, sample_rate=self.sr)
            block = reader.read(0, 320)
            self.assertEqual(len(block), 320)
            reader.close()

    def test_audio_reader_video_container(self):
        """Ensure AudioReader can open video container (MP4) with audio and read blocks."""
        import av
        import fractions

        mp4_path = os.path.join(self.temp_dir, "test_container.mp4")
        container = av.open(mp4_path, mode="w", format="mp4")
        stream = container.add_stream("aac", rate=self.sr)
        stream.time_base = fractions.Fraction(1, self.sr)

        tone = (np.sin(2 * np.pi * 440 * np.linspace(0, 2.0, 2 * self.sr))).astype(np.float32)
        frame = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = self.sr
        frame.time_base = stream.time_base
        frame.pts = 0

        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()

        try:
            with AudioReader(mp4_path, sample_rate=self.sr) as reader:
                block = reader.read(0, 1600)
                self.assertEqual(len(block), 1600)
                self.assertEqual(block.dtype, np.float32)
                # Verify non-zero content
                self.assertTrue(np.max(np.abs(block)) > 0.01)
        finally:
            if os.path.exists(mp4_path):
                try:
                    os.remove(mp4_path)
                except OSError:
                    pass

    def test_audio_reader_packed_integer_pcm16_matroska(self):
        """Ensure AudioReader decodes packed s16 stereo Matroska accurately."""
        import av
        import fractions

        mkv_path = os.path.join(self.temp_dir, "test_s16_reader.mkv")
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=self.sr)
        stream.time_base = fractions.Fraction(1, self.sr)

        left = np.full(1600, 0.25 * 32767, dtype=np.int16)
        right = np.full(1600, 0.25 * 32767, dtype=np.int16)
        interleaved = np.empty(3200, dtype=np.int16)
        interleaved[0::2] = left
        interleaved[1::2] = right
        frame = av.AudioFrame.from_ndarray(interleaved.reshape(1, -1), format="s16", layout="stereo")
        frame.sample_rate = self.sr
        frame.time_base = stream.time_base
        for p in stream.encode(frame):
            container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with AudioReader(mkv_path, sample_rate=self.sr) as reader:
                block = reader.read(0, 1600)
                self.assertEqual(len(block), 1600)
                self.assertEqual(block.dtype, np.float32)
                np.testing.assert_allclose(block, 0.25, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass

    def test_pyav_audio_reader_seek_pts_alignment(self):
        """Ensure seeking in PyAV container precisely aligns PTS timestamps with samples."""
        import av
        import fractions

        mkv_path = os.path.join(self.temp_dir, "test_pts_alignment.mkv")
        container = av.open(mkv_path, mode="w", format="matroska")
        stream = container.add_stream("pcm_s16le", rate=16000, layout="mono")
        full_pcm = np.linspace(0, 1.0, 16000, dtype=np.float32)
        frame_size = 1000
        for i in range(16):
            chunk = full_pcm[i * frame_size : (i + 1) * frame_size]
            int_chunk = (chunk * 32767).astype(np.int16)
            frame = av.AudioFrame.from_ndarray(int_chunk.reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = 16000
            frame.pts = i * frame_size
            frame.time_base = fractions.Fraction(1, 16000)
            for p in stream.encode(frame):
                container.mux(p)
        for p in stream.encode(None):
            container.mux(p)
        container.close()

        try:
            with AudioReader(mkv_path, sample_rate=16000) as reader:
                block = reader.read(4800, 160)
                expected = full_pcm[4800:4960]
                self.assertEqual(len(block), 160)
                np.testing.assert_allclose(block, expected, atol=1e-3)
        finally:
            if os.path.exists(mkv_path):
                try:
                    os.remove(mkv_path)
                except OSError:
                    pass

    def test_resample_continuous_dc_window(self):
        """Ensure reading 10ms windows across sample rates preserves filter continuity without edge drop."""
        wav_44k = os.path.join(self.temp_dir, "test_dc_44k.wav")
        dc_val = 0.5
        samples_44k = np.full(44100, dc_val, dtype=np.float32)
        sf.write(wav_44k, samples_44k, 44100, format="WAV", subtype="FLOAT")

        try:
            with AudioReader(wav_44k, sample_rate=16000) as reader:
                block_size = 160  # 10ms at 16kHz
                blocks = []
                # Read consecutive windows across 100ms to 400ms
                for i in range(10, 40):
                    b = reader.read(i * block_size, block_size)
                    blocks.append(b)
                concatenated = np.concatenate(blocks)
                # Max difference from 0.5 must be < 0.01 (Issue 5)
                max_diff = float(np.max(np.abs(concatenated - dc_val)))
                self.assertLess(max_diff, 0.01)
        finally:
            if os.path.exists(wav_44k):
                try:
                    os.remove(wav_44k)
                except OSError:
                    pass


class TestAudioMixerPCM(unittest.TestCase):
    """Test block-based PCM mixing and linear gain clipping."""

    def test_mix_pcm_block_linear_and_clip(self):
        """Test linear gain combination and clipping to [-1.0, 1.0]."""
        from app.audio_mixer import mix_pcm_block

        a = np.array([0.2, 0.9], dtype=np.float32)
        b = np.array([0.4, 0.9], dtype=np.float32)
        original = a.copy()
        mixed = mix_pcm_block([a, b], [0.5, 1.0])
        np.testing.assert_allclose(mixed, [0.5, 1.0], atol=1e-6)
        np.testing.assert_array_equal(a, original)

    def test_mix_pcm_block_negative_clipping(self):
        """Test negative overload clips to -1.0."""
        from app.audio_mixer import mix_pcm_block

        a = np.array([-0.8, -0.6], dtype=np.float32)
        b = np.array([-0.5, -0.7], dtype=np.float32)
        mixed = mix_pcm_block([a, b], [1.0, 1.0])
        # -0.8 + -0.5 = -1.3 -> -1.0; -0.6 + -0.7 = -1.3 -> -1.0
        np.testing.assert_allclose(mixed, [-1.0, -1.0], atol=1e-6)

    def test_mix_pcm_block_length_mismatch(self):
        """Ensure blocks with different lengths raise ValueError."""
        from app.audio_mixer import mix_pcm_block

        a = np.array([0.1, 0.2], dtype=np.float32)
        b = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        with self.assertRaises(ValueError):
            mix_pcm_block([a, b], [1.0, 1.0])

    def test_mix_pcm_block_empty(self):
        """Ensure empty input returns empty array."""
        from app.audio_mixer import mix_pcm_block

        mixed = mix_pcm_block([], [])
        self.assertEqual(len(mixed), 0)
        self.assertEqual(mixed.dtype, np.float32)

    def test_mix_pcm_block_does_not_mutate_nan_inputs(self):
        """Ensure blocks containing NaN or inf are not mutated in-place."""
        from app.audio_mixer import mix_pcm_block

        input_arr = np.array([0.5, np.nan, np.inf, -np.inf], dtype=np.float32)
        mixed = mix_pcm_block([input_arr], [1.0])
        # Output must be cleaned and clamped
        np.testing.assert_allclose(mixed, [0.5, 0.0, 1.0, -1.0], atol=1e-6)
        # Original input MUST still contain NaN and inf (never mutated in-place)
        self.assertTrue(np.isnan(input_arr[1]))
        self.assertTrue(np.isposinf(input_arr[2]))
        self.assertTrue(np.isneginf(input_arr[3]))

    def test_mix_pcm_block_gains_count_mismatch(self):
        """Ensure mismatch between number of blocks and number of gains raises ValueError."""
        from app.audio_mixer import mix_pcm_block

        b1 = np.array([0.1, 0.2], dtype=np.float32)
        b2 = np.array([0.3, 0.4], dtype=np.float32)
        with self.assertRaises(ValueError):
            # 2 blocks but only 1 gain
            mix_pcm_block([b1, b2], [1.0])


class TestPreviewAudioEngine(unittest.TestCase):
    """Test PreviewAudioEngine lifecycle and asynchronous controls."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self):
        self.temp_dir = os.path.join(PROJECT_ROOT, "temp", "test_preview_engine")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.wav_path = os.path.join(self.temp_dir, "test_track.wav")
        tone = (np.sin(2 * np.pi * 440 * np.linspace(0, 3.0, 3 * 16000))).astype(np.float32) * 0.5
        sf.write(self.wav_path, tone, 16000, format="WAV", subtype="FLOAT")

    def tearDown(self):
        if os.path.exists(self.wav_path):
            try:
                os.remove(self.wav_path)
            except OSError:
                pass

    def test_engine_lifecycle(self):
        """Ensure PreviewAudioEngine initializes, sets tracks, seeks, and closes cleanly."""
        from ui.utils.preview_audio import PreviewAudioEngine

        engine = PreviewAudioEngine()
        try:
            tracks = [
                {
                    "id": "track_1",
                    "path": self.wav_path,
                    "start": 0.0,
                    "end": 3.0,
                    "volume": 80.0,
                    "muted": False,
                }
            ]
            engine.set_tracks(tracks)
            engine.set_track_gain("track_1", 0.5, muted=False)
            engine.seek(1200)
            self.assertEqual(engine.timeline_position_ms(), 1200)

            engine.play()
            self.app.processEvents()
            engine.pause()
            self.app.processEvents()
            engine.stop()
            self.assertEqual(engine.timeline_position_ms(), 0)
        finally:
            engine.close()

    def test_music_clip_seamless_looping(self):
        """Ensure looping music track wraps seamlessly across reader boundaries."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        ramp_wav = os.path.join(self.temp_dir, "test_ramp.wav")
        ramp_samples = (np.linspace(0.0, 1.0, 1000, endpoint=False)).astype(np.float32)
        sf.write(ramp_wav, ramp_samples, 16000, format="WAV", subtype="FLOAT")

        worker = _PreviewAudioWorker()
        try:
            worker.block_size = 320
            worker._sink_sr = 16000
            worker._sink_channels = 1
            worker._sink_is_float = True
            worker._is_playing = True
            worker._tracks = [
                {
                    "id": "loop_music",
                    "path": ramp_wav,
                    "start_ms": 0,
                    "end_ms": 10000,
                    "source_start_ms": 0,
                    "volume": 100.0,
                    "muted": False,
                    "target_gain": 1.0,
                    "current_gain": 1.0,
                    "loop": True,
                    "is_original_video": False,
                }
            ]
            worker._readers = {ramp_wav: AudioReader(ramp_wav, sample_rate=16000)}
            mock_io = MagicMock()
            mock_io.write.return_value = 1280
            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            worker._sink = mock_sink
            worker._io_device = mock_io

            # Set timeline position near loop boundary (sample 960 -> 960/16 = 60ms exactly)
            # Sample 960..1280 spans across boundary: 960..999 is [960:1000] (40 samples), 1000..1279 is [0:280] (280 samples)
            worker._timeline_pos_ms = 60
            worker._on_timer_tick()

            self.assertTrue(mock_io.write.called)
            written_bytes = mock_io.write.call_args[0][0]
            written_samples = np.frombuffer(written_bytes, dtype=np.float32)
            self.assertEqual(len(written_samples), 320)
            # Verify continuity: first 40 samples match ramp[960:], next 280 match ramp[:280]
            np.testing.assert_allclose(written_samples[:40], ramp_samples[960:1000], atol=1e-4)
            np.testing.assert_allclose(written_samples[40:], ramp_samples[:280], atol=1e-4)
        finally:
            worker.close()
            if os.path.exists(ramp_wav):
                try:
                    os.remove(ramp_wav)
                except OSError:
                    pass

    def test_audio_worker_clock_advances_only_on_written_bytes(self):
        """Ensure audio clock does not advance when io_device.write returns 0."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        worker = _PreviewAudioWorker()
        try:
            worker.block_size = 320
            worker._sink_sr = 16000
            worker._sink_channels = 1
            worker._sink_is_float = True
            worker._is_playing = True
            worker._timeline_pos_ms = 500
            worker._tracks = []
            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            mock_io = MagicMock()
            # Simulate sink buffer full: 0 bytes written
            mock_io.write.return_value = 0
            worker._sink = mock_sink
            worker._io_device = mock_io

            worker._on_timer_tick()
            # Clock should remain at 500
            self.assertEqual(worker._timeline_pos_ms, 500)

            # Now simulate successful write of 320 float32 samples (1280 bytes)
            mock_io.write.return_value = 1280
            worker._on_timer_tick()
            # 320 samples @ 16kHz = 20ms -> 500 + 20 = 520ms
            self.assertEqual(worker._timeline_pos_ms, 520)
        finally:
            worker.close()

    def test_atempo_playback_rate(self):
        """Ensure set_rate configures tempo filter on worker."""
        from ui.utils.preview_audio import _PreviewAudioWorker

        worker = _PreviewAudioWorker()
        try:
            worker.set_rate(1.5)
            self.assertEqual(worker._playback_rate, 1.5)
            # Setting close to 1.0 resets tempo graph
            worker.set_rate(1.0)
            self.assertEqual(worker._playback_rate, 1.0)
        finally:
            worker.close()

    def test_atempo_clock_accuracy_2x_and_half_x(self):
        """Ensure 100 ticks at 2.0x reaches ~2000 ms and at 0.5x reaches ~500 ms."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        for rate, expected_ms in ((2.0, 2000), (0.5, 500)):
            worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
            try:
                worker._sink_sr = 16000
                worker._sink_channels = 1
                worker._sink_is_float = True
                worker._is_playing = True
                worker._tracks = []
                worker.set_rate(rate)

                mock_sink = MagicMock()
                mock_sink.bytesFree.return_value = 100000
                mock_io = MagicMock()
                # 160 samples float32 = 640 bytes written per tick
                mock_io.write.return_value = 640
                worker._sink = mock_sink
                worker._io_device = mock_io

                for _ in range(100):
                    worker._on_timer_tick()

                self.assertEqual(worker._timeline_pos_ms, expected_ms)
            finally:
                worker.close()

    def test_partial_write_buffer_preservation(self):
        """Ensure partial sink writes buffer trailing bytes without dropping them."""
        from ui.utils.preview_audio import _PreviewAudioWorker
        from unittest.mock import MagicMock

        worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
        try:
            worker._sink_sr = 16000
            worker._sink_channels = 1
            worker._sink_is_float = True
            worker._is_playing = True
            worker._tracks = []

            mock_sink = MagicMock()
            mock_sink.bytesFree.return_value = 100000
            mock_io = MagicMock()
            worker._sink = mock_sink
            worker._io_device = mock_io

            # Block size 160 float32 = 640 bytes
            # Tick 1: only 320 bytes accepted by sink
            mock_io.write.return_value = 320
            worker._on_timer_tick()
            # Clock advanced by 320 bytes (80 samples = 5 ms)
            self.assertEqual(worker._timeline_pos_ms, 5)
            self.assertEqual(len(worker._pending_write_bytes), 320)

            # Tick 2: sink accepts remaining 320 bytes
            mock_io.write.return_value = 320
            worker._on_timer_tick()
            # Clock advances remaining 5 ms (total 10 ms = 160 samples)
            self.assertEqual(worker._timeline_pos_ms, 10)
            self.assertEqual(len(worker._pending_write_bytes), 0)
        finally:
            worker.close()


if __name__ == "__main__":
    unittest.main()
