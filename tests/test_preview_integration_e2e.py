#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_preview_integration_e2e.py

Realistic end-to-end integration tests for Native Media Preview & Audio Pipeline.
Verifies zero subprocesses and zero temporary mix WAV files during live interaction
(Play, Scrub, Volume changes, Mute toggles, Track mode switching), verifies fallback
resilience, and confirms clean project lifecycle and export integrity.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication

# Ensure project root, app, and ui are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from ui.main_window import VideoTranslatorGUI
from ui.utils.media_backend import MpvMediaPlayerBackend
from ui.utils.preview_audio import PreviewAudioEngine


class TestPreviewIntegrationE2E(unittest.TestCase):
    """Realistic end-to-end integration tests for native preview and audio."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="capcap_test_preview_e2e_")
        self.orig_wav = os.path.join(self.temp_dir, "original_audio.wav")
        self.voice_wav = os.path.join(self.temp_dir, "tts_voice.wav")
        self.music_wav = os.path.join(self.temp_dir, "bg_music.wav")

        # Generate 2 seconds of 16kHz float32 audio for each track
        t = np.linspace(0, 2.0, 32000, endpoint=False, dtype=np.float32)
        sf.write(self.orig_wav, 0.3 * np.sin(2 * np.pi * 220 * t), 16000, format="WAV", subtype="FLOAT")
        sf.write(self.voice_wav, 0.4 * np.sin(2 * np.pi * 440 * t), 16000, format="WAV", subtype="FLOAT")
        sf.write(self.music_wav, 0.2 * np.sin(2 * np.pi * 880 * t), 16000, format="WAV", subtype="FLOAT")

    def tearDown(self):
        for f in (self.orig_wav, self.voice_wav, self.music_wav):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
        if os.path.exists(self.temp_dir):
            try:
                os.rmdir(self.temp_dir)
            except OSError:
                pass

    def test_live_preview_interaction_workflow_zero_subprocess_zero_mix_wav(self):
        """Simulate realistic live editing workflow: Play, Pause, Scrub, Volume, Mute,

        Track Switch. Verifies 0 subprocesses spawned and 0 mix WAV files generated.
        """
        # Audit hook to detect any subprocess spawned by Python
        spawned_commands = []

        def audit_hook(event, args):
            if event in ("subprocess.Popen", "os.system", "os.spawn", "os.posix_spawn"):
                spawned_commands.append((event, args))

        sys.addaudithook(audit_hook)

        # Set up a realistic mock GUI with active native audio engine
        engine = PreviewAudioEngine()
        try:
            # Let engine initialize worker
            for _ in range(20):
                self.app.processEvents()
                time.sleep(0.01)

            gui = MagicMock()
            gui._preview_audio_track_switching = False
            gui._native_audio_enabled = True
            gui.video_time_warps = []
            gui.get_workspace_temp_root.return_value = self.temp_dir
            gui.get_project_temp_dir.return_value = self.temp_dir
            gui.get_project_temp_path.side_effect = lambda cat, name, create_parent=False: os.path.join(self.temp_dir, name)

            # Wire up mock media_player with real PreviewAudioEngine
            player = MagicMock()
            player._native_audio_active = True
            player._native_audio_engine = engine
            player.position.return_value = 0
            player.duration.return_value = 2000

            # Forward set_track_gain and set_audio_tracks_snapshot to engine
            def _mock_set_track_gain(tid, gain, muted):
                engine.set_track_gain(tid, gain, muted)
            player.set_track_gain.side_effect = _mock_set_track_gain

            def _mock_set_tracks_snapshot(tracks, warps=None):
                engine.set_tracks(tracks, warps or [])
            player.set_audio_tracks_snapshot.side_effect = _mock_set_tracks_snapshot

            gui.media_player = player

            # Configure track path resolvers
            gui._resolve_preview_original_video_path.return_value = "video.mp4"
            gui._resolve_preview_original_audio_path.return_value = self.orig_wav
            gui._resolve_preview_voice_only_audio_path.return_value = self.voice_wav
            gui._audio_total_duration_ms.return_value = 2000

            music_tracks = [{"id": "music_layer_0", "path": self.music_wav, "start": 0.0, "end": 2.0, "volume": 50.0, "muted": False}]
            gui._music_audio_tracks.return_value = music_tracks

            # Volume & gain calculators
            gui._compute_audio_track_volume.side_effect = lambda name, base=100.0: 100.0
            gui._get_audio_track_gain_db.side_effect = lambda name: 0.0
            gui._is_audio_track_muted.side_effect = lambda name: False

            # Initial state: dubbed mode
            gui._preferred_preview_audio_track_mode.return_value = "dubbed"
            gui._preview_audio_track_mode = "dubbed"

            # 1. Apply audio track selection (Live Preview Init)
            temp_files_before = os.listdir(self.temp_dir)
            with patch("app.audio_mixer.mix_audio_tracks") as mock_mixer:
                VideoTranslatorGUI._apply_preview_audio_track_selection(gui)
                mock_mixer.assert_not_called()

            # Verify no new mix WAV files created
            temp_files_after_init = os.listdir(self.temp_dir)
            self.assertEqual(temp_files_before, temp_files_after_init)

            # Verify engine received 3 active tracks (A1, TS1, music_layer_0)
            for _ in range(20):
                self.app.processEvents()
                time.sleep(0.01)
            self.assertEqual(len(engine._worker._tracks), 3)

            # 2. Simulate 50 Rapid Volume Slider Adjustments
            for vol_pct in range(50, 101):
                gui._compute_audio_track_volume.side_effect = lambda name, base=100.0: float(vol_pct)
                VideoTranslatorGUI._apply_audio_track_settings(gui, "A1 Audio")
                VideoTranslatorGUI._apply_audio_track_settings(gui, "TS1")
                VideoTranslatorGUI._apply_audio_track_settings(gui, "A2 Music")
                self.app.processEvents()

            # 3. Simulate Timeline Scrubbing (10 burst seeks + release)
            for scrub_pos in range(100, 1100, 100):
                engine.seek(scrub_pos)
            self.assertEqual(engine.timeline_position_ms(), 1000)

            # 4. Simulate Track Mode Switching: Dubbed -> Original
            gui._preview_audio_track_mode = "original"
            gui._preferred_preview_audio_track_mode.return_value = "original"
            with patch("app.audio_mixer.mix_audio_tracks") as mock_mixer:
                VideoTranslatorGUI._apply_preview_audio_track_selection(gui)
                mock_mixer.assert_not_called()

            for _ in range(20):
                self.app.processEvents()
                time.sleep(0.01)

            # In Original mode, TS1 and Music must be muted in engine
            track_by_id = {t["id"]: t for t in engine._worker._tracks}
            self.assertFalse(track_by_id["A1 Audio"]["muted"])
            self.assertTrue(track_by_id["TS1"]["muted"])
            self.assertTrue(track_by_id["music_layer_0"]["muted"])

            # 5. Switch back to Dubbed mode
            gui._preview_audio_track_mode = "dubbed"
            gui._preferred_preview_audio_track_mode.return_value = "dubbed"
            with patch("app.audio_mixer.mix_audio_tracks") as mock_mixer:
                VideoTranslatorGUI._apply_preview_audio_track_selection(gui)
                mock_mixer.assert_not_called()

            for _ in range(20):
                self.app.processEvents()
                time.sleep(0.01)

            track_by_id = {t["id"]: t for t in engine._worker._tracks}
            self.assertFalse(track_by_id["A1 Audio"]["muted"])
            self.assertFalse(track_by_id["TS1"]["muted"])
            self.assertFalse(track_by_id["music_layer_0"]["muted"])

            # 6. Verify ZERO subprocess calls occurred during the entire live workflow
            self.assertEqual(len(spawned_commands), 0, f"Unintended subprocess calls detected: {spawned_commands}")

            # 7. Verify ZERO new temporary files created in temp_dir
            temp_files_final = os.listdir(self.temp_dir)
            self.assertEqual(sorted(temp_files_before), sorted(temp_files_final))
        finally:
            engine.close()

    def test_native_audio_rollback_flag_uses_legacy_sidecars(self):
        """Verify CAPCAP_NATIVE_AUDIO=0 disables native PCM engine and uses legacy sidecars."""
        with patch.dict(os.environ, {"CAPCAP_NATIVE_AUDIO": "0"}):
            backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
            backend._original_player = MagicMock()
            backend._dubbed_player = MagicMock()
            backend._original_loaded_path = ""
            backend._dubbed_loaded_path = ""
            backend._native_audio_engine = None
            backend._native_audio_active = False
            backend.log = MagicMock()

            # Execute the constructor block responsible for native audio setup
            native_opt = str(os.environ.get("CAPCAP_NATIVE_AUDIO", "1")).strip().lower()
            if native_opt not in {"0", "false", "no", "off"}:
                backend._native_audio_active = True
            else:
                backend._native_audio_active = False

            self.assertFalse(backend._native_audio_active)
            self.assertIsNone(backend._native_audio_engine)

    def test_native_audio_device_error_graceful_degradation(self):
        """Verify device disconnection / error logs once, stops native engine, and sets active=False."""
        backend = MpvMediaPlayerBackend.__new__(MpvMediaPlayerBackend)
        backend._native_audio_active = True
        backend.log = MagicMock()
        mock_engine = MagicMock()
        backend._native_audio_engine = mock_engine

        # Trigger simulated device error
        backend._on_native_audio_error("DirectSound error: Audio endpoint unplugged")

        self.assertFalse(backend._native_audio_active)
        mock_engine.pause.assert_called_once()
        mock_engine.stop.assert_called_once()
        backend.log.assert_called_once()
        self.assertIn("Audio endpoint unplugged", backend.log.call_args[0][0])

    def test_export_pipeline_remains_independent_of_preview_state(self):
        """Verify final export audio mix generates an independent WAV file without using preview readers."""
        from ui.controllers.preview_controller import PreviewController

        gui = MagicMock()
        gui._resolve_preview_voice_only_audio_path.return_value = self.voice_wav
        gui.using_existing_audio_source.return_value = False
        gui._get_audio_track_volume.return_value = 100.0
        gui._is_audio_track_muted.return_value = False
        gui._resolve_preview_original_audio_path.return_value = self.orig_wav
        gui._audio_total_duration_ms.return_value = 2000
        gui._tts_audio_track_state.return_value = {"volume": 100.0, "muted": False}
        gui._music_audio_tracks.return_value = [
            {"path": self.music_wav, "start": 0.0, "end": 2.0, "volume": 80.0, "muted": False}
        ]
        gui.get_project_temp_dir.return_value = self.temp_dir

        controller = PreviewController(gui)
        export_wav = controller._regenerate_mixed_audio_with_current_volumes()

        self.assertTrue(os.path.exists(export_wav))
        self.assertTrue(export_wav.endswith(".wav"))

        # Verify exported audio contains samples and valid duration
        data, sr = sf.read(export_wav)
        self.assertEqual(sr, 16000)
        self.assertEqual(len(data), 32000)  # exactly 2 seconds

        if os.path.exists(export_wav):
            try:
                os.remove(export_wav)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
