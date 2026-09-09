from __future__ import annotations

import uuid
from typing import Any


class TimeWarpService:
    """Service to manage non-destructive video time-warps (freeze frame / extension)

    and timeline ripple-shifting.
    """

    @staticmethod
    def create_time_warp(
        time: float,
        duration: float,
        warp_type: str = "freeze",
        segment_index: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return {
            "id": f"warp_{uuid.uuid4().hex[:8]}",
            "time": round(float(time), 3),
            "duration": round(float(duration), 3),
            "type": warp_type,
            "segment_index": segment_index,
            "note": note,
        }

    @staticmethod
    def apply_segment_extension(
        segments: list[dict],
        segment_index: int,
        added_duration: float,
        translated_segments: list[dict] | None = None,
    ) -> tuple[dict, list[dict], list[dict] | None]:
        """Extend a segment by added_duration (+Δt) and ripple shift all subsequent segments by +Δt.

        Returns (new_warp, updated_segments, updated_translated_segments).
        """
        if not segments and translated_segments:
            segments = translated_segments
            translated_segments = None

        if segment_index < 0 or segment_index >= len(segments):
            raise IndexError(f"Segment index {segment_index} out of range [0, {len(segments)})")

        target_seg = segments[segment_index]
        orig_end = float(target_seg.get("end", 0.0))
        warp_time = orig_end
        delta = round(float(added_duration), 3)
        if delta <= 0:
            raise ValueError(f"Added duration must be positive, got {delta}")

        warp = TimeWarpService.create_time_warp(
            time=warp_time,
            duration=delta,
            warp_type="freeze",
            segment_index=segment_index,
        )

        prev_ext = float(target_seg.get("extended_duration", 0.0) or 0.0)
        target_seg["extended_duration"] = round(prev_ext + delta, 3)
        target_seg["end"] = round(float(target_seg["end"]) + delta, 3)
        target_seg["time_warp_id"] = warp["id"]

        for i in range(segment_index + 1, len(segments)):
            segments[i]["start"] = round(float(segments[i]["start"]) + delta, 3)
            segments[i]["end"] = round(float(segments[i]["end"]) + delta, 3)
            if "_audio_end" in segments[i] and segments[i]["_audio_end"] is not None:
                segments[i]["_audio_end"] = round(float(segments[i]["_audio_end"]) + delta, 3)

        if translated_segments and 0 <= segment_index < len(translated_segments):
            t_seg = translated_segments[segment_index]
            t_prev_ext = float(t_seg.get("extended_duration", 0.0) or 0.0)
            t_seg["extended_duration"] = round(t_prev_ext + delta, 3)
            t_seg["end"] = round(float(t_seg["end"]) + delta, 3)
            t_seg["time_warp_id"] = warp["id"]

            for i in range(segment_index + 1, len(translated_segments)):
                translated_segments[i]["start"] = round(float(translated_segments[i]["start"]) + delta, 3)
                translated_segments[i]["end"] = round(float(translated_segments[i]["end"]) + delta, 3)
                if "_audio_end" in translated_segments[i] and translated_segments[i]["_audio_end"] is not None:
                    translated_segments[i]["_audio_end"] = round(float(translated_segments[i]["_audio_end"]) + delta, 3)

        return warp, segments, translated_segments

    @staticmethod
    def remove_segment_extension(
        segments: list[dict],
        segment_index: int,
        translated_segments: list[dict] | None = None,
        warps: list[dict] | None = None,
    ) -> tuple[float, list[dict], list[dict] | None, list[dict]]:
        """Revert the extension on segment_index, subtracting extended_duration (-Δt)

        and ripple shifting all subsequent segments by -Δt.
        Returns (removed_delta, updated_segments, updated_translated_segments, updated_warps).
        """
        if not segments and translated_segments:
            segments = translated_segments
            translated_segments = None

        if segment_index < 0 or segment_index >= len(segments):
            raise IndexError(f"Segment index {segment_index} out of range [0, {len(segments)})")

        target_seg = segments[segment_index]
        delta = float(target_seg.get("extended_duration", 0.0) or 0.0)
        if delta <= 0:
            return 0.0, segments, translated_segments, warps or []

        warp_id = target_seg.get("time_warp_id", "")
        target_seg["end"] = round(float(target_seg["end"]) - delta, 3)
        target_seg["extended_duration"] = 0.0
        target_seg.pop("time_warp_id", None)

        for i in range(segment_index + 1, len(segments)):
            segments[i]["start"] = round(max(0.0, float(segments[i]["start"]) - delta), 3)
            segments[i]["end"] = round(max(0.0, float(segments[i]["end"]) - delta), 3)
            if "_audio_end" in segments[i] and segments[i]["_audio_end"] is not None:
                segments[i]["_audio_end"] = round(max(0.0, float(segments[i]["_audio_end"]) - delta), 3)

        if translated_segments and 0 <= segment_index < len(translated_segments):
            t_seg = translated_segments[segment_index]
            t_seg["end"] = round(float(t_seg["end"]) - delta, 3)
            t_seg["extended_duration"] = 0.0
            t_seg.pop("time_warp_id", None)

            for i in range(segment_index + 1, len(translated_segments)):
                translated_segments[i]["start"] = round(max(0.0, float(translated_segments[i]["start"]) - delta), 3)
                translated_segments[i]["end"] = round(max(0.0, float(translated_segments[i]["end"]) - delta), 3)
                if "_audio_end" in translated_segments[i] and translated_segments[i]["_audio_end"] is not None:
                    translated_segments[i]["_audio_end"] = round(max(0.0, float(translated_segments[i]["_audio_end"]) - delta), 3)

        updated_warps = [
            w for w in (warps or [])
            if w.get("id") != warp_id and w.get("segment_index") != segment_index
        ]

        return delta, segments, translated_segments, updated_warps

    @staticmethod
    def timeline_to_media_time(timeline_time: float, warps: list[dict]) -> float:
        """Map timeline coordinate (with freezes) back to original media coordinate."""
        if not warps:
            return timeline_time
        sorted_warps = sorted(warps, key=lambda w: float(w.get("time", 0.0)))
        accumulated_shift = 0.0
        t = float(timeline_time)
        for w in sorted_warps:
            anchor = float(w.get("time", 0.0))
            dur = float(w.get("duration", 0.0))
            warp_start_on_timeline = anchor + accumulated_shift
            warp_end_on_timeline = warp_start_on_timeline + dur
            if t < warp_start_on_timeline:
                return max(0.0, t - accumulated_shift)
            if warp_start_on_timeline <= t < warp_end_on_timeline:
                return anchor
            accumulated_shift += dur
        return max(0.0, t - accumulated_shift)

    @staticmethod
    def media_to_timeline_time(media_time: float, warps: list[dict]) -> float:
        """Map original media coordinate to timeline coordinate."""
        if not warps:
            return media_time
        sorted_warps = sorted(warps, key=lambda w: float(w.get("time", 0.0)))
        accumulated_shift = 0.0
        m = float(media_time)
        for w in sorted_warps:
            anchor = float(w.get("time", 0.0))
            dur = float(w.get("duration", 0.0))
            if m < anchor:
                break
            accumulated_shift += dur
        return m + accumulated_shift

    @staticmethod
    def build_ffmpeg_freeze_filtergraph(
        video_stream: str,
        warps: list[dict],
        total_media_duration: float,
        fps: float = 30.0,
        audio_stream: str | None = None,
    ) -> tuple[str, str] | tuple[str, str, str | None]:
        """Build an FFmpeg complex filter chain that slices the video at each warp point,
        freezes the frame using 'loop', and concats them back together.
        If audio_stream is provided (e.g. '0:a'), inserts matching silence pads
        so audio never desyncs.
        Returns (filter_string, output_video_pad) if audio_stream is None,
        or (filter_string, output_video_pad, output_audio_pad) if audio_stream is given.
        """
        clean_v_stream = video_stream.strip("[]")
        if not warps:
            return ("", clean_v_stream, audio_stream.strip("[]") if audio_stream else None) if audio_stream else ("", clean_v_stream)

        sorted_warps = sorted(warps, key=lambda w: float(w.get("time", 0.0)))
        valid_warps = [w for w in sorted_warps if float(w.get("duration", 0.0)) > 0]
        if not valid_warps:
            return ("", clean_v_stream, audio_stream.strip("[]") if audio_stream else None) if audio_stream else ("", clean_v_stream)

        filter_parts = []
        concat_v_inputs = []
        concat_a_inputs = []
        curr_time = 0.0
        part_idx = 0
        has_audio = audio_stream is not None
        clean_a_stream = audio_stream.strip("[]") if audio_stream else ""

        for w in valid_warps:
            warp_time = min(float(w.get("time", 0.0)), total_media_duration)
            dur = float(w.get("duration", 0.0))
            loop_frames = max(1, int(round(dur * fps)))

            # Normal slice leading to freeze point
            if warp_time > curr_time:
                pad_name = f"v_norm_{part_idx}"
                filter_parts.append(
                    f"[{clean_v_stream}]trim=start={curr_time:.3f}:end={warp_time:.3f},setpts=PTS-STARTPTS[{pad_name}]"
                )
                concat_v_inputs.append(f"[{pad_name}]")

                if has_audio:
                    a_pad_name = f"a_norm_{part_idx}"
                    filter_parts.append(
                        f"[{clean_a_stream}]atrim=start={curr_time:.3f}:end={warp_time:.3f},asetpts=PTS-STARTPTS[{a_pad_name}]"
                    )
                    concat_a_inputs.append(f"[{a_pad_name}]")

                part_idx += 1

            # Freeze slice: hold single frame at warp_time
            freeze_pad = f"v_freeze_{part_idx}"
            frame_window = 1.0 / max(1.0, fps)
            trim_end = min(total_media_duration, warp_time + frame_window)
            filter_parts.append(
                f"[{clean_v_stream}]trim=start={warp_time:.3f}:end={trim_end:.3f},loop=loop={loop_frames}:size=1:start=0,setpts=PTS-STARTPTS[{freeze_pad}]"
            )
            concat_v_inputs.append(f"[{freeze_pad}]")

            if has_audio:
                a_pad_silence = f"a_freeze_{part_idx}"
                filter_parts.append(
                    f"anullsrc=r=44100:cl=stereo,atrim=end={dur:.3f},asetpts=PTS-STARTPTS[{a_pad_silence}]"
                )
                concat_a_inputs.append(f"[{a_pad_silence}]")

            part_idx += 1
            curr_time = warp_time

        # Remaining tail slice
        if curr_time < total_media_duration:
            tail_pad = f"v_tail_{part_idx}"
            filter_parts.append(
                f"[{clean_v_stream}]trim=start={curr_time:.3f}:end={total_media_duration:.3f},setpts=PTS-STARTPTS[{tail_pad}]"
            )
            concat_v_inputs.append(f"[{tail_pad}]")

            if has_audio:
                a_tail_pad = f"a_tail_{part_idx}"
                filter_parts.append(
                    f"[{clean_a_stream}]atrim=start={curr_time:.3f}:end={total_media_duration:.3f},asetpts=PTS-STARTPTS[{a_tail_pad}]"
                )
                concat_a_inputs.append(f"[{a_tail_pad}]")

            part_idx += 1

        concat_v_out = "v_warped"
        concat_cmd_v = f"{''.join(concat_v_inputs)}concat=n={len(concat_v_inputs)}:v=1:a=0[{concat_v_out}]"
        filter_parts.append(concat_cmd_v)

        if has_audio:
            concat_a_out = "a_warped"
            concat_cmd_a = f"{''.join(concat_a_inputs)}concat=n={len(concat_a_inputs)}:v=0:a=1[{concat_a_out}]"
            filter_parts.append(concat_cmd_a)
            return "; ".join(filter_parts), concat_v_out, concat_a_out

        return "; ".join(filter_parts), concat_v_out

    @staticmethod
    def ripple_shift_timeline_layers(timeline: Any, split_time: float, delta: float) -> None:
        """Non-destructively shift all timeline layers (on visual/effect tracks like Logo, Mask, Blur, Text)
        that occur at or after split_time by delta.
        """
        if timeline is None:
            return
        tracks = getattr(timeline, "tracks", None)
        if tracks is None:
            return
        split_s = round(float(split_time), 3)
        shift_s = round(float(delta), 3)
        if abs(shift_s) < 0.001:
            return

        for track in tracks:
            # Skip TS1 / subtitle tracks because their cues are synced from self.current_segments
            track_name = str(getattr(track, "name", "") or "").strip()
            track_type = str(getattr(getattr(track, "type", ""), "value", getattr(track, "type", ""))).lower()
            if track_name in ("TS1", "S1") or track_type in ("subtitle", "dub_subtitle", "video", "audio"):
                continue

            for layer in getattr(track, "layers", []):
                start = float(getattr(layer, "start", 0.0) or 0.0)
                end = float(getattr(layer, "end", 0.0) or 0.0)
                if start >= split_s - 0.005:
                    layer.start = max(0.0, round(start + shift_s, 3))
                    layer.end = max(layer.start + 0.1, round(end + shift_s, 3))
                elif start < split_s < end:
                    layer.end = max(start + 0.1, round(end + shift_s, 3))
