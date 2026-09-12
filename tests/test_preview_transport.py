#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_preview_transport.py

Unit tests for preview transport, exact/keyframe seeking, timeline clock,
and TimeWarpService synchronization.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.services.time_warp_service import TimeWarpService


class TestPreviewTransport(unittest.TestCase):
    """Test transport synchronization, freeze mapping, and seek flags."""

    def test_time_warp_freeze_mapping(self):
        """Verify timeline-to-media time mapping with freeze frame warps."""
        warps = [{"time": 2.0, "duration": 1.0}]
        # Within the freeze interval (2.0s to 3.0s on timeline), media stays locked at anchor 2.0s
        self.assertEqual(TimeWarpService.timeline_to_media_time(2.5, warps), 2.0)
        # After the freeze interval, media resumes (3.5s timeline - 1.0s freeze = 2.5s media)
        self.assertEqual(TimeWarpService.timeline_to_media_time(3.5, warps), 2.5)

    def test_time_warp_multiple_freezes(self):
        """Verify mapping with multiple sequential freeze intervals."""
        warps = [
            {"time": 1.0, "duration": 0.5},  # Timeline [1.0, 1.5] -> Media 1.0
            {"time": 3.0, "duration": 1.0},  # Timeline [3.5, 4.5] -> Media 3.0
        ]
        self.assertEqual(TimeWarpService.timeline_to_media_time(0.5, warps), 0.5)
        self.assertEqual(TimeWarpService.timeline_to_media_time(1.2, warps), 1.0)
        self.assertEqual(TimeWarpService.timeline_to_media_time(2.0, warps), 1.5)
        self.assertEqual(TimeWarpService.timeline_to_media_time(4.0, warps), 3.0)
        self.assertEqual(TimeWarpService.timeline_to_media_time(5.0, warps), 3.5)

    def test_mpv_backend_seek_exact_vs_keyframe_flags(self):
        """Verify MpvMediaPlayerBackend passes exact vs keyframe flags to mpv command."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        # Mock video_view and underlying mpv player
        view = MagicMock()
        view.winId.return_value = 0
        view.video_source_width = 1920
        view.video_source_height = 1080

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "mock_video.mp4"
        backend._position_ms = 0
        backend._video_frozen = False
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend._native_audio_active = False
        backend._native_audio_engine = None
        backend._video_time_warps = []
        backend.positionChanged = MagicMock()

        mock_player = MagicMock()
        backend._player = mock_player

        # 1. Seek with exact=True (default)
        backend.setPosition(2500, exact=True)
        mock_player.command.assert_called_with("seek", 2.5, "absolute", "exact")

        # 2. Seek with exact=False (scrubbing fast seek)
        backend.setPosition(3000, exact=False)
        mock_player.command.assert_called_with("seek", 3.0, "absolute", "keyframes")

    def test_seek_burst_throttling_simulation(self):
        """Simulate rapid scrubbing burst where only the release seek is exact."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "mock_video.mp4"
        backend._position_ms = 0
        backend._video_frozen = False
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend._native_audio_active = False
        backend._native_audio_engine = None
        backend._video_time_warps = []
        backend.positionChanged = MagicMock()

        mock_player = MagicMock()
        backend._player = mock_player

        # Simulate 10 rapid scrub steps (during mouse drag)
        for t_ms in range(100, 1100, 100):
            backend.setPosition(t_ms, exact=False)
            mock_player.command.assert_called_with("seek", t_ms / 1000.0, "absolute", "keyframes")

        # Final mouse release at 1200ms
        backend.setPosition(1200, exact=True)
        mock_player.command.assert_called_with("seek", 1.2, "absolute", "exact")
        self.assertEqual(backend.position(), 1200)


if __name__ == "__main__":
    unittest.main()
