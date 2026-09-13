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
from unittest.mock import MagicMock, patch

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

    def test_set_position_exact_signature_forwarding(self):
        """Verify set_position forwards exact flag to media_player.setPosition."""
        import inspect
        from ui.main_window import VideoTranslatorGUI
        from ui.utils.media_utils import set_position

        # Verify GUI set_position signature accepts exact
        sig = inspect.signature(VideoTranslatorGUI.set_position)
        self.assertIn("exact", sig.parameters)
        self.assertIs(sig.parameters["exact"].default, True)

        # Verify media_utils.set_position forwards exact flag
        gui = MagicMock()
        gui.is_filter_workflow_active.return_value = False
        gui.video_time_warps = []
        gui.media_player.duration.return_value = 10000

        set_position(gui, 3000, exact=False)
        gui.media_player.setPosition.assert_called_with(3000, timeline_pos=3000, exact=False)

        set_position(gui, 4000, exact=True)
        gui.media_player.setPosition.assert_called_with(4000, timeline_pos=4000, exact=True)

    def test_timeline_scrub_finished_attribute(self):
        """Verify timeline mouse release emits scrubFinished with integer playhead ms without AttributeError."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        _ = QApplication.instance() or QApplication([])
        from ui.views.editor.timeline import EditorTimeline

        timeline = EditorTimeline.__new__(EditorTimeline)
        timeline._selection_drag = {"mode": "scrub"}
        timeline._playhead = 2.5
        timeline.scrubFinished = MagicMock()
        event = MagicMock()
        event.button.return_value = Qt.LeftButton

        timeline.mouseReleaseEvent(event)
        timeline.scrubFinished.emit.assert_called_once_with(2500)

    def test_sync_audio_does_not_activate_sidecars_when_native_active(self):
        """Verify legacy audio sidecars are never resumed when native audio is active."""
        from PySide6.QtMultimedia import QMediaPlayer
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._player = MagicMock()
        backend._player.time_pos = 2.0
        backend._player.pause = False
        backend._native_audio_active = True
        backend._native_audio_engine = MagicMock()
        backend._native_audio_engine.timeline_position_ms.return_value = 2000

        backend._original_loaded_path = "orig.wav"
        backend._original_player = MagicMock()
        backend._original_player.playbackState.return_value = QMediaPlayer.PlayingState

        backend._dubbed_loaded_path = "dub.wav"
        backend._dubbed_player = MagicMock()
        backend._dubbed_player.playbackState.return_value = QMediaPlayer.PlayingState

        backend._sync_audio_to_video()

        # Legacy players must be paused and NEVER played
        backend._original_player.pause.assert_called_once()
        backend._dubbed_player.pause.assert_called_once()
        backend._original_player.play.assert_not_called()
        backend._dubbed_player.play.assert_not_called()
        # Native engine must receive play
        backend._native_audio_engine.play.assert_called_once()

    def test_freeze_audio_mapping_two_accumulated_freezes(self):
        """Verify freeze detection correctly accumulates multiple warp durations."""
        from ui.utils.preview_audio import _PreviewAudioWorker

        worker = _PreviewAudioWorker.__new__(_PreviewAudioWorker)
        worker._warps = [
            {"time": 2.0, "duration": 1.0},
            {"time": 4.0, "duration": 1.0},
        ]
        # First freeze: [2.0, 3.0]
        self.assertTrue(worker._is_time_frozen(2.5))
        # Normal playback: [3.0, 5.0] (corresponds to media [2.0, 4.0])
        self.assertFalse(worker._is_time_frozen(3.5))
        # Second freeze: anchor 4.0 + 1.0 prior duration = [5.0, 6.0]
        self.assertTrue(worker._is_time_frozen(5.5))
        # Normal playback after second freeze: > 6.0
        self.assertFalse(worker._is_time_frozen(6.5))

    def test_native_sink_ready_and_error_fallback(self):
        """Verify _native_audio_active responds to sinkReady and falls back on error."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = False
        backend.log = MagicMock()

        backend._on_native_audio_sink_ready(True)
        self.assertTrue(backend._native_audio_active)

        backend._on_native_audio_sink_ready(False)
        self.assertFalse(backend._native_audio_active)

        backend._native_audio_active = True
        backend._on_native_audio_error("Sink device disconnected")
        self.assertFalse(backend._native_audio_active)

    def test_apply_preview_audio_track_selection_no_intermediate_wav(self):
        """Verify track selection does not generate intermediate mix WAV when native audio is active."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_audio_track_switching = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._resolve_preview_original_video_path.return_value = "video.mp4"
        gui._resolve_preview_original_audio_path.return_value = "orig.wav"
        gui._resolve_preview_voice_only_audio_path.return_value = "voice.wav"
        gui._preferred_preview_audio_track_mode.return_value = "dubbed"
        gui._music_audio_tracks.return_value = []
        gui._compute_audio_track_volume.return_value = 100.0
        gui._get_audio_track_gain_db.return_value = 0.0
        gui._audio_total_duration_ms.return_value = 10000
        gui._is_audio_track_muted.return_value = False

        with patch.object(VideoTranslatorGUI, "_resolve_preview_dubbed_playback_source") as mock_mix:
            VideoTranslatorGUI._apply_preview_audio_track_selection(gui)
            mock_mix.assert_not_called()

    def test_music_audio_tracks_unique_ids_and_gain_calculation(self):
        """Verify unique IDs for music clips and single volume factor."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._normalize_local_file_path = lambda p: p
        layer1 = MagicMock(id="", source="track1.mp3", start=0.0, end=5.0, source_start=0.0)
        layer2 = MagicMock(id="", source="track2.mp3", start=5.0, end=10.0, source_start=0.0)
        track = MagicMock(
            name="A2 Music",
            metadata={"_audio_role": "music", "_volume": 40.0},
            layers=[layer1, layer2],
            muted=False,
            visible=True,
            solo=False,
        )
        gui.timeline = MagicMock()
        gui.timeline._timeline.tracks = [track]
        gui._is_audio_track_muted.return_value = False

        with patch("os.path.exists", return_value=True):
            tracks = VideoTranslatorGUI._music_audio_tracks(gui)
            self.assertEqual(len(tracks), 2)
            self.assertEqual(tracks[0]["id"], "music_layer_0")
            self.assertEqual(tracks[1]["id"], "music_layer_1")
            self.assertNotEqual(tracks[0]["id"], tracks[1]["id"])

        # Verify _apply_audio_track_settings applies linear gain without double multiplication
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._music_audio_tracks.return_value = tracks
        gui._compute_audio_track_volume.return_value = 40.0
        gui._get_audio_track_gain_db.return_value = 0.0

        VideoTranslatorGUI._apply_audio_track_settings(gui, "A2 Music")
        # 40% volume -> gain 0.40
        for t in tracks:
            gui.media_player.set_track_gain.assert_any_call(t["id"], 0.4, False)

    def test_preview_panel_scrub_throttling_and_playback_resume(self):
        """Verify scrub throttling timer and restoring playback state upon release."""
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        _ = QApplication.instance() or QApplication([])

        gui = MagicMock()
        gui._scrub_pending_pos_ms = None
        gui._scrub_was_playing = False
        gui._scrub_throttle_timer = QTimer()
        gui._scrub_throttle_timer.setSingleShot(True)
        gui._scrub_throttle_timer.setInterval(33)

        def _flush_scrub_seek():
            if gui._scrub_pending_pos_ms is not None:
                pos = gui._scrub_pending_pos_ms
                gui._scrub_pending_pos_ms = None
                gui.set_position(pos, exact=False)

        gui._scrub_throttle_timer.timeout.connect(_flush_scrub_seek)

        def _on_scrub_started():
            gui._is_scrubbing = True
            gui._scrub_was_playing = bool(gui.media_player.is_playing())

        def _on_scrub_seek(pos_ms):
            gui._scrub_pending_pos_ms = pos_ms
            if not gui._scrub_throttle_timer.isActive():
                gui._scrub_throttle_timer.start()

        def _on_scrub_finished(final_pos_ms):
            gui._is_scrubbing = False
            if gui._scrub_throttle_timer.isActive():
                gui._scrub_throttle_timer.stop()
            gui._scrub_pending_pos_ms = None
            gui.set_position(final_pos_ms, exact=True)
            if gui._scrub_was_playing:
                gui._scrub_was_playing = False
                gui.media_player.play()

        gui.media_player.is_playing.return_value = True

        # 1. Scrub started while playing
        _on_scrub_started()
        self.assertTrue(gui._scrub_was_playing)

        # 2. Scrub seek bursts
        _on_scrub_seek(100)
        _on_scrub_seek(200)
        self.assertEqual(gui._scrub_pending_pos_ms, 200)
        self.assertTrue(gui._scrub_throttle_timer.isActive())

        # 3. Scrub finished at 500ms
        _on_scrub_finished(500)
        gui.set_position.assert_called_with(500, exact=True)
        gui.media_player.play.assert_called_once()
        self.assertFalse(gui._scrub_was_playing)

    def test_sync_audio_uses_timeline_coords_after_freeze(self):
        """Verify _sync_audio_to_video maps media time through warps and does not seek backwards after freeze."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._player = MagicMock()
        # Video media time is 2500 ms
        backend._player.time_pos = 2.5
        backend._player.pause = False
        backend._native_audio_active = True
        # 1.0s freeze at 2.0s shifts timeline by 1.0s -> media 2.5s corresponds to timeline 3500 ms!
        backend._video_time_warps = [{"time": 2.0, "duration": 1.0}]
        backend._native_audio_engine = MagicMock()
        # Audio is already at timeline 3500 ms
        backend._native_audio_engine.timeline_position_ms.return_value = 3500

        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""

        backend._sync_audio_to_video()

        # Must NOT seek to 2500ms
        backend._native_audio_engine.seek.assert_not_called()

    def test_freeze_unfreeze_never_plays_sidecars_when_native_active(self):
        """Verify freeze and unfreeze do not activate legacy QMediaPlayer sidecars when native audio is active."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._source_path = "video.mp4"
        backend._video_frozen = False
        backend._position_ms = 0
        backend._native_audio_active = True
        backend._native_audio_engine = MagicMock()

        backend._player = MagicMock()
        backend._original_loaded_path = "orig.wav"
        backend._original_player = MagicMock()
        backend._dubbed_loaded_path = "dub.wav"
        backend._dubbed_player = MagicMock()

        # Freeze frame
        backend.freeze_video_frame(2000)
        backend._dubbed_player.play.assert_not_called()
        backend._original_player.play.assert_not_called()

        # Unfreeze frame
        backend.unfreeze_video_frame(2035)
        backend._dubbed_player.play.assert_not_called()
        backend._original_player.play.assert_not_called()
        backend._native_audio_engine.play.assert_called()

    def test_native_error_fallback_stops_engine(self):
        """Verify that on native engine error, engine is stopped and paused to prevent dual audio."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = True
        backend.log = MagicMock()
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine

        backend._on_native_audio_error("Device unplugged")
        self.assertFalse(backend._native_audio_active)
        mock_engine.pause.assert_called_once()
        mock_engine.stop.assert_called_once()

    def test_apply_audio_fade_updates_native_track_gain(self):
        """Verify _apply_audio_fade updates native track gain during fade window."""
        from ui.utils.media_utils import _apply_audio_fade

        gui = MagicMock()
        gui.media_player.duration.return_value = 10000
        gui.media_player._native_audio_active = True
        gui._is_audio_track_muted.return_value = False

        track = MagicMock(metadata={"_fade_in": 2.0, "_volume": 100.0})
        track.name = "A1 Audio"
        gui.timeline._timeline.tracks = [track]

        # At 1.0s (halfway through 2.0s fade-in), gain should be ~0.50
        _apply_audio_fade(gui, 1000)
        gui.media_player.set_track_gain.assert_called_with("A1 Audio", 0.5, False)

    def test_set_time_warps_propagates_to_native_engine(self):
        """Verify set_time_warps updates backend and forwards warps to native engine."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_engine = MagicMock()
        warps = [{"time": 1.5, "duration": 0.5}]

        backend.set_time_warps(warps)
        self.assertEqual(backend._video_time_warps, warps)
        backend._native_audio_engine.set_warps.assert_called_once_with(warps)

    def test_tracks_snapshot_cached_and_sent_on_sink_ready(self):
        """Verify tracks snapshot is cached if sink is not yet ready, then sent when sink becomes ready."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = False
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._video_time_warps = []
        backend.log = MagicMock()

        tracks = [{"id": "t1", "path": "t1.wav"}]
        backend.set_audio_tracks_snapshot(tracks)
        mock_engine.set_tracks.assert_not_called()

        backend._on_native_audio_sink_ready(True)
        self.assertTrue(backend._native_audio_active)
        mock_engine.set_tracks.assert_called_once_with(tracks, [])

    def test_set_source_resets_native_audio_engine(self):
        """Verify setSource stops and resets native audio engine on media change and teardown."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._last_tracks_snapshot = ([{"id": "t1"}], [])
        backend._player = MagicMock()
        backend._original_player = MagicMock()
        backend._dubbed_player = MagicMock()
        backend._original_loaded_path = ""
        backend._dubbed_loaded_path = ""
        backend.stop = MagicMock()
        backend.clear_subtitle = MagicMock()
        backend._apply_blur_filter = MagicMock()
        backend._apply_current_subtitle = MagicMock()

        # Teardown / clear source
        backend.setSource("")
        mock_engine.stop.assert_called_once()
        mock_engine.seek.assert_called_once_with(0)
        mock_engine.set_tracks.assert_called_once_with([])
        self.assertIsNone(backend._last_tracks_snapshot)

        # Load new source
        mock_engine.reset_mock()
        backend._last_tracks_snapshot = ([{"id": "t1"}], [])
        backend._normalize_source = lambda s: "sample.mp4"
        backend.durationChanged = MagicMock()
        backend.setSource("sample.mp4")
        mock_engine.stop.assert_called_once()
        mock_engine.seek.assert_called_once_with(0)
        mock_engine.set_tracks.assert_called_once_with([])
        self.assertIsNone(backend._last_tracks_snapshot)

    def test_on_native_audio_position_changed_emits_position_changed(self):
        """Verify _on_native_audio_position_changed updates position and emits positionChanged."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = True
        backend._video_time_warps = []
        backend.positionChanged = MagicMock()

        backend._on_native_audio_position_changed(1500)
        self.assertEqual(backend._dubbed_position_ms, 1500)
        self.assertEqual(backend._position_ms, 1500)
        backend.positionChanged.emit.assert_called_once_with(1500)

    def test_freeze_timer_tick_uses_native_audio_clock(self):
        """Verify freeze timer uses media_player.timeline_position_ms() when native audio is active."""
        from ui.utils.media_utils import _on_freeze_timer_tick

        gui = MagicMock()
        gui._active_freeze_warp = {"id": "warp_1"}
        gui.timeline._playing = True
        gui.media_player._native_audio_active = True
        gui.media_player.timeline_position_ms.return_value = 2500  # 2.5s
        gui._freeze_anchor_tl_s = 2.0  # anchor at 2.0s
        gui._freeze_duration_s = 1.0  # duration 1.0s (ends at 3.0s)
        gui.video_time_warps = []
        gui.media_player.duration.return_value = 10000

        # At 2.5s, elapsed is 0.5s < 1.0s
        _on_freeze_timer_tick(gui)
        gui.timeline.set_position.assert_called_with(2500)

    def test_terminate_workers_calls_media_player_close(self):
        """Verify _terminate_workers calls media_player.close() to release mpv and timers."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._active_workers = {}
        gui.media_player = MagicMock()

        VideoTranslatorGUI._terminate_workers(gui)
        gui.media_player.close.assert_called_once()

    def test_set_audio_tracks_snapshot_preserves_empty_warps(self):
        """Verify set_audio_tracks_snapshot preserves an explicitly empty warps list."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._native_audio_active = True
        backend._video_time_warps = [{"start": 1.0, "end": 2.0, "speed": 0.5}]

        tracks = [{"id": "t1", "path": "test.wav"}]
        backend.set_audio_tracks_snapshot(tracks, warps=[])

        self.assertEqual(backend._last_tracks_snapshot, (tracks, []))
        mock_engine.set_tracks.assert_called_once_with(tracks, [])

    def test_close_stops_sidecars_and_native_engine(self):
        """Verify close() stops sidecar players and releases native engine."""
        from ui.utils.media_backend import MpvMediaPlayerBackend

        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine
        backend._native_audio_active = True
        backend._player = MagicMock()
        backend._original_player = MagicMock()
        backend._dubbed_player = MagicMock()
        backend._poll_timer = MagicMock()
        backend._poll_timer.isActive.return_value = True
        backend._sync_timer = MagicMock()
        backend._sync_timer.isActive.return_value = True

        backend.close()

        backend._poll_timer.stop.assert_called_once()
        backend._sync_timer.stop.assert_called_once()
        backend._original_player.stop.assert_called_once()
        backend._dubbed_player.stop.assert_called_once()
        mock_engine.close.assert_called_once()
        backend._player.terminate.assert_called_once()

    def test_real_thread_set_tracks_unloads_readers(self):
        """Verify PreviewAudioEngine unloads readers in real background QThread when setting empty tracks."""
        import tempfile
        import time
        import numpy as np
        import soundfile as sf
        from PySide6.QtWidgets import QApplication
        from ui.utils.preview_audio import PreviewAudioEngine

        app = QApplication.instance() or QApplication(sys.argv)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        wav_path = tmp.name

        try:
            sf.write(wav_path, np.zeros(1600, dtype=np.float32), 16000)
            engine = PreviewAudioEngine()

            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)

            engine.set_tracks([{"id": "t1", "path": wav_path, "start": 0.0, "end": 1.0}])
            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)
                if len(engine._worker._readers) == 1:
                    break
            self.assertEqual(len(engine._worker._readers), 1)

            # Clear tracks: should close AudioReader in real background thread
            engine.set_tracks([])
            for _ in range(30):
                app.processEvents()
                time.sleep(0.01)
                if len(engine._worker._readers) == 0:
                    break
            self.assertEqual(len(engine._worker._readers), 0)

            engine.close()
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def test_resolve_preview_mixed_audio_path_bypasses_wav_when_native_active(self):
        """Verify _resolve_preview_mixed_audio_path returns empty string and does not invoke mix_audio_tracks."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui.using_existing_audio_source.return_value = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._native_audio_enabled = True

        with patch("app.audio_mixer.mix_audio_tracks") as mock_mixer:
            path = VideoTranslatorGUI._resolve_preview_mixed_audio_path(gui)
            self.assertEqual(path, "")
            mock_mixer.assert_not_called()

    def test_resolve_timeline_audio_visualization_path_native_active(self):
        """Verify resolve_timeline_audio_visualization_path uses voice_only directly without dubbed mix resolution."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui.using_existing_audio_source.return_value = False
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._native_audio_enabled = True
        gui._resolve_preview_voice_only_audio_path.return_value = "C:/fake/voice.wav"

        with patch("os.path.exists", return_value=True), \
             patch.object(VideoTranslatorGUI, "_resolve_preview_dubbed_playback_source") as mock_dubbed:
            path = VideoTranslatorGUI.resolve_timeline_audio_visualization_path(gui)
            self.assertEqual(path, "C:/fake/voice.wav")
            mock_dubbed.assert_not_called()

    def test_is_active_timeline_audio_track_muted_native_active(self):
        """Verify _is_active_timeline_audio_track_muted returns a2_muted directly without invoking dubbed mix resolution."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_audio_track_mode = "dubbed"
        gui.media_player = MagicMock()
        gui.media_player._native_audio_active = True
        gui._native_audio_enabled = True
        gui._timeline_audio_track_mutes.return_value = (False, True)

        with patch.object(VideoTranslatorGUI, "_resolve_preview_dubbed_playback_source") as mock_dubbed:
            is_muted = VideoTranslatorGUI._is_active_timeline_audio_track_muted(gui)
            self.assertTrue(is_muted)
            mock_dubbed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
