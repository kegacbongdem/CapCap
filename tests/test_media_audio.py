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


class TestPreviewAudioEngine(unittest.TestCase):
    """Test PreviewAudioEngine lifecycle and asynchronous controls."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance()
        if cls.app is None:
            cls.app = QCoreApplication([])

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


if __name__ == "__main__":
    unittest.main()
