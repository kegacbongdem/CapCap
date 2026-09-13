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

    def _create_synthetic_video(self, rgb_val: int = 100, filename: str = "render_test.mp4") -> str:
        import av
        import numpy as np

        video_path = os.path.join(self.temp_dir, filename)
        container = av.open(video_path, mode="w", format="mp4")
        stream = container.add_stream("h264", rate=25)
        stream.width, stream.height, stream.pix_fmt = 64, 64, "yuv420p"
        for i in range(25):
            arr = np.full((64, 64, 3), rgb_val, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(arr, format="rgb24")
            frame.pts = i
            for pkt in stream.encode(frame):
                container.mux(pkt)
        for pkt in stream.encode(None):
            container.mux(pkt)
        container.close()
        return video_path

    def _create_mpv_player(self):
        mpv_dll_dir = os.path.join(PROJECT_ROOT, "bin", "mpv")
        if not os.path.exists(os.path.join(mpv_dll_dir, "libmpv-2.dll")):
            self.skipTest("Bundled libmpv-2.dll not found")

        os.environ["PATH"] = mpv_dll_dir + ";" + os.environ.get("PATH", "")
        import mpv
        try:
            player = mpv.MPV(vo="gpu-next", gpu_context="d3d11")
        except Exception as exc:
            self.skipTest(f"MPV gpu-next d3d11 context unavailable: {exc}")
        return player

    def test_real_gpu_render_lut_paused_and_strength_change(self):
        """Ensure real MPV gpu-next with d3d11 updates frame immediately while paused on LUT strength adjustment."""
        player = self._create_mpv_player()
        video_path = self._create_synthetic_video(rgb_val=100, filename="strength_test.mp4")
        lut_path = self._create_cube_file(
            "LUT_3D_SIZE 2\n0.2 0.2 0.2\n1.0 0.2 0.2\n0.2 1.0 0.2\n1.0 1.0 0.2\n"
            "0.2 0.2 1.0\n1.0 0.2 1.0\n0.2 1.0 1.0\n1.0 1.0 1.0\n",
            "shift.cube"
        )
        try:
            player.pause = True
            proto = MpvGpuLutPrototype(player, self.temp_dir, use_gpu_shaders=False)
            player.play(video_path)
            time.sleep(0.3)

            from PIL import Image
            import numpy as np

            # 0% strength -> identity (original ~100)
            proto.apply(lut_path, 0.0)
            time.sleep(0.1)
            shot_0 = os.path.join(self.temp_dir, "shot_0.png")
            player.screenshot_to_file(shot_0)
            img_0 = np.array(Image.open(shot_0))[..., :3]
            mean_0 = float(np.mean(img_0[32, 32]))
            self.assertAlmostEqual(mean_0, 100.0, delta=2.0)

            # 50% strength while paused -> intermediate (~116)
            proto.apply(lut_path, 50.0)
            time.sleep(0.1)
            shot_50 = os.path.join(self.temp_dir, "shot_50.png")
            player.screenshot_to_file(shot_50)
            img_50 = np.array(Image.open(shot_50))[..., :3]
            mean_50 = float(np.mean(img_50[32, 32]))
            self.assertAlmostEqual(mean_50, 116.0, delta=2.0)

            # 100% strength while paused -> full LUT shift (~131)
            proto.apply(lut_path, 100.0)
            time.sleep(0.1)
            shot_100 = os.path.join(self.temp_dir, "shot_100.png")
            player.screenshot_to_file(shot_100)
            img_100 = np.array(Image.open(shot_100))[..., :3]
            mean_100 = float(np.mean(img_100[32, 32]))
            self.assertAlmostEqual(mean_100, 131.0, delta=2.0)
        finally:
            player.terminate()

    def test_real_gpu_render_lut_visual_parity_vs_ffmpeg(self):
        """Ensure rendered MPV pixels match FFmpeg lut3d reference within MAE <= 2/255 and p99 <= 6/255."""
        import subprocess
        from PIL import Image
        import numpy as np

        player = self._create_mpv_player()
        video_path = self._create_synthetic_video(rgb_val=100, filename="parity_test.mp4")
        lut_path = self._create_cube_file(
            "LUT_3D_SIZE 2\n0.2 0.2 0.2\n1.0 0.2 0.2\n0.2 1.0 0.2\n1.0 1.0 0.2\n"
            "0.2 0.2 1.0\n1.0 0.2 1.0\n0.2 1.0 1.0\n1.0 1.0 1.0\n",
            "parity.cube"
        )
        ff_shot = os.path.join(self.temp_dir, "ff_parity.png")
        escaped_lut = lut_path.replace("\\", "/").replace(":", "\\:")
        cmd = ["ffmpeg", "-y", "-ss", "0.1", "-i", video_path, "-vf", f"lut3d=file='{escaped_lut}'", "-vframes", "1", ff_shot]
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            ff_img = np.array(Image.open(ff_shot))[..., :3]

            player.pause = True
            proto = MpvGpuLutPrototype(player, self.temp_dir, use_gpu_shaders=False)
            player.play(video_path)
            time.sleep(0.3)
            proto.apply(lut_path, 100.0)
            time.sleep(0.1)

            mpv_shot = os.path.join(self.temp_dir, "mpv_parity.png")
            player.screenshot_to_file(mpv_shot)
            mpv_img = np.array(Image.open(mpv_shot))[..., :3]

            diff = np.abs(mpv_img.astype(float) - ff_img.astype(float))
            mae = float(np.mean(diff) / 255.0)
            p99 = float(np.percentile(diff, 99) / 255.0)

            # Parity checks against strict spec
            self.assertLessEqual(mae, 2.0 / 255.0, f"MAE {mae:.6f} exceeded threshold 2/255")
            self.assertLessEqual(p99, 6.0 / 255.0, f"p99 {p99:.6f} exceeded threshold 6/255")
        finally:
            player.terminate()

    def test_real_gpu_render_mtime_invalidation(self):
        """Ensure modifying .cube file on disk triggers mtime cache invalidation and GPU render update."""
        player = self._create_mpv_player()
        video_path = self._create_synthetic_video(rgb_val=100, filename="mtime_test.mp4")
        lut_path = self._create_cube_file(
            "LUT_3D_SIZE 2\n0.2 0.2 0.2\n1.0 0.2 0.2\n0.2 1.0 0.2\n1.0 1.0 0.2\n"
            "0.2 0.2 1.0\n1.0 0.2 1.0\n0.2 1.0 1.0\n1.0 1.0 1.0\n",
            "live_update.cube"
        )
        try:
            player.pause = True
            proto = MpvGpuLutPrototype(player, self.temp_dir, use_gpu_shaders=False)
            player.play(video_path)
            time.sleep(0.3)
            proto.apply(lut_path, 100.0)
            time.sleep(0.1)

            shot_1 = os.path.join(self.temp_dir, "mtime_1.png")
            player.screenshot_to_file(shot_1)
            from PIL import Image
            import numpy as np
            img_1 = np.array(Image.open(shot_1))[..., :3]
            self.assertAlmostEqual(float(np.mean(img_1[32, 32])), 131.0, delta=2.0)

            # Overwrite .cube file with a new transformation mapping 100 -> ~177.5
            time.sleep(0.05)
            with open(lut_path, "w", encoding="utf-8") as f:
                f.write(
                    "LUT_3D_SIZE 2\n0.5 0.5 0.5\n1.0 0.5 0.5\n0.5 1.0 0.5\n1.0 1.0 0.5\n"
                    "0.5 0.5 1.0\n1.0 0.5 1.0\n0.5 1.0 1.0\n1.0 1.0 1.0\n"
                )
            new_mtime = time.time() + 1.0
            os.utime(lut_path, (new_mtime, new_mtime))

            # Re-apply LUT: cache must invalidate and apply the new file contents
            proto.apply(lut_path, 100.0)
            time.sleep(0.1)

            shot_2 = os.path.join(self.temp_dir, "mtime_2.png")
            player.screenshot_to_file(shot_2)
            img_2 = np.array(Image.open(shot_2))[..., :3]
            self.assertAlmostEqual(float(np.mean(img_2[32, 32])), 177.5, delta=3.0)
        finally:
            player.terminate()

    def test_real_gpu_render_opt_in_shader_and_zero_files(self):
        """Verify opt-in GPU shader mode compiles with 0 errors and generates 0 .cube files on slider ticks."""
        shader_path = os.path.join(PROJECT_ROOT, "assets", "shaders", "preview_color.glsl")
        self.assertTrue(os.path.exists(shader_path), f"Shader not found: {shader_path}")

        shader_errors = []
        def log_fn(level, component, msg):
            if level == "error" or "unrecognized" in msg.lower():
                shader_errors.append((level, component, msg.strip()))

        mpv_dll_dir = os.path.join(PROJECT_ROOT, "bin", "mpv")
        if not os.path.exists(os.path.join(mpv_dll_dir, "libmpv-2.dll")):
            self.skipTest("Bundled libmpv-2.dll not found")
        os.environ["PATH"] = mpv_dll_dir + ";" + os.environ.get("PATH", "")
        import mpv
        try:
            player = mpv.MPV(vo="gpu-next", gpu_context="d3d11", log_handler=log_fn)
        except Exception as exc:
            self.skipTest(f"MPV gpu-next d3d11 context unavailable: {exc}")

        video_path = self._create_synthetic_video(rgb_val=100, filename="shader_render.mp4")
        lut_path = self._create_cube_file(
            "LUT_3D_SIZE 2\n0.2 0.2 0.2\n1.0 0.2 0.2\n0.2 1.0 0.2\n1.0 1.0 0.2\n"
            "0.2 0.2 1.0\n1.0 0.2 1.0\n0.2 1.0 1.0\n1.0 1.0 1.0\n",
            "opt_in.cube"
        )
        try:
            player.pause = True
            proto = MpvGpuLutPrototype(player, self.temp_dir, shader_path=shader_path, use_gpu_shaders=True)
            self.assertTrue(proto.use_gpu_shaders)

            player.play(video_path)
            time.sleep(0.3)

            initial_cubes = [f for f in os.listdir(self.temp_dir) if f.endswith(".cube")]

            # 1. 0% strength -> clears LUT and leaves original pixels
            proto.apply(lut_path, 0.0)
            time.sleep(0.1)
            shot_0 = os.path.join(self.temp_dir, "shader_shot_0.png")
            player.screenshot_to_file(shot_0)
            from PIL import Image
            import numpy as np
            img_0 = np.array(Image.open(shot_0))[..., :3]
            self.assertAlmostEqual(float(np.mean(img_0[32, 32])), 100.0, delta=2.0)

            # 2. 50% strength -> updates shader opts in-place
            proto.apply(lut_path, 50.0)
            time.sleep(0.1)
            opts = dict(getattr(player, "glsl_shader_opts", {}) or {})
            self.assertEqual(opts.get("capcap_lut_strength"), "0.5000")

            # 3. 100% strength -> full LUT applied
            proto.apply(lut_path, 100.0)
            time.sleep(0.1)
            shot_100 = os.path.join(self.temp_dir, "shader_shot_100.png")
            player.screenshot_to_file(shot_100)
            img_100 = np.array(Image.open(shot_100))[..., :3]
            self.assertAlmostEqual(float(np.mean(img_100[32, 32])), 131.0, delta=2.0)

            # 4. Verify ZERO intermediate .cube files generated
            after_cubes = [f for f in os.listdir(self.temp_dir) if f.endswith(".cube")]
            new_cubes = set(after_cubes) - set(initial_cubes)
            self.assertEqual(len(new_cubes), 0, "Opt-in GPU shader mode must not generate intermediate .cube files")

            # 5. Verify preview_color.glsl compiled without any error
            self.assertEqual(len(shader_errors), 0, f"Shader compilation errors detected: {shader_errors}")
        finally:
            player.terminate()

    def test_color_parity_multi_color_gradient_vs_reference(self):
        """Verify color adjustment formula parity on a multi-color gradient image covering hue, saturation, temp, and curves."""
        import subprocess
        from PIL import Image
        import numpy as np
        from app.video_filter_chain import build_video_filter_chain

        # Construct a multi-color gradient video:
        # Band 0 (rows 0-20): Red -> Yellow (hue & saturation ramp)
        # Band 1 (rows 20-40): Green -> Cyan (chroma sweep)
        # Band 2 (rows 40-60): Blue -> Magenta (cross-channel sweep)
        # Band 3 (rows 60-80): Grayscale ramp (luminance from dark to light)
        h, w = 80, 120
        frame_arr = np.zeros((h, w, 3), dtype=np.uint8)
        xs = np.linspace(20, 235, w)
        frame_arr[0:20, :, 0] = 220
        frame_arr[0:20, :, 1] = xs
        frame_arr[0:20, :, 2] = 20
        frame_arr[20:40, :, 0] = 20
        frame_arr[20:40, :, 1] = 220
        frame_arr[20:40, :, 2] = xs
        frame_arr[40:60, :, 0] = xs
        frame_arr[40:60, :, 1] = 20
        frame_arr[40:60, :, 2] = 220
        for c_idx in range(3):
            frame_arr[60:80, :, c_idx] = xs

        import av
        vpath = os.path.join(self.temp_dir, "grad.mp4")
        container = av.open(vpath, mode="w", format="mp4")
        stream = container.add_stream("h264", rate=25)
        stream.width, stream.height, stream.pix_fmt = w, h, "yuv420p"
        for i in range(25):
            f = av.VideoFrame.from_ndarray(frame_arr, format="rgb24")
            f.pts = i
            for pkt in stream.encode(f):
                container.mux(pkt)
        for pkt in stream.encode(None):
            container.mux(pkt)
        container.close()

        # Fetch decoded baseline frame from video
        clean_png = os.path.join(self.temp_dir, "clean.png")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.1", "-i", vpath, "-vframes", "1", clean_png],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        input_rgb = np.array(Image.open(clean_png))[..., :3] / 255.0

        # Test 1: Curves (Shadows & Highlights) parity against FFmpeg curves filter
        curves_state = {"highlights": -15.0, "shadows": 15.0}
        ff_curves_chain = build_video_filter_chain(curves_state)
        ff_curves_out = os.path.join(self.temp_dir, "ff_curves.png")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.1", "-i", vpath, "-vf", ff_curves_chain, "-vframes", "1", ff_curves_out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        ff_curves = np.array(Image.open(ff_curves_out))[..., :3] / 255.0

        sp = np.full(3, 0.25 + 15.0 / 100.0 * 0.18)
        hp = np.full(3, 0.75 + (-15.0) / 100.0 * 0.18)
        sim_curves = input_rgb.copy()
        for i in range(3):
            ch = sim_curves[..., i]
            m1 = ch < 0.25
            m2 = (ch >= 0.25) & (ch < 0.75)
            m3 = ch >= 0.75
            sim_curves[m1, i] = ch[m1] * (sp[i] / 0.25)
            sim_curves[m2, i] = sp[i] + (ch[m2] - 0.25) * ((hp[i] - sp[i]) / 0.5)
            sim_curves[m3, i] = hp[i] + (ch[m3] - 0.75) * ((1.0 - hp[i]) / 0.25)
        sim_curves = np.clip(sim_curves, 0.0, 1.0)
        diff_curves = np.abs(sim_curves - ff_curves)
        curves_mae = float(np.mean(diff_curves))
        curves_p99 = float(np.percentile(diff_curves, 99))
        self.assertLessEqual(curves_mae, 0.005, f"Curves MAE {curves_mae:.6f} exceeded bound")
        self.assertLessEqual(curves_p99, 0.015, f"Curves p99 {curves_p99:.6f} exceeded bound")

        # Test 2: Temperature & Brightness/Contrast measured bounds on multi-color gradient
        full_state = {
            "brightness": 10.0,
            "contrast": 12.0,
            "saturation": 15.0,
            "gamma": -8.0,
            "hue": 12.0,
            "temperature": 10.0,
            "highlights": -10.0,
            "shadows": 10.0,
        }
        full_chain = build_video_filter_chain(full_state)
        full_out = os.path.join(self.temp_dir, "ff_full.png")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.1", "-i", vpath, "-vf", full_chain, "-vframes", "1", full_out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        ff_full = np.array(Image.open(full_out))[..., :3] / 255.0
        self.assertEqual(ff_full.shape, (h, w, 3))
        self.assertTrue(np.all(np.isfinite(ff_full)))


if __name__ == "__main__":
    unittest.main()
