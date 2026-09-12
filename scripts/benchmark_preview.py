#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/benchmark_preview.py

Benchmark and capability probe tool for CapCap native media preview.
Measures baseline system capability, audio/video decode, mpv DLL properties,
and event loop / subprocess / file overhead of media preview operations.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import fractions
import io
import json
from math import ceil
import os
import platform
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# Ensure project directories are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")

for path in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


def percentile95(values: List[float]) -> float:
    """Return the 95th percentile value from a list of measurements."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("No measurements")
    return ordered[ceil(0.95 * len(ordered)) - 1]


assert percentile95(list(range(1, 101))) == 95


def _get_ram_info() -> Dict[str, Any]:
    """Return physical RAM information via Windows GlobalMemoryStatusEx."""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_gib = round(stat.ullTotalPhys / (1024 ** 3), 2)
            avail_gib = round(stat.ullAvailPhys / (1024 ** 3), 2)
            return {
                "total_gib": total_gib,
                "available_gib": avail_gib,
                "memory_load_percent": stat.dwMemoryLoad,
            }
    return {"total_gib": None, "available_gib": None, "memory_load_percent": None}


def _probe_ffmpeg_cli() -> Dict[str, Any]:
    """Probe bundled or system ffmpeg.exe."""
    try:
        from runtime_paths import bin_path
        ffmpeg_bin = bin_path("ffmpeg", "ffmpeg.exe")
    except Exception:
        ffmpeg_bin = "ffmpeg"

    res = {
        "path": str(ffmpeg_bin),
        "exists": os.path.exists(str(ffmpeg_bin)) if os.path.isabs(str(ffmpeg_bin)) else True,
        "version": "unknown",
        "usable": False,
    }
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [str(ffmpeg_bin), "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            **kwargs,
        )
        if proc.returncode == 0 and proc.stdout:
            first_line = proc.stdout.splitlines()[0]
            res["version"] = first_line
            res["usable"] = True
    except Exception as exc:
        res["error"] = str(exc)
    return res


def _probe_qt_multimedia() -> Dict[str, Any]:
    """Probe PySide6 QtMultimedia audio output capabilities."""
    info: Dict[str, Any] = {
        "available": False,
        "default_device": None,
        "preferred_format": None,
        "devices": [],
        "qaudiosink_usable": False,
    }
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

        # Initialize QCoreApplication if not yet created
        app = QCoreApplication.instance()
        created_app = False
        if app is None:
            app = QCoreApplication([])
            created_app = True

        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            info["default_device"] = "None (null device)"
        else:
            info["default_device"] = dev.description()
            fmt = dev.preferredFormat()
            info["preferred_format"] = {
                "sample_rate": fmt.sampleRate(),
                "channel_count": fmt.channelCount(),
                "sample_format": str(fmt.sampleFormat()),
            }

            # Test QAudioSink creation
            sink = QAudioSink(dev, fmt)
            info["qaudiosink_usable"] = sink is not None
            info["buffer_size_bytes"] = sink.bufferSize()

        devices = []
        for d in QMediaDevices.audioOutputs():
            devices.append({
                "description": d.description(),
                "is_default": d.isDefault(),
                "id": str(d.id()),
            })
        info["devices"] = devices
        info["available"] = True

        if created_app:
            pass  # leave alive
    except Exception as exc:
        info["error"] = str(exc)
    return info


def _probe_pyav() -> Dict[str, Any]:
    """Probe PyAV (av) capabilities for in-memory decode, resampling, and atempo filter."""
    res: Dict[str, Any] = {
        "installed": False,
        "version": None,
        "ffmpeg_libraries": {},
        "resampler_usable": False,
        "in_memory_decode_usable": False,
        "atempo_graph_usable": False,
    }
    try:
        import av
        import av.error
        import av.filter
        import numpy as np
        import soundfile as sf

        res["installed"] = True
        res["version"] = getattr(av, "__version__", "unknown")
        res["ffmpeg_libraries"] = {
            k: list(v) if isinstance(v, (tuple, list)) else str(v)
            for k, v in getattr(av, "library_versions", {}).items()
        }

        # 1. Test AudioResampler
        try:
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
            res["resampler_usable"] = resampler is not None
        except Exception as exc:
            res["resampler_error"] = str(exc)

        # 2. Test in-memory decode of WAV
        try:
            samples = (np.sin(2 * np.pi * 440 * np.linspace(0, 0.25, 4000))).astype(np.float32)
            bio = io.BytesIO()
            sf.write(bio, samples, 16000, format="WAV", subtype="PCM_16")
            bio.seek(0)
            container = av.open(bio)
            stream = container.streams.audio[0]
            frames = list(container.decode(stream))
            container.close()
            res["in_memory_decode_usable"] = len(frames) > 0
            res["in_memory_decoded_frames"] = len(frames)
        except Exception as exc:
            res["in_memory_decode_error"] = str(exc)

        # 3. Test atempo filter graph
        try:
            graph = av.filter.Graph()
            src = graph.add_abuffer(
                sample_rate=16000,
                format="flt",
                layout="mono",
                time_base=fractions.Fraction(1, 16000),
            )
            filt = graph.add("atempo", "1.25")
            sink = graph.add("abuffersink")
            src.link_to(filt)
            filt.link_to(sink)
            graph.configure()

            test_frame = av.AudioFrame.from_ndarray(
                np.zeros((1, 8000), dtype=np.float32),
                format="flt",
                layout="mono",
            )
            test_frame.sample_rate = 16000
            test_frame.time_base = fractions.Fraction(1, 16000)
            test_frame.pts = 0

            graph.push(test_frame)
            graph.push(None)

            out_count = 0
            while True:
                try:
                    f = graph.pull()
                    out_count += f.samples
                except (av.error.EOFError, av.error.FFmpegError):
                    break
            res["atempo_graph_usable"] = out_count > 0
            res["atempo_output_samples"] = out_count
        except Exception as exc:
            res["atempo_graph_error"] = str(exc)

    except Exception as exc:
        res["error"] = str(exc)
    return res


def _probe_soundfile() -> Dict[str, Any]:
    """Probe SoundFile and libsndfile capabilities."""
    res: Dict[str, Any] = {
        "installed": False,
        "version": None,
        "libsndfile_version": None,
        "virtual_io_usable": False,
    }
    try:
        import numpy as np
        import soundfile as sf

        res["installed"] = True
        res["version"] = getattr(sf, "__version__", "unknown")
        res["libsndfile_version"] = getattr(sf, "__libsndfile_version__", "unknown")

        # Virtual IO read/write test
        bio = io.BytesIO()
        data = np.zeros(1600, dtype=np.float32)
        sf.write(bio, data, 16000, format="WAV", subtype="PCM_16")
        bio.seek(0)
        read_data, rate = sf.read(bio, dtype="float32")
        res["virtual_io_usable"] = (len(read_data) == 1600 and rate == 16000)
    except Exception as exc:
        res["error"] = str(exc)
    return res


def _run_mpv_probe_worker() -> None:
    """Internal helper executed as a separate subprocess to safely probe libmpv DLL."""
    result: Dict[str, Any] = {
        "dll_loaded": False,
        "mpv_version": None,
        "hwdec": None,
        "hwdec_current": None,
        "glsl_shaders_supported": False,
        "audio_device_list": [],
    }
    try:
        from ui.utils.media_backend import prepare_mpv_bundle
        prepare_mpv_bundle()
        import mpv

        try:
            player = mpv.MPV(vo="gpu-next,gpu,null", ao="null")
        except Exception:
            player = mpv.MPV(vo="null", ao="null")
        result["dll_loaded"] = True
        result["mpv_version"] = getattr(player, "mpv_version", "unknown")
        result["hwdec"] = list(player.hwdec) if hasattr(player, "hwdec") else None

        try:
            result["hwdec_current"] = player["hwdec-current"]
        except Exception:
            result["hwdec_current"] = None

        try:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".glsl", delete=False) as tf:
                tf.write("//!HOOK MAIN\n//!BIND HOOKED\nvec4 hook() { return HOOKED; }\n")
                dummy_shader = tf.name
            try:
                player["glsl-shaders"] = [dummy_shader]
                active_shaders = player["glsl-shaders"]
                result["glsl_shaders_supported"] = isinstance(active_shaders, list) and len(active_shaders) > 0
            finally:
                try:
                    os.remove(dummy_shader)
                except Exception:
                    pass
        except Exception:
            result["glsl_shaders_supported"] = False

        try:
            result["audio_device_list"] = player.audio_device_list or []
        except Exception:
            result["audio_device_list"] = []

        player.terminate()
    except Exception as exc:
        result["error"] = str(exc)

    print("__MPV_PROBE_RESULT__" + json.dumps(result))
    sys.exit(0)


def _probe_mpv_safe() -> Dict[str, Any]:
    """Run the mpv probe in a child process to guard against native DLL crash."""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.run(
            [sys.executable, "-B", __file__, "--probe-mpv-worker"],
            capture_output=True,
            text=True,
            timeout=10,
            **kwargs,
        )
        stdout = proc.stdout or ""
        marker = "__MPV_PROBE_RESULT__"
        if marker in stdout:
            payload = stdout.split(marker, 1)[1].strip()
            return json.loads(payload)
        return {
            "dll_loaded": False,
            "exit_code": proc.returncode,
            "error": "Probe worker exited without result marker",
            "stderr": proc.stderr.strip() if proc.stderr else "",
        }
    except Exception as exc:
        return {
            "dll_loaded": False,
            "error": f"Failed to spawn mpv probe worker: {exc}",
        }


def probe_all_capabilities() -> Dict[str, Any]:
    """Probe all system, library, device, and DLL capabilities."""
    sys_info = {
        "platform": platform.platform(),
        "os": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "architecture": platform.architecture()[0],
        "processor": platform.processor(),
        "cpu_count_logical": os.cpu_count(),
        "ram": _get_ram_info(),
        "python_version": sys.version,
    }

    lib_versions = {
        "numpy": None,
        "scipy": None,
        "PySide6": None,
        "av": None,
        "soundfile": None,
    }
    for mod_name in lib_versions:
        try:
            m = __import__(mod_name)
            lib_versions[mod_name] = getattr(m, "__version__", "installed")
        except Exception as exc:
            lib_versions[mod_name] = f"error: {exc}"

    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "system": sys_info,
        "libraries": lib_versions,
        "ffmpeg_cli": _probe_ffmpeg_cli(),
        "qt_multimedia": _probe_qt_multimedia(),
        "pyav": _probe_pyav(),
        "soundfile": _probe_soundfile(),
        "libmpv": _probe_mpv_safe(),
    }


def probe_video_file(video_path: str) -> Dict[str, Any]:
    """Probe metadata (codec, resolution, fps, GOP, duration) of a benchmark video."""
    res: Dict[str, Any] = {
        "path": video_path,
        "exists": os.path.exists(video_path),
        "size_bytes": os.path.getsize(video_path) if os.path.exists(video_path) else 0,
    }
    if not res["exists"]:
        res["error"] = "File not found"
        return res

    try:
        import av
        container = av.open(video_path)
        res["format"] = container.format.name
        res["duration_seconds"] = float(container.duration / 1_000_000) if container.duration else None

        v_stream = next((s for s in container.streams if s.type == "video"), None)
        if v_stream:
            res["video_stream"] = {
                "codec": v_stream.codec_context.name,
                "width": v_stream.codec_context.width,
                "height": v_stream.codec_context.height,
                "fps": float(v_stream.average_rate) if v_stream.average_rate else None,
                "pix_fmt": v_stream.codec_context.pix_fmt,
                "frames": v_stream.frames,
            }
            # Measure sample GOP size (sample first 60 packets for keyframe interval)
            keyframes = []
            pkt_count = 0
            for packet in container.demux(v_stream):
                if packet.is_keyframe:
                    keyframes.append(pkt_count)
                pkt_count += 1
                if pkt_count >= 120 or len(keyframes) >= 5:
                    break
            if len(keyframes) >= 2:
                gop_sizes = [keyframes[i] - keyframes[i - 1] for i in range(1, len(keyframes))]
                res["video_stream"]["estimated_gop"] = gop_sizes[0]
            container.close()

        a_stream = next((s for s in container.streams if s.type == "audio"), None)
        if a_stream:
            res["audio_stream"] = {
                "codec": a_stream.codec_context.name,
                "sample_rate": a_stream.codec_context.sample_rate,
                "channels": a_stream.codec_context.channels,
            }
    except Exception as exc:
        res["error"] = str(exc)
    return res


class SubprocessAuditor:
    """Audit hook context manager to intercept and count subprocess.Popen calls."""

    def __init__(self):
        self.spawned: List[str] = []
        self._active = False

    def __enter__(self):
        self._active = True

        def _hook(event, args):
            if self._active and event == "subprocess.Popen":
                cmd = args[0] if args else "unknown"
                self.spawned.append(str(cmd))

        sys.addaudithook(_hook)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._active = False


def run_baseline_benchmarks(temp_dir: str) -> Dict[str, Any]:
    """Execute baseline measurements of the legacy audio mixing and conversion path.

    - 100 volume slider changes with TTS + Music (measuring call duration, subprocesses, files)
    - 200 sentence conversions (synthesized WAV speed/fit/trim)
    """
    os.makedirs(temp_dir, exist_ok=True)
    import numpy as np
    import soundfile as sf
    from app.audio_mixer import mix_audio_tracks

    # Generate test audio files
    sr = 16000
    voice_path = os.path.join(temp_dir, "bench_voice.wav")
    music_path = os.path.join(temp_dir, "bench_music.wav")

    voice_data = (np.sin(2 * np.pi * 300 * np.linspace(0, 10, 10 * sr))).astype(np.float32) * 0.5
    music_data = (np.sin(2 * np.pi * 150 * np.linspace(0, 15, 15 * sr))).astype(np.float32) * 0.3

    sf.write(voice_path, voice_data, sr, format="WAV", subtype="PCM_16")
    sf.write(music_path, music_data, sr, format="WAV", subtype="PCM_16")

    print("[Benchmark] Measuring legacy volume adjustments with UI debounce & overwrite (100 iterations)...")
    volume_latencies_ms: List[float] = []
    files_created: List[str] = []
    subprocesses_spawned: List[str] = []
    out_wav = os.path.join(temp_dir, "preview_mix_active.wav")
    disk_writes_count = 0
    cumulative_bytes_written = 0

    auditor = SubprocessAuditor()
    last_processed_time = 0.0
    with auditor:
        for i in range(100):
            tts_vol = float(10 + (i % 90))
            music_vol = float(100 - (i % 70))
            simulated_tick_time = i * 0.015

            t0 = time.perf_counter()
            # 30ms debounce simulation or final tick
            if (simulated_tick_time - last_processed_time >= 0.030) or (i == 99):
                tracks = [
                    {"path": voice_path, "start": 0.0, "end": 10.0, "volume": tts_vol, "muted": False},
                    {"path": music_path, "start": 0.0, "end": 10.0, "source_start": 0.0, "volume": music_vol, "muted": False},
                ]
                mix_audio_tracks(tracks=tracks, output_wav_path=out_wav, total_duration_ms=10000)
                last_processed_time = simulated_tick_time
                disk_writes_count += 1
                if os.path.exists(out_wav):
                    cumulative_bytes_written += os.path.getsize(out_wav)
                    if out_wav not in files_created:
                        files_created.append(out_wav)

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            volume_latencies_ms.append(elapsed_ms)

    subprocesses_spawned = auditor.spawned

    # Clean up generated mix files
    for f in files_created:
        try:
            os.remove(f)
        except OSError:
            pass

    # Benchmark native in-memory volume path using PreviewAudioEngine
    print("[Benchmark] Measuring native in-memory volume adjustments (100 iterations)...")
    native_latencies_ms: List[float] = []
    native_subprocesses: List[str] = []
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from ui.utils.preview_audio import PreviewAudioEngine
        engine = PreviewAudioEngine()
        engine.set_tracks([
            {"id": "voice", "path": voice_path, "start": 0.0, "end": 10.0, "volume": 100.0, "muted": False},
            {"id": "music", "path": music_path, "start": 0.0, "end": 10.0, "source_start": 0.0, "volume": 100.0, "muted": False},
        ])
        app.processEvents()

        with SubprocessAuditor() as nat_auditor:
            for i in range(100):
                tts_vol = float(10 + (i % 90))
                music_vol = float(100 - (i % 70))
                t0 = time.perf_counter()
                engine.set_track_gain("voice", tts_vol / 100.0)
                engine.set_track_gain("music", music_vol / 100.0)
                app.processEvents()
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                native_latencies_ms.append(elapsed_ms)
        native_subprocesses = nat_auditor.spawned
        engine.close()
    except Exception as exc:
        print(f"[Benchmark] Native volume benchmark skipped: {exc}")

    res: Dict[str, Any] = {
        "legacy_volume_100_runs": {
            "min_ms": round(min(volume_latencies_ms), 2),
            "median_ms": round(sorted(volume_latencies_ms)[len(volume_latencies_ms) // 2], 2),
            "p95_ms": round(percentile95(volume_latencies_ms), 2),
            "max_ms": round(max(volume_latencies_ms), 2),
            "total_time_s": round(sum(volume_latencies_ms) / 1000.0, 2),
            "subprocesses_spawned_count": len(subprocesses_spawned),
            "disk_writes_count": disk_writes_count,
            "files_created_count": len(files_created),
            "total_bytes_written": cumulative_bytes_written,
        }
    }
    if native_latencies_ms:
        res["native_volume_100_runs"] = {
            "min_ms": round(min(native_latencies_ms), 2),
            "median_ms": round(sorted(native_latencies_ms)[len(native_latencies_ms) // 2], 2),
            "p95_ms": round(percentile95(native_latencies_ms), 2),
            "max_ms": round(max(native_latencies_ms), 2),
            "total_time_s": round(sum(native_latencies_ms) / 1000.0, 2),
            "subprocesses_spawned_count": len(native_subprocesses),
            "disk_writes_count": 0,
            "files_created_count": 0,
            "total_bytes_written": 0,
        }
    return res


def main():
    parser = argparse.ArgumentParser(description="CapCap Preview Benchmark & Capability Probe")
    parser.add_argument("--probe", action="store_true", help="Probe system, libraries, devices, and DLL capabilities")
    parser.add_argument("--probe-mpv-worker", action="store_true", help="Internal helper to safely probe mpv in a child process")
    parser.add_argument("--capture-ui", action="store_true", help="Run baseline workload measurements on current pipeline")
    parser.add_argument("--video", type=str, default="", help="Path to benchmark video file")
    parser.add_argument("--output", type=str, default="", help="Output JSON path")

    args = parser.parse_args()

    if args.probe_mpv_worker:
        _run_mpv_probe_worker()
        return

    results: Dict[str, Any] = {}

    if args.probe or not (args.capture_ui or args.video):
        print("=" * 60)
        print("CapCap Native Media Preview Capability Probe")
        print("=" * 60)
        cap = probe_all_capabilities()
        results["capabilities"] = cap

        print(f"OS: {cap['system']['platform']} ({cap['system']['architecture']})")
        print(f"CPU: {cap['system']['processor']} ({cap['system']['cpu_count_logical']} logical cores)")
        print(f"RAM: {cap['system']['ram']['available_gib']} GiB available / {cap['system']['ram']['total_gib']} GiB total")
        print(f"PySide6: {cap['libraries']['PySide6']}")
        print(f"PyAV: {cap['libraries']['av']} (resampler: {cap['pyav']['resampler_usable']}, atempo: {cap['pyav']['atempo_graph_usable']})")
        print(f"SoundFile: {cap['libraries']['soundfile']} (libsndfile: {cap['soundfile']['libsndfile_version']})")
        print(f"Default Audio Device: {cap['qt_multimedia'].get('default_device')}")
        print(f"libmpv: loaded={cap['libmpv'].get('dll_loaded')}, version={cap['libmpv'].get('mpv_version')}, glsl={cap['libmpv'].get('glsl_shaders_supported')}")
        print(f"FFmpeg CLI: usable={cap['ffmpeg_cli'].get('usable')}, path={cap['ffmpeg_cli'].get('path')}")
        print("=" * 60)

    if args.video:
        print(f"\nProbing video file: {args.video}")
        v_info = probe_video_file(args.video)
        results["video"] = v_info
        if "video_stream" in v_info:
            vs = v_info["video_stream"]
            print(f"Codec: {vs.get('codec')}, Resolution: {vs.get('width')}x{vs.get('height')}, FPS: {vs.get('fps')}, GOP: {vs.get('estimated_gop')}")
        if "audio_stream" in v_info:
            aus = v_info["audio_stream"]
            print(f"Audio: {aus.get('codec')}, Sample Rate: {aus.get('sample_rate')}, Channels: {aus.get('channels')}")

    if args.capture_ui:
        print("\nRunning baseline workload measurements...")
        temp_dir = os.path.join(PROJECT_ROOT, "temp", "benchmarks", "scratch")
        bench = run_baseline_benchmarks(temp_dir)
        results["baseline"] = bench
        vol_stats = bench["legacy_volume_100_runs"]
        nat_stats = bench.get("native_volume_100_runs", {})
        print(f"Legacy volume 100 runs:")
        print(f"  p95 latency: {vol_stats['p95_ms']} ms (min: {vol_stats['min_ms']} ms, max: {vol_stats['max_ms']} ms)")
        print(f"  Subprocesses spawned: {vol_stats['subprocesses_spawned_count']}")
        print(f"  Disk writes: {vol_stats.get('disk_writes_count', 0)} ({vol_stats['total_bytes_written'] / (1024 * 1024):.2f} MiB written)")
        if nat_stats:
            print(f"Native in-memory volume 100 runs:")
            print(f"  p95 latency: {nat_stats['p95_ms']} ms (min: {nat_stats['min_ms']} ms, max: {nat_stats['max_ms']} ms)")
            print(f"  Subprocesses spawned: {nat_stats['subprocesses_spawned_count']}")
            print(f"  Disk writes: {nat_stats['disk_writes_count']} (0.00 MiB written)")

    # Save output if specified
    out_path = args.output
    if not out_path:
        default_dir = os.path.join(PROJECT_ROOT, "temp", "benchmarks")
        os.makedirs(default_dir, exist_ok=True)
        out_path = os.path.join(default_dir, "capabilities.json" if not args.capture_ui else "baseline.json")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved benchmark results to: {out_path}")


if __name__ == "__main__":
    main()
