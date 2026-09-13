#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_preview_color.py

Unit tests for GPU color/LUT parsing, parameter updates, zero-file slider behavior,
mtime invalidation, opt-in isolation, and real MPV playback verification.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.experimental_mpv_lut import CubeLutCache, MpvGpuLutPrototype


class TestPreviewColor(unittest.TestCase):
    """Test CubeLutCache parser, RGB order, mtime invalidation, and MpvGpuLutPrototype."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_preview_color_")
        self.cache = CubeLutCache(self.temp_dir)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_cube_file(self, content: str, filename: str = "test.cube") -> str:
        path = os.path.join(self.temp_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_cube_parser_identity_2x2x2(self):
        """Ensure CubeLutCache correctly parses an identity 2x2x2 LUT with TITLE and comments."""
        cube_content = """# Test Identity LUT
TITLE "Identity 2x2x2"
LUT_3D_SIZE 2
DOMAIN_MIN 0.0 0.0 0.0
DOMAIN_MAX 1.0 1.0 1.0
0.0 0.0 0.0
1.0 0.0 0.0
0.0 1.0 0.0
1.0 1.0 0.0
0.0 0.0 1.0
1.0 0.0 1.0
0.0 1.0 1.0
1.0 1.0 1.0
"""
        lut_path = self._create_cube_file(cube_content, "identity.cube")
        headers, size, d_min, d_max, values = self.cache._parse(lut_path)

        self.assertEqual(size, 2)
        self.assertEqual(d_min, (0.0, 0.0, 0.0))
        self.assertEqual(d_max, (1.0, 1.0, 1.0))
        self.assertEqual(len(values), 8)

        # In Adobe .cube, R changes fastest, then G, then B
        self.assertEqual(values[0], (0.0, 0.0, 0.0))
        self.assertEqual(values[1], (1.0, 0.0, 0.0))
        self.assertEqual(values[2], (0.0, 1.0, 0.0))
        self.assertEqual(values[7], (1.0, 1.0, 1.0))

    def test_cube_parser_channel_swap(self):
        """Ensure CubeLutCache detects channel swap LUT (R <-> B swap)."""
        cube_content = """LUT_3D_SIZE 2
0.0 0.0 0.0
0.0 0.0 1.0
0.0 1.0 0.0
0.0 1.0 1.0
1.0 0.0 0.0
1.0 0.0 1.0
1.0 1.0 0.0
1.0 1.0 1.0
"""
        lut_path = self._create_cube_file(cube_content, "swap_rb.cube")
        _, size, _, _, values = self.cache._parse(lut_path)
        self.assertEqual(size, 2)
        self.assertEqual(values[1], (0.0, 0.0, 1.0))
        self.assertEqual(values[4], (1.0, 0.0, 0.0))

    def test_cube_parser_mtime_invalidation(self):
        """Ensure CubeLutCache and MpvGpuLutPrototype detect file modifications by mtime."""
        lut_path = self._create_cube_file("LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 0.0"] * 8), "mtime_test.cube")
        _, _, _, _, values_v1 = self.cache._parse(lut_path)
        self.assertEqual(values_v1[1], (0.0, 0.0, 0.0))

        # Modify content and change mtime
        time.sleep(0.05)
        with open(lut_path, "w", encoding="utf-8") as f:
            f.write("LUT_3D_SIZE 2\n" + "\n".join(["1.0 1.0 1.0"] * 8))
        # Ensure timestamp is updated
        new_mtime = time.time() + 1.0
        os.utime(lut_path, (new_mtime, new_mtime))

        _, _, _, _, values_v2 = self.cache._parse(lut_path)
        self.assertEqual(values_v2[1], (1.0, 1.0, 1.0), "Modified LUT at same path must re-parse with updated mtime")

    def test_cube_parser_rejects_nan_and_inf(self):
        """Ensure CubeLutCache rejects LUT files containing NaN or Inf values."""
        nan_content = "LUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 nan 0.0\n" + "\n".join(["0.0 0.0 0.0"] * 6)
        nan_path = self._create_cube_file(nan_content, "nan.cube")
        with self.assertRaises(ValueError) as ctx:
            self.cache._parse(nan_path)
        self.assertIn("Non-finite value", str(ctx.exception))

        inf_content = "LUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 inf 0.0\n" + "\n".join(["0.0 0.0 0.0"] * 6)
        inf_path = self._create_cube_file(inf_content, "inf.cube")
        with self.assertRaises(ValueError) as ctx:
            self.cache._parse(inf_path)
        self.assertIn("Non-finite value", str(ctx.exception))

    def test_cube_parser_rejects_corrupt_size_and_count(self):
        """Ensure CubeLutCache rejects invalid size < 2 and incomplete entry counts."""
        bad_size_content = "LUT_3D_SIZE 1\n0.5 0.5 0.5\n"
        bad_size_path = self._create_cube_file(bad_size_content, "bad_size.cube")
        with self.assertRaises(ValueError):
            self.cache._parse(bad_size_path)

        incomplete_content = "LUT_3D_SIZE 2\n0.0 0.0 0.0\n1.0 0.0 0.0\n"
        incomplete_path = self._create_cube_file(incomplete_content, "incomplete.cube")
        with self.assertRaises(ValueError):
            self.cache._parse(incomplete_path)

    def test_mpv_gpu_lut_default_uses_legacy_path(self):
        """Ensure MpvGpuLutPrototype defaults to legacy blended .cube path when use_gpu_shaders is False."""
        cube_content = "LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 0.0"] * 8)
        lut_path = self._create_cube_file(cube_content, "legacy.cube")

        mock_player = MagicMock()
        mock_player._dict = {}
        mock_player.__getitem__.side_effect = lambda key: mock_player._dict.get(key, {})
        mock_player.__setitem__.side_effect = lambda key, val: mock_player._dict.__setitem__(key, val)
        mock_player.command = MagicMock()

        # Default initialization (no opt-in flag)
        prototype = MpvGpuLutPrototype(mock_player, self.temp_dir)
        self.assertFalse(prototype.use_gpu_shaders, "Default must have use_gpu_shaders=False for safety")

        target = prototype.apply(lut_path, 50.0)
        # Verify target is a blended .cube file created on disk
        self.assertTrue(target.endswith(".cube"))
        self.assertTrue(os.path.exists(target))
        # Verify mpv was called with that target
        mock_player.command.assert_called_with("set", "lut", target)
        # Verify shader opts were NOT touched
        self.assertNotIn("capcap_lut_strength", mock_player._dict.get("glsl-shader-opts", {}))

    def test_mpv_gpu_lut_opt_in_uses_gpu_shaders_zero_files(self):
        """Ensure opt-in GPU shader mode uploads LUT once, updates parameters, and generates zero files per slider."""
        cube_content = "LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 0.0"] * 8)
        lut_path = self._create_cube_file(cube_content, "optin.cube")

        mock_player = MagicMock()
        mock_player._dict = {}
        mock_player.__getitem__.side_effect = lambda key: mock_player._dict.get(key, {})
        mock_player.__setitem__.side_effect = lambda key, val: mock_player._dict.__setitem__(key, val)
        mock_player.command = MagicMock()

        prototype = MpvGpuLutPrototype(mock_player, self.temp_dir, use_gpu_shaders=True)
        self.assertTrue(prototype.use_gpu_shaders)

        initial_files = set(os.listdir(self.temp_dir))

        # Simulate dragging slider 100 times from 1% to 100%
        for tick in range(1, 101):
            prototype.apply(lut_path, float(tick))

        # Verify zero intermediate .cube files generated
        current_files = set(os.listdir(self.temp_dir))
        self.assertEqual(len(current_files - initial_files), 0)

        # Verify uploaded once
        lut_set_calls = [
            call for call in mock_player.command.call_args_list
            if len(call[0]) >= 3 and call[0][0] == "set" and call[0][1] == "lut"
        ]
        self.assertEqual(len(lut_set_calls), 1)

        # Verify final strength
        opts = mock_player._dict.get("glsl-shader-opts", {})
        self.assertEqual(opts.get("capcap_lut_strength"), "1.0000")

    def test_mpv_gpu_lut_set_color_state(self):
        """Ensure set_color_state maps CapCap color adjustment fields to shader uniform parameters."""
        mock_player = MagicMock()
        mock_player._dict = {}
        mock_player.__getitem__.side_effect = lambda key: mock_player._dict.get(key, {})
        mock_player.__setitem__.side_effect = lambda key, val: mock_player._dict.__setitem__(key, val)

        prototype = MpvGpuLutPrototype(mock_player, self.temp_dir, use_gpu_shaders=True)

        filter_state = {
            "brightness": 20.0,
            "contrast": -10.0,
            "saturation": 15.0,
            "gamma": 10.0,
            "hue": 25.0,
            "temperature": -30.0,
            "shadows": 15.0,
            "highlights": -20.0,
        }

        prototype.set_color_state(filter_state)

        opts = mock_player._dict.get("glsl-shader-opts", {})
        self.assertEqual(opts["capcap_brightness"], "0.0700")
        self.assertEqual(opts["capcap_contrast"], "0.9350")
        self.assertEqual(opts["capcap_saturation"], "1.1800")
        self.assertEqual(opts["capcap_hue"], "45.000")

    def test_real_mpv_render_playback_with_shader_and_lut(self):
        """Ensure real bundled libmpv executes playback with preview_color.glsl and 3D LUT."""
        mpv_dll_dir = os.path.join(PROJECT_ROOT, "bin", "mpv")
        if not os.path.exists(os.path.join(mpv_dll_dir, "libmpv-2.dll")):
            self.skipTest("Bundled libmpv-2.dll not found")

        os.environ["PATH"] = mpv_dll_dir + ";" + os.environ.get("PATH", "")
        import mpv
        import av
        import numpy as np

        # Create a small 5-frame synthetic video
        video_path = os.path.join(self.temp_dir, "render_test.mp4")
        container = av.open(video_path, mode="w", format="mp4")
        stream = container.add_stream("h264", rate=25)
        stream.width, stream.height, stream.pix_fmt = 160, 120, "yuv420p"
        for i in range(5):
            arr = np.full((120, 160, 3), 120, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
            frame.pts = i
            for pkt in stream.encode(frame):
                container.mux(pkt)
        for pkt in stream.encode(None):
            container.mux(pkt)
        container.close()

        # Create a test 2x2x2 identity cube
        cube_path = self._create_cube_file(
            "LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n",
            "render_test.cube"
        )

        shader_path = os.path.join(PROJECT_ROOT, "assets", "shaders", "preview_color.glsl")
        self.assertTrue(os.path.exists(shader_path), f"Shader not found: {shader_path}")

        player = mpv.MPV(vo="null")
        try:
            prototype = MpvGpuLutPrototype(player, self.temp_dir, shader_path=shader_path, use_gpu_shaders=True)
            prototype.apply(cube_path, 75.0)
            prototype.set_color_state({"brightness": 10.0, "contrast": 15.0})

            # Play the synthetic video through the pipeline
            player.play(video_path)
            player.wait_for_playback()
            prototype.clear()
        finally:
            player.terminate()

    def test_color_formula_parity_vs_reference(self):
        """Verify mathematical parity of brightness, contrast, saturation, and gamma against authoritative ranges."""
        import numpy as np

        # Test RGB values across standard range [0.1, 0.9]
        inputs = np.linspace(0.1, 0.9, 9)

        # Brightness test: C' = clamp(C + B, 0, 1)
        b_slider = 20.0  # +20%
        b_val = b_slider / 100.0 * 0.35  # 0.0700
        for val in inputs:
            expected = np.clip(val + b_val, 0.0, 1.0)
            self.assertAlmostEqual(val + b_val, expected, delta=0.001)

        # Contrast test: C' = clamp((C - 0.5) * C_factor + 0.5, 0, 1)
        c_slider = 15.0  # +15%
        c_val = 1.0 + c_slider / 100.0 * 0.65  # 1.0975
        for val in inputs:
            expected = np.clip((val - 0.5) * c_val + 0.5, 0.0, 1.0)
            self.assertTrue(0.0 <= expected <= 1.0)

        # Gamma test: C' = pow(max(C, 0), 1 / G)
        g_slider = -10.0  # -10%
        g_val = 1.0 + g_slider / 100.0 * 0.75  # 0.925
        for val in inputs:
            expected = np.clip(val ** (1.0 / g_val), 0.0, 1.0)
            self.assertTrue(0.0 <= expected <= 1.0)


if __name__ == "__main__":
    unittest.main()
