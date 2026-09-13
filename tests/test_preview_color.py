#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_preview_color.py

Unit tests for GPU color/LUT parsing, parameter updates, and zero-file slider behavior.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
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
    """Test CubeLutCache parser, RGB order, validation, and MpvGpuLutPrototype."""

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
        # Index 0: (r=0, g=0, b=0) -> 0.0, 0.0, 0.0
        # Index 1: (r=1, g=0, b=0) -> 1.0, 0.0, 0.0
        # Index 2: (r=0, g=1, b=0) -> 0.0, 1.0, 0.0
        # Index 3: (r=1, g=1, b=0) -> 1.0, 1.0, 0.0
        # Index 4: (r=0, g=0, b=1) -> 0.0, 0.0, 1.0
        # Index 5: (r=1, g=0, b=1) -> 1.0, 0.0, 1.0
        # Index 6: (r=0, g=1, b=1) -> 0.0, 1.0, 1.0
        # Index 7: (r=1, g=1, b=1) -> 1.0, 1.0, 1.0
        self.assertEqual(values[0], (0.0, 0.0, 0.0))
        self.assertEqual(values[1], (1.0, 0.0, 0.0))
        self.assertEqual(values[2], (0.0, 1.0, 0.0))
        self.assertEqual(values[7], (1.0, 1.0, 1.0))

    def test_cube_parser_channel_swap(self):
        """Ensure CubeLutCache detects channel swap LUT (R <-> B swap)."""
        # In a Red <-> Blue swap LUT:
        # (r, g, b) maps to (b, g, r)
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
        # Entry 1 was (r=1, g=0, b=0) in input; output is (0.0, 0.0, 1.0)
        self.assertEqual(values[1], (0.0, 0.0, 1.0))
        # Entry 4 was (r=0, g=0, b=1) in input; output is (1.0, 0.0, 0.0)
        self.assertEqual(values[4], (1.0, 0.0, 0.0))

    def test_cube_parser_rejects_nan_and_inf(self):
        """Ensure CubeLutCache rejects LUT files containing NaN or Inf values."""
        nan_content = """LUT_3D_SIZE 2
0.0 0.0 0.0
1.0 nan 0.0
0.0 1.0 0.0
1.0 1.0 0.0
0.0 0.0 1.0
1.0 0.0 1.0
0.0 1.0 1.0
1.0 1.0 1.0
"""
        nan_path = self._create_cube_file(nan_content, "nan.cube")
        with self.assertRaises(ValueError) as ctx:
            self.cache._parse(nan_path)
        self.assertIn("Non-finite value", str(ctx.exception))

        inf_content = """LUT_3D_SIZE 2
0.0 0.0 0.0
1.0 inf 0.0
0.0 1.0 0.0
1.0 1.0 0.0
0.0 0.0 1.0
1.0 0.0 1.0
0.0 1.0 1.0
1.0 1.0 1.0
"""
        inf_path = self._create_cube_file(inf_content, "inf.cube")
        with self.assertRaises(ValueError) as ctx:
            self.cache._parse(inf_path)
        self.assertIn("Non-finite value", str(ctx.exception))

    def test_cube_parser_rejects_corrupt_size_and_count(self):
        """Ensure CubeLutCache rejects invalid size < 2 and incomplete entry counts."""
        bad_size_content = """LUT_3D_SIZE 1
0.5 0.5 0.5
"""
        bad_size_path = self._create_cube_file(bad_size_content, "bad_size.cube")
        with self.assertRaises(ValueError):
            self.cache._parse(bad_size_path)

        incomplete_content = """LUT_3D_SIZE 2
0.0 0.0 0.0
1.0 0.0 0.0
0.0 1.0 0.0
"""
        incomplete_path = self._create_cube_file(incomplete_content, "incomplete.cube")
        with self.assertRaises(ValueError):
            self.cache._parse(incomplete_path)

    def test_mpv_gpu_lut_zero_files_per_slider(self):
        """Ensure MpvGpuLutPrototype uploads LUT once and updates strength via shader parameter without writing files."""
        cube_content = """LUT_3D_SIZE 2
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

        mock_player = MagicMock()
        mock_player.__getitem__.side_effect = lambda key: mock_player._dict.get(key, {})
        mock_player._dict = {}
        def set_item(key, val):
            mock_player._dict[key] = val
        mock_player.__setitem__.side_effect = set_item
        mock_player.command = MagicMock()

        prototype = MpvGpuLutPrototype(mock_player, self.temp_dir)

        # Count files in temp_dir before slider movements
        initial_files = set(os.listdir(self.temp_dir))

        # Simulate dragging slider 100 times from 1% to 100%
        for tick in range(1, 101):
            prototype.apply(lut_path, float(tick))

        # Verify that NO new files were generated in cache_dir during 100 slider ticks!
        current_files = set(os.listdir(self.temp_dir))
        new_files = current_files - initial_files
        self.assertEqual(len(new_files), 0, f"Expected 0 new files, found: {new_files}")

        # Verify player.command('set', 'lut', ...) was called exactly ONCE (uploaded once)
        lut_set_calls = [
            call for call in mock_player.command.call_args_list
            if len(call[0]) >= 3 and call[0][0] == "set" and call[0][1] == "lut"
        ]
        self.assertEqual(len(lut_set_calls), 1)

        # Verify glsl-shader-opts has the final strength
        opts = mock_player._dict.get("glsl-shader-opts", {})
        self.assertEqual(opts.get("capcap_lut_strength"), "1.0000")

    def test_mpv_gpu_lut_set_color_state(self):
        """Ensure set_color_state maps CapCap color adjustment fields to shader uniform parameters."""
        mock_player = MagicMock()
        mock_player._dict = {}
        mock_player.__getitem__.side_effect = lambda key: mock_player._dict.get(key, {})
        mock_player.__setitem__.side_effect = lambda key, val: mock_player._dict.__setitem__(key, val)

        prototype = MpvGpuLutPrototype(mock_player, self.temp_dir)

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
        self.assertIn("capcap_brightness", opts)
        self.assertIn("capcap_contrast", opts)
        self.assertIn("capcap_saturation", opts)
        self.assertIn("capcap_gamma", opts)
        self.assertIn("capcap_hue", opts)
        self.assertIn("capcap_temp", opts)
        self.assertIn("capcap_shadow_point", opts)
        self.assertIn("capcap_highlight_point", opts)

        # Check mapped formula values
        # brightness = 20 / 100 * 0.35 = 0.0700
        self.assertEqual(opts["capcap_brightness"], "0.0700")
        # contrast = 1.0 + (-10) / 100 * 0.65 = 0.9350
        self.assertEqual(opts["capcap_contrast"], "0.9350")
        # saturation = 1.0 + 15 / 100 * 1.2 = 1.1800
        self.assertEqual(opts["capcap_saturation"], "1.1800")
        # hue = 25 * 1.8 = 45.000
        self.assertEqual(opts["capcap_hue"], "45.000")

    def test_mpv_gpu_lut_clear(self):
        """Ensure clear resets LUT property and strength parameter."""
        mock_player = MagicMock()
        mock_player._dict = {}
        mock_player.__getitem__.side_effect = lambda key: mock_player._dict.get(key, {})
        mock_player.__setitem__.side_effect = lambda key, val: mock_player._dict.__setitem__(key, val)
        mock_player.command = MagicMock()

        prototype = MpvGpuLutPrototype(mock_player, self.temp_dir)
        cube_content = "LUT_3D_SIZE 2\n" + "\n".join(["0.0 0.0 0.0"] * 8)
        lut_path = self._create_cube_file(cube_content, "dummy.cube")

        prototype.apply(lut_path, 80.0)
        self.assertEqual(mock_player._dict.get("glsl-shader-opts", {}).get("capcap_lut_strength"), "0.8000")

        prototype.clear()
        self.assertEqual(mock_player._dict.get("glsl-shader-opts", {}).get("capcap_lut_strength"), "0.0")
        # Ensure clear command was called
        mock_player.command.assert_any_call("set", "lut", "")


if __name__ == "__main__":
    unittest.main()
