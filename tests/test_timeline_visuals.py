#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_timeline_visuals.py

Unit tests for in-process thumbnail decoding and O(1) streaming waveform generation.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
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

import av
from app.media_decode import build_waveform, iter_video_thumbnails


class TestTimelineVisuals(unittest.TestCase):
    """Test native in-process video thumbnail and waveform generation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_timeline_visuals_")

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_video(self, filename: str = "synth_test.mp4") -> str:
        """Create a 2-second synthetic video: first second RED, second second BLUE."""
        video_path = os.path.join(self.temp_dir, filename)
        container = av.open(video_path, mode="w", format="mp4")
        stream = container.add_stream("h264", rate=25)
        stream.width = 320
        stream.height = 240
        stream.pix_fmt = "yuv420p"

        # 50 frames: 25 red (0.0s - 0.96s), 25 blue (1.0s - 1.96s)
        for i in range(50):
            frame_arr = np.zeros((240, 320, 3), dtype=np.uint8)
            if i < 25:
                frame_arr[:, :, 0] = 240  # Red
            else:
                frame_arr[:, :, 2] = 240  # Blue
            frame = av.VideoFrame.from_ndarray(frame_arr, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()
        return video_path

    def _create_synthetic_audio(self, filename: str = "synth_audio.wav", duration_s: float = 3.0) -> str:
        """Create a synthetic multi-tone audio file."""
        audio_path = os.path.join(self.temp_dir, filename)
        sr = 16000
        t = np.linspace(0, duration_s, int(duration_s * sr), endpoint=False)
        audio = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
        sf.write(audio_path, audio, sr, format="WAV", subtype="PCM_16")
        return audio_path

    def test_iter_video_thumbnails_pts_and_colors(self):
        """Ensure iter_video_thumbnails decodes frames with accurate PTS and colors with 0 subprocess calls."""
        video_path = self._create_synthetic_video()

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            thumbnails = list(iter_video_thumbnails(video_path, [0.0, 1.2], width=180))

        self.assertEqual(len(thumbnails), 2)

        # First thumbnail at t=0.0s (Red)
        pts_0, rgb_0 = thumbnails[0]
        self.assertAlmostEqual(pts_0, 0.0, delta=0.08)
        self.assertEqual(rgb_0.shape[1], 180)  # Width
        self.assertEqual(rgb_0.shape[2], 3)    # RGB channels
        self.assertEqual(rgb_0.dtype, np.uint8)
        # Verify dominant red color
        self.assertGreater(rgb_0[:, :, 0].mean(), 180)
        self.assertLess(rgb_0[:, :, 2].mean(), 60)

        # Second thumbnail at t=1.2s (Blue)
        pts_1, rgb_1 = thumbnails[1]
        self.assertAlmostEqual(pts_1, 1.2, delta=0.08)
        self.assertEqual(rgb_1.shape[1], 180)
        self.assertEqual(rgb_1.shape[2], 3)
        self.assertEqual(rgb_1.dtype, np.uint8)
        # Verify dominant blue color
        self.assertGreater(rgb_1[:, :, 2].mean(), 180)
        self.assertLess(rgb_1[:, :, 0].mean(), 60)

    def test_iter_video_thumbnails_empty_and_invalid(self):
        """Ensure iter_video_thumbnails gracefully handles empty or invalid inputs."""
        video_path = self._create_synthetic_video()

        # Empty timestamps
        self.assertEqual(list(iter_video_thumbnails(video_path, [])), [])

        # Non-existent file
        self.assertEqual(list(iter_video_thumbnails("non_existent_file.mp4", [0.0, 1.0])), [])

        # Audio-only file
        audio_path = self._create_synthetic_audio()
        self.assertEqual(list(iter_video_thumbnails(audio_path, [0.0, 1.0])), [])

    def test_build_waveform_matches_batch(self):
        """Ensure streaming build_waveform matches the batch envelope algorithm with 0 subprocess calls."""
        audio_path = self._create_synthetic_audio(duration_s=4.0)

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            waveform, duration_s = build_waveform(audio_path, bucket_count=300)

        self.assertAlmostEqual(duration_s, 4.0, delta=0.05)
        self.assertGreater(len(waveform), 0)
        self.assertTrue(all(0.0 <= x <= 1.0 for x in waveform))

        # Compute batch reference
        data, sr = sf.read(audio_path, dtype="float32")
        peak = float(np.max(np.abs(data)))
        norm_samples = data / peak
        actual_buckets = int(min(300, max(240, round(duration_s * 12.0))))
        chunk_size = max(256, int(np.ceil(norm_samples.size / max(1, actual_buckets))))
        batch_wf = []
        for start in range(0, norm_samples.size, chunk_size):
            chunk = norm_samples[start:start + chunk_size]
            abs_chunk = np.abs(chunk)
            pv = float(np.max(abs_chunk)) if abs_chunk.size else 0.0
            rv = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
            val = max(pv, rv * 1.15)
            batch_wf.append(min(1.0, max(0.03, val ** 0.85)))

        self.assertEqual(len(waveform), len(batch_wf))
        np.testing.assert_allclose(waveform, batch_wf, atol=1e-4)

    def test_build_waveform_silence_and_no_audio(self):
        """Ensure build_waveform returns zeros for silence and empty for no-audio without raising."""
        # 1. Pure silence
        silence_path = os.path.join(self.temp_dir, "silence.wav")
        sf.write(silence_path, np.zeros(32000, dtype=np.float32), 16000, format="WAV", subtype="PCM_16")
        wf_silence, dur_silence = build_waveform(silence_path)
        self.assertAlmostEqual(dur_silence, 2.0, delta=0.05)
        self.assertTrue(all(x == 0.0 for x in wf_silence))

        # 2. Video without audio stream
        video_path = self._create_synthetic_video()
        wf_no_audio, dur_video = build_waveform(video_path)
        self.assertEqual(wf_no_audio, [])
        self.assertAlmostEqual(dur_video, 2.0, delta=0.1)

        # 3. Non-existent path
        wf_none, dur_none = build_waveform("does_not_exist.wav")
        self.assertEqual(wf_none, [])
        self.assertEqual(dur_none, 0.0)

    def test_timeline_waveform_worker_native(self):
        """Ensure TimelineWaveformWorker runs in-process without invoking FFmpeg CLI."""
        from ui.worker_adapters.processing_workers import TimelineWaveformWorker

        audio_path = self._create_synthetic_audio(duration_s=2.5)
        worker = TimelineWaveformWorker(
            request_signature="req_test_wf",
            video_path="",
            audio_path=audio_path,
            temp_audio_path=os.path.join(self.temp_dir, "temp_audio.wav"),
            duration_s=2.5,
        )

        results = []
        worker.finished.connect(lambda sig, wf, dur, err: results.append((sig, wf, dur, err)))

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            worker.run()

        self.assertEqual(len(results), 1)
        sig, wf, dur, err = results[0]
        self.assertEqual(sig, "req_test_wf")
        self.assertEqual(err, "")
        self.assertGreater(len(wf), 0)
        self.assertAlmostEqual(dur, 2.5, delta=0.1)

    def test_timeline_thumbnail_worker_native(self):
        """Ensure TimelineThumbnailWorker runs in-process without invoking FFmpeg CLI."""
        from ui.worker_adapters.processing_workers import TimelineThumbnailWorker

        video_path = self._create_synthetic_video()
        thumb_dir = os.path.join(self.temp_dir, "worker_thumbs")
        worker = TimelineThumbnailWorker(
            request_signature="req_test_thumbs",
            video_path=video_path,
            duration_s=2.0,
            thumb_dir=thumb_dir,
        )

        results = []
        worker.finished.connect(lambda sig, thumbs, err: results.append((sig, thumbs, err)))

        with patch("subprocess.Popen", side_effect=AssertionError("CLI invoked")), \
             patch("subprocess.run", side_effect=AssertionError("CLI invoked")):
            worker.run()

        self.assertEqual(len(results), 1)
        sig, thumbs, err = results[0]
        self.assertEqual(sig, "req_test_thumbs")
        self.assertEqual(err, "")
        self.assertGreater(len(thumbs), 0)
        for pts, path in thumbs:
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)


if __name__ == "__main__":
    unittest.main()
