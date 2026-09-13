"""GPU-native LUT and color adjustment support for MPV ``gpu-next``."""

from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class CubeLutCache:
    """Parser and validator for Adobe .cube 3D LUT files."""

    def __init__(self, cache_dir: str | os.PathLike):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._parsed: Dict[Tuple[str, float], Tuple[List[str], int, Tuple[float, float, float], Tuple[float, float, float], List[Tuple[float, float, float]]]] = {}

    def _parse(self, path: str):
        path = os.path.abspath(path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"LUT file does not exist: {path}")

        mtime = os.path.getmtime(path)
        cache_key = (path, mtime)
        cached = self._parsed.get(cache_key)
        if cached is not None:
            return cached

        headers: List[str] = []
        size = 0
        domain_min = (0.0, 0.0, 0.0)
        domain_max = (1.0, 1.0, 1.0)
        values: List[Tuple[float, float, float]] = []

        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#"):
                    headers.append(raw.rstrip("\r\n"))
                    continue

                parts = line.split()
                key = parts[0].upper()

                if key == "TITLE":
                    headers.append(raw.rstrip("\r\n"))
                    continue
                elif key == "LUT_3D_SIZE" and len(parts) >= 2:
                    try:
                        size = int(parts[1])
                    except ValueError:
                        raise ValueError(f"Invalid LUT_3D_SIZE in {path}: {parts[1]}")
                    headers.append(raw.rstrip("\r\n"))
                elif key == "DOMAIN_MIN" and len(parts) >= 4:
                    try:
                        domain_min = tuple(float(v) for v in parts[1:4])
                    except ValueError:
                        raise ValueError(f"Invalid DOMAIN_MIN in {path}")
                    headers.append(raw.rstrip("\r\n"))
                elif key == "DOMAIN_MAX" and len(parts) >= 4:
                    try:
                        domain_max = tuple(float(v) for v in parts[1:4])
                    except ValueError:
                        raise ValueError(f"Invalid DOMAIN_MAX in {path}")
                    headers.append(raw.rstrip("\r\n"))
                elif len(parts) >= 3:
                    # Check for non-numeric or non-finite values
                    try:
                        r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
                    except ValueError:
                        # Non-numeric line (e.g. metadata header)
                        headers.append(raw.rstrip("\r\n"))
                        continue

                    if not (math.isfinite(r) and math.isfinite(g) and math.isfinite(b)):
                        raise ValueError(f"Non-finite value (NaN/Inf) found in .cube LUT: {path}")
                    values.append((r, g, b))

        if size < 2:
            raise ValueError(f"Invalid or missing LUT_3D_SIZE (< 2) in {path}: size={size}")
        expected_count = size ** 3
        if len(values) != expected_count:
            raise ValueError(
                f"Corrupt or incomplete .cube LUT: {path}. Expected {expected_count} values, got {len(values)}"
            )

        for i in range(3):
            if not (math.isfinite(domain_min[i]) and math.isfinite(domain_max[i])):
                raise ValueError(f"Invalid domain bounds in {path}")
            if domain_min[i] >= domain_max[i]:
                raise ValueError(f"DOMAIN_MIN must be strictly less than DOMAIN_MAX in {path}")

        parsed = (headers, size, domain_min, domain_max, values)
        self._parsed[cache_key] = parsed
        return parsed

    def blended_path(self, path: str, strength: float) -> str:
        """Legacy fallback: create a blended .cube file when shader parameters are unsupported."""
        path = os.path.abspath(path)
        strength = max(0.0, min(1.0, float(strength)))
        if strength >= 0.9995:
            return path
        if strength <= 0.0005:
            return ""
        mtime = os.path.getmtime(path)
        stamp = f"{path}|{mtime:.6f}|{strength:.5f}".encode()
        target = self.cache_dir / f"{hashlib.sha1(stamp).hexdigest()}.cube"
        if target.exists():
            return str(target)
        headers, size, domain_min, domain_max, values = self._parse(path)
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            for header in headers:
                handle.write(header + "\n")
            index = 0
            # Adobe Cube 1.0 ordering: red index changes fastest, then green, then blue
            for b in range(size):
                for g in range(size):
                    for r in range(size):
                        identity = (
                            domain_min[0] + (domain_max[0] - domain_min[0]) * r / (size - 1),
                            domain_min[1] + (domain_max[1] - domain_min[1]) * g / (size - 1),
                            domain_min[2] + (domain_max[2] - domain_min[2]) * b / (size - 1),
                        )
                        lut = values[index]
                        blended = tuple(identity[i] * (1.0 - strength) + lut[i] * strength for i in range(3))
                        handle.write("%.8f %.8f %.8f\n" % blended)
                        index += 1
        return str(target)


class MpvGpuLutPrototype:
    """Apply GPU color parameters and native LUT blending through MPV gpu-next.

    When ``use_gpu_shaders`` is False (default for stability until visual parity is verified),
    LUT blend uses the legacy blended .cube path.
    When ``use_gpu_shaders`` is True (opt-in via CAPCAP_GPU_COLOR_SHADERS=1 or parameter),
    the 3D LUT is loaded once and blend strength is controlled via GPU shader parameters.
    """

    def __init__(
        self,
        mpv_player,
        cache_dir: str | os.PathLike,
        shader_path: Optional[str] = None,
        use_gpu_shaders: Optional[bool] = None,
    ):
        self.player = mpv_player
        self.cache = CubeLutCache(cache_dir)
        self._current_lut_path: str = ""
        self._current_lut_mtime: float = -1.0
        self._shader_opts: Dict[str, str] = {}
        self._params: Dict[str, float] = {
            "capcap_brightness": 0.0,
            "capcap_contrast": 1.0,
            "capcap_saturation": 1.0,
            "capcap_gamma": 1.0,
            "capcap_hue": 0.0,
            "capcap_temp": 0.0,
            "capcap_shadow_point": 0.25,
            "capcap_highlight_point": 0.75,
            "capcap_lut_strength": 0.0,
        }
        self._shader_version: int = 0
        self._active_shader_path: Optional[Path] = None

        if use_gpu_shaders is None:
            self.use_gpu_shaders = os.environ.get("CAPCAP_GPU_COLOR_SHADERS", "0") == "1"
        else:
            self.use_gpu_shaders = bool(use_gpu_shaders)

        if shader_path:
            self._shader_path = os.path.abspath(shader_path)
        else:
            default_shader = Path(__file__).resolve().parent.parent / "assets" / "shaders" / "preview_color.glsl"
            self._shader_path = str(default_shader) if default_shader.exists() else ""

        self._shader_loaded = False

    def _build_shader_text(self) -> str:
        """Build GLSL shader text with active color and LUT parameters substituted as compile-time constants."""
        if not self._shader_path or not os.path.exists(self._shader_path):
            return ""
        with open(self._shader_path, "r", encoding="utf-8") as handle:
            text = handle.read()
        for param, val in self._params.items():
            pattern = rf"float\s+{re.escape(param)}\s*=\s*[^;]+;"
            replacement = f"float {param} = {val:.6f};"
            text = re.sub(pattern, replacement, text)
        return text

    def _sync_gpu_shader(self) -> None:
        """Synchronize active shader to MPV via a versioned GLSL file to bypass libplacebo caching and link parameters."""
        if not self.use_gpu_shaders or not self._shader_path or not os.path.exists(self._shader_path):
            return

        self._shader_version += 1
        shader_dir = self.cache.cache_dir / "shaders"
        shader_dir.mkdir(parents=True, exist_ok=True)
        new_file = shader_dir / f"preview_color_active_v{self._shader_version}.glsl"

        content = self._build_shader_text()
        new_file.write_text(content, encoding="utf-8")

        old_file = self._active_shader_path
        self._active_shader_path = new_file

        try:
            self.player["glsl-shaders"] = [str(new_file)]
            self._shader_loaded = True
        except Exception:
            pass

        # Clean up previous versioned active shader to ensure zero file bloat
        if old_file and old_file.exists() and old_file != new_file:
            try:
                old_file.unlink()
            except Exception:
                pass

    def ensure_shader_loaded(self) -> bool:
        """Register preview_color active shader with mpv if available."""
        if not self.use_gpu_shaders:
            return False
        if self._active_shader_path and self._active_shader_path.exists():
            return True
        self._sync_gpu_shader()
        return bool(self._active_shader_path and self._active_shader_path.exists())

    def set_color_state(self, state: Optional[dict]) -> None:
        """Update GPU shader parameters for color adjustments directly in-memory."""
        if not self.use_gpu_shaders:
            return

        source = state.get("final", state) if isinstance(state, dict) else {}
        if not isinstance(source, dict):
            source = {}

        def _val(field: str) -> float:
            try:
                return max(-100.0, min(100.0, float(source.get(field, 0.0) or 0.0)))
            except (TypeError, ValueError):
                return 0.0

        b = _val("brightness")
        c = _val("contrast")
        s = _val("saturation")
        g = _val("gamma")
        h = _val("hue")
        t = _val("temperature")
        hl = _val("highlights")
        sh = _val("shadows")

        # Map to shader uniform parameters matching build_video_filter_chain() formulas
        brightness_val = b / 100.0 * 0.35
        contrast_val = max(0.4, min(1.8, 1.0 + c / 100.0 * 0.65))
        saturation_val = max(0.0, min(2.2, 1.0 + s / 100.0 * 1.2))
        gamma_val = max(0.5, min(2.0, 1.0 + g / 100.0 * 0.75))
        hue_val = max(-180.0, min(180.0, h * 1.8))
        temp_val = max(-0.35, min(0.35, t / 100.0 * 0.35))
        shadow_pt = max(0.0, min(0.45, 0.25 + sh / 100.0 * 0.18))
        highlight_pt = max(0.55, min(1.0, 0.75 + hl / 100.0 * 0.18))

        self._params.update({
            "capcap_brightness": brightness_val,
            "capcap_contrast": contrast_val,
            "capcap_saturation": saturation_val,
            "capcap_gamma": gamma_val,
            "capcap_hue": hue_val,
            "capcap_temp": temp_val,
            "capcap_shadow_point": shadow_pt,
            "capcap_highlight_point": highlight_pt,
        })

        self._shader_opts.update({
            "capcap_brightness": f"{brightness_val:.4f}",
            "capcap_contrast": f"{contrast_val:.4f}",
            "capcap_saturation": f"{saturation_val:.4f}",
            "capcap_gamma": f"{gamma_val:.4f}",
            "capcap_hue": f"{hue_val:.3f}",
            "capcap_temp": f"{temp_val:.4f}",
            "capcap_shadow_point": f"{shadow_pt:.4f}",
            "capcap_highlight_point": f"{highlight_pt:.4f}",
        })

        try:
            current_opts = dict(getattr(self.player, "glsl_shader_opts", {}) or {})
            current_opts.update(self._shader_opts)
            self.player["glsl-shader-opts"] = current_opts
        except Exception:
            pass

        self._sync_gpu_shader()

    def apply(self, lut_path: str, strength_percent: float) -> str:
        """Apply 3D LUT.
        
        If use_gpu_shaders is False (default): uses the legacy blended .cube path.
        If use_gpu_shaders is True (opt-in): uploads LUT once (reloading on mtime change)
        and controls blend via GPU shader parameters without writing .cube files.
        """
        lut_path = str(lut_path or "").strip()
        try:
            strength = max(0.0, min(1.0, float(strength_percent) / 100.0))
        except (TypeError, ValueError):
            strength = 0.0

        if not lut_path or strength <= 0.001 or not os.path.exists(lut_path):
            self.clear()
            return ""

        # Validate .cube parsing and fetch mtime
        self.cache._parse(lut_path)
        abs_lut = str(Path(lut_path).resolve())
        mtime = os.path.getmtime(abs_lut)

        if not self.use_gpu_shaders:
            # Legacy verified default path
            target = self.cache.blended_path(abs_lut, strength)
            if self._current_lut_mtime > 0 and self._current_lut_mtime != mtime:
                try:
                    self.player.command("set", "lut", "")
                except Exception:
                    pass
            self.player.command("set", "lut", target)
            self._current_lut_path = abs_lut
            self._current_lut_mtime = mtime
            return target

        # Opt-in GPU shader path
        if self._current_lut_path != abs_lut or self._current_lut_mtime != mtime:
            try:
                if self._current_lut_mtime > 0 and self._current_lut_mtime != mtime:
                    self.player.command("set", "lut", "")
                self.player.command("set", "lut", abs_lut)
                self._current_lut_path = abs_lut
                self._current_lut_mtime = mtime
            except Exception:
                pass

        self._params["capcap_lut_strength"] = strength
        self._shader_opts["capcap_lut_strength"] = f"{strength:.4f}"
        try:
            current_opts = dict(getattr(self.player, "glsl_shader_opts", {}) or {})
            current_opts.update(self._shader_opts)
            self.player["glsl-shader-opts"] = current_opts
        except Exception:
            pass

        self._sync_gpu_shader()
        return abs_lut

    def clear(self) -> None:
        """Clear LUT and reset strength parameter."""
        if self._current_lut_path:
            try:
                self.player.command("set", "lut", "")
            except Exception:
                pass
            self._current_lut_path = ""
            self._current_lut_mtime = -1.0

        if self.use_gpu_shaders:
            self._params["capcap_lut_strength"] = 0.0
            self._shader_opts["capcap_lut_strength"] = "0.0"
            try:
                current_opts = dict(getattr(self.player, "glsl_shader_opts", {}) or {})
                current_opts.update(self._shader_opts)
                self.player["glsl-shader-opts"] = current_opts
            except Exception:
                pass
            self._sync_gpu_shader()

    def cleanup(self) -> None:
        """Clean up active shader resources on player close."""
        if self._active_shader_path and self._active_shader_path.exists():
            try:
                self._active_shader_path.unlink()
            except Exception:
                pass
            self._active_shader_path = None

