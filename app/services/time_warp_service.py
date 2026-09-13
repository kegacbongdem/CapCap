from __future__ import annotations

import uuid
from typing import Any


class TimeWarpService:
    """Service to manage non-destructive video time-warps (freeze frame / slow motion / extension)
    and timeline ripple-shifting.
    """

    @staticmethod
    def create_time_warp(
        time: float,
        duration: float,
        warp_type: str = "freeze",
        segment_index: int | None = None,
        speed: float = 1.0,
        media_start: float | None = None,
        media_end: float | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        m_start = round(float(media_start if media_start is not None else time), 3)
        m_end = round(float(media_end if media_end is not None else time), 3)
        speed_val = round(float(speed or 1.0), 3)
        if warp_type == "freeze":
            speed_val = 1.0
        return {
            "id": f"warp_{uuid.uuid4().hex[:8]}",
            "time": round(float(time), 3),
            "duration": round(float(duration), 3),
            "type": warp_type,
            "segment_index": segment_index,
            "speed": speed_val,
            "media_start": m_start,
            "media_end": m_end,
            "note": note,
        }

    @staticmethod
    def apply_segment_extension(
        segments: list[dict],
        segment_index: int,
        added_duration: float,
        translated_segments: list[dict] | None = None,
        warp_type: str = "freeze",
    ) -> tuple[dict, list[dict], list[dict] | None]:
        """Extend a segment by added_duration (+Δt) and ripple shift all subsequent segments by +Δt.
        warp_type can be 'freeze' (hold frame at end) or 'slow' (stretch video duration smoothly).
        Returns (new_warp, updated_segments, updated_translated_segments).
        """
        if not segments and translated_segments:
            segments = translated_segments
            translated_segments = None

        if segment_index < 0 or segment_index >= len(segments):
            raise IndexError(f"Segment index {segment_index} out of range [0, {len(segments)})")

        target_seg = segments[segment_index]
        orig_start = float(target_seg.get("start", 0.0))
        orig_end = float(target_seg.get("end", 0.0))
        delta = round(float(added_duration), 3)
        if delta <= 0:
            raise ValueError(f"Added duration must be positive, got {delta}")

        prev_ext = float(target_seg.get("extended_duration", 0.0) or 0.0)
        total_ext = round(prev_ext + delta, 3)
        prior_shift = sum(
            float(segments[i].get("extended_duration", 0.0) or 0.0)
            for i in range(segment_index)
        ) + prev_ext

        # Media coordinates on original file (accounting for prior extensions)
        media_start = round(max(0.0, orig_start - (prior_shift - prev_ext)), 3)
        warp_time = round(max(0.0, orig_end - prior_shift), 3)
        media_end = warp_time

        orig_dur = max(0.001, media_end - media_start)
        new_dur = orig_dur + total_ext
        speed = round(orig_dur / new_dur, 3) if warp_type == "slow" else 1.0

        warp = TimeWarpService.create_time_warp(
            time=warp_time,
            duration=total_ext,
            warp_type=warp_type,
            segment_index=segment_index,
            speed=speed,
            media_start=media_start,
            media_end=media_end,
        )

        target_seg["extended_duration"] = total_ext
        target_seg["end"] = round(float(target_seg["end"]) + delta, 3)
        target_seg["time_warp_id"] = warp["id"]
        target_seg["warp_type"] = warp_type
        target_seg["warp_speed"] = speed

        for i in range(segment_index + 1, len(segments)):
            segments[i]["start"] = round(float(segments[i]["start"]) + delta, 3)
            segments[i]["end"] = round(float(segments[i]["end"]) + delta, 3)
            if "_audio_end" in segments[i] and segments[i]["_audio_end"] is not None:
                segments[i]["_audio_end"] = round(float(segments[i]["_audio_end"]) + delta, 3)
            if isinstance(segments[i].get("metadata"), dict) and "_audio_end" in segments[i]["metadata"]:
                try:
                    segments[i]["metadata"]["_audio_end"] = round(float(segments[i]["metadata"]["_audio_end"]) + delta, 3)
                except (TypeError, ValueError):
                    pass

        if translated_segments and 0 <= segment_index < len(translated_segments):
            t_seg = translated_segments[segment_index]
            t_prev_ext = float(t_seg.get("extended_duration", 0.0) or 0.0)
            t_seg["extended_duration"] = round(t_prev_ext + delta, 3)
            t_seg["end"] = round(float(t_seg["end"]) + delta, 3)
            t_seg["time_warp_id"] = warp["id"]
            t_seg["warp_type"] = warp_type
            t_seg["warp_speed"] = speed

            for i in range(segment_index + 1, len(translated_segments)):
                translated_segments[i]["start"] = round(float(translated_segments[i]["start"]) + delta, 3)
                translated_segments[i]["end"] = round(float(translated_segments[i]["end"]) + delta, 3)
                if "_audio_end" in translated_segments[i] and translated_segments[i]["_audio_end"] is not None:
                    translated_segments[i]["_audio_end"] = round(float(translated_segments[i]["_audio_end"]) + delta, 3)
                if isinstance(translated_segments[i].get("metadata"), dict) and "_audio_end" in translated_segments[i]["metadata"]:
                    try:
                        translated_segments[i]["metadata"]["_audio_end"] = round(float(translated_segments[i]["metadata"]["_audio_end"]) + delta, 3)
                    except (TypeError, ValueError):
                        pass

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

        target_seg = segments[segment_index] if (segments and 0 <= segment_index < len(segments)) else {}
        delta = float(target_seg.get("extended_duration", 0.0) or 0.0)
        if delta <= 0 and translated_segments and 0 <= segment_index < len(translated_segments):
            delta = float(translated_segments[segment_index].get("extended_duration", 0.0) or 0.0)
        if delta <= 0:
            for w in (warps or []):
                if w.get("segment_index") == segment_index:
                    delta = float(w.get("duration", 0.0) or 0.0)
                    break
        if delta <= 0:
            return 0.0, segments, translated_segments, warps or []

        warp_id = target_seg.get("time_warp_id", "")
        if not warp_id and translated_segments and 0 <= segment_index < len(translated_segments):
            warp_id = translated_segments[segment_index].get("time_warp_id", "")

        if segments and 0 <= segment_index < len(segments):
            segments[segment_index]["end"] = round(float(segments[segment_index]["end"]) - delta, 3)
            segments[segment_index]["extended_duration"] = 0.0
            segments[segment_index].pop("time_warp_id", None)
            segments[segment_index].pop("warp_type", None)
            segments[segment_index].pop("warp_speed", None)

            for i in range(segment_index + 1, len(segments)):
                segments[i]["start"] = round(max(0.0, float(segments[i]["start"]) - delta), 3)
                segments[i]["end"] = round(max(0.0, float(segments[i]["end"]) - delta), 3)
                if "_audio_end" in segments[i] and segments[i]["_audio_end"] is not None:
                    segments[i]["_audio_end"] = round(max(0.0, float(segments[i]["_audio_end"]) - delta), 3)
                if isinstance(segments[i].get("metadata"), dict) and "_audio_end" in segments[i]["metadata"]:
                    try:
                        segments[i]["metadata"]["_audio_end"] = round(max(0.0, float(segments[i]["metadata"]["_audio_end"]) - delta), 3)
                    except (TypeError, ValueError):
                        pass

        if translated_segments and 0 <= segment_index < len(translated_segments):
            t_seg = translated_segments[segment_index]
            t_seg["end"] = round(float(t_seg["end"]) - delta, 3)
            t_seg["extended_duration"] = 0.0
            t_seg.pop("time_warp_id", None)
            t_seg.pop("warp_type", None)
            t_seg.pop("warp_speed", None)

            for i in range(segment_index + 1, len(translated_segments)):
                translated_segments[i]["start"] = round(max(0.0, float(translated_segments[i]["start"]) - delta), 3)
                translated_segments[i]["end"] = round(max(0.0, float(translated_segments[i]["end"]) - delta), 3)
                if "_audio_end" in translated_segments[i] and translated_segments[i]["_audio_end"] is not None:
                    translated_segments[i]["_audio_end"] = round(max(0.0, float(translated_segments[i]["_audio_end"]) - delta), 3)
                if isinstance(translated_segments[i].get("metadata"), dict) and "_audio_end" in translated_segments[i]["metadata"]:
                    try:
                        translated_segments[i]["metadata"]["_audio_end"] = round(max(0.0, float(translated_segments[i]["metadata"]["_audio_end"]) - delta), 3)
                    except (TypeError, ValueError):
                        pass

        updated_warps = [
            w for w in (warps or [])
            if (not warp_id or w.get("id") != warp_id) and w.get("segment_index") != segment_index
        ]

        return delta, segments, translated_segments, updated_warps

    @staticmethod
    def timeline_to_media_time(timeline_time: float, warps: list[dict]) -> float:
        """Map timeline coordinate (with freezes/slow) back to original media coordinate."""
        if not warps:
            return timeline_time
        sorted_warps = sorted(warps, key=lambda w: float(w.get("media_start", w.get("time", 0.0))))
        accumulated_shift = 0.0
        t = float(timeline_time)
        for w in sorted_warps:
            w_type = w.get("type", "freeze")
            dur = float(w.get("duration", 0.0))
            if w_type == "slow":
                m_start = float(w.get("media_start", w.get("time", 0.0)))
                m_end = float(w.get("media_end", m_start))
                m_dur = max(0.001, m_end - m_start)
                warp_start_on_timeline = m_start + accumulated_shift
                warp_end_on_timeline = warp_start_on_timeline + m_dur + dur
                if t < warp_start_on_timeline:
                    return max(0.0, t - accumulated_shift)
                if warp_start_on_timeline <= t <= warp_end_on_timeline:
                    speed = float(w.get("speed", m_dur / (m_dur + dur)))
                    return m_start + (t - warp_start_on_timeline) * speed
                accumulated_shift += dur
            else:
                anchor = float(w.get("time", 0.0))
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
        sorted_warps = sorted(warps, key=lambda w: float(w.get("media_start", w.get("time", 0.0))))
        accumulated_shift = 0.0
        m = float(media_time)
        for w in sorted_warps:
            w_type = w.get("type", "freeze")
            dur = float(w.get("duration", 0.0))
            if w_type == "slow":
                m_start = float(w.get("media_start", w.get("time", 0.0)))
                m_end = float(w.get("media_end", m_start))
                m_dur = max(0.001, m_end - m_start)
                if m < m_start:
                    break
                if m_start <= m <= m_end:
                    speed = float(w.get("speed", m_dur / (m_dur + dur)))
                    warp_start_on_timeline = m_start + accumulated_shift
                    return warp_start_on_timeline + (m - m_start) / speed
                accumulated_shift += dur
            else:
                anchor = float(w.get("time", 0.0))
                if m < anchor:
                    break
                accumulated_shift += dur
        return m + accumulated_shift

    @staticmethod
    def map_segments_to_media_time(segments: list[dict], warps: list[dict]) -> list[dict]:
        """Convert a list of timeline-coordinate segments to media-coordinate segments.
        Used for live player subtitle overlays when playing raw (unwarped) media.
        """
        if not warps or not segments:
            return list(segments or [])
        mapped = []
        for s in segments:
            new_s = dict(s)
            try:
                tl_start = float(s.get("start", 0.0))
                tl_end = float(s.get("end", 0.0))
                new_s["start"] = round(TimeWarpService.timeline_to_media_time(tl_start, warps), 3)
                new_s["end"] = round(TimeWarpService.timeline_to_media_time(tl_end, warps), 3)
            except (TypeError, ValueError):
                pass
            if "words" in s and isinstance(s["words"], list):
                new_words = []
                for w in s["words"]:
                    new_w = dict(w)
                    try:
                        w_start = float(w.get("start", 0.0))
                        w_end = float(w.get("end", 0.0))
                        new_w["start"] = round(TimeWarpService.timeline_to_media_time(w_start, warps), 3)
                        new_w["end"] = round(TimeWarpService.timeline_to_media_time(w_end, warps), 3)
                    except (TypeError, ValueError):
                        pass
                    new_words.append(new_w)
                new_s["words"] = new_words
            mapped.append(new_s)
        return mapped

    @staticmethod
    def _format_atempo_chain(speed: float) -> str:
        s = float(speed)
        if s <= 0:
            return "atempo=1.0"
        parts = []
        while s < 0.5:
            parts.append("atempo=0.5")
            s /= 0.5
        while s > 2.0:
            parts.append("atempo=2.0")
            s /= 2.0
        parts.append(f"atempo={s:.3f}")
        return ",".join(parts)

    @staticmethod
    def slice_time_warps_for_window(
        warps: list[dict],
        start_timeline_s: float,
        duration_s: float,
    ) -> tuple[float, float, list[dict]]:
        """Slice project time warps for a specific timeline window [T_start, T_start + duration].

        Returns:
            (media_start_s, media_duration_s, local_warps)
        where:
            - media_start_s: start time to slice from the source media file.
            - media_duration_s: duration of source media to slice.
            - local_warps: list of warps with coordinates relative to media_start_s,
              ready for build_ffmpeg_timewarp_filtergraph.
        """
        t0 = max(0.0, float(start_timeline_s))
        dur = max(0.1, float(duration_s))
        t1 = t0 + dur

        if not warps:
            return t0, dur, []

        m0 = TimeWarpService.timeline_to_media_time(t0, warps)
        m1 = TimeWarpService.timeline_to_media_time(t1, warps)

        sorted_warps = sorted(warps, key=lambda w: float(w.get("media_start", w.get("time", 0.0))))
        local_warps: list[dict] = []

        for w in sorted_warps:
            w_type = w.get("type", "freeze")
            w_dur = float(w.get("duration", 0.0))
            if w_dur <= 0:
                continue

            if w_type == "slow":
                m_start = float(w.get("media_start", w.get("time", 0.0)))
                m_end = float(w.get("media_end", m_start))
                speed = float(w.get("speed", 1.0))
                if speed <= 0:
                    speed = 1.0

                w_t_start = TimeWarpService.media_to_timeline_time(m_start, warps)
                w_t_end = TimeWarpService.media_to_timeline_time(m_end, warps)

                overlap_t_start = max(t0, w_t_start)
                overlap_t_end = min(t1, w_t_end)

                if overlap_t_start < overlap_t_end:
                    overlap_m_start = TimeWarpService.timeline_to_media_time(overlap_t_start, warps)
                    overlap_m_end = TimeWarpService.timeline_to_media_time(overlap_t_end, warps)
                    slice_t_dur = overlap_t_end - overlap_t_start
                    slice_m_dur = overlap_m_end - overlap_m_start
                    added_dur = max(0.001, slice_t_dur - slice_m_dur)

                    loc_m_start = max(0.0, overlap_m_start - m0)
                    loc_m_end = max(loc_m_start, overlap_m_end - m0)

                    local_warps.append({
                        "id": w.get("id"),
                        "type": "slow",
                        "media_start": round(loc_m_start, 3),
                        "media_end": round(loc_m_end, 3),
                        "time": round(loc_m_end, 3),
                        "speed": round(speed, 3),
                        "duration": round(added_dur, 3),
                    })
            else:  # freeze
                anchor = float(w.get("time", 0.0))
                w_t_start = TimeWarpService.media_to_timeline_time(anchor, warps)
                w_t_end = w_t_start + w_dur

                overlap_t_start = max(t0, w_t_start)
                overlap_t_end = min(t1, w_t_end)

                if overlap_t_start < overlap_t_end:
                    freeze_dur = overlap_t_end - overlap_t_start
                    loc_anchor = max(0.0, anchor - m0)

                    local_warps.append({
                        "id": w.get("id"),
                        "type": "freeze",
                        "time": round(loc_anchor, 3),
                        "duration": round(freeze_dur, 3),
                        "speed": 1.0,
                    })

        if m1 > m0:
            media_dur = round(m1 - m0, 3)
        else:
            media_dur = 0.5

        return round(m0, 3), media_dur, local_warps

    @staticmethod
    def build_ffmpeg_timewarp_filtergraph(
        video_stream: str,
        warps: list[dict],
        total_media_duration: float,
        fps: float = 30.0,
        audio_stream: str | None = None,
    ) -> tuple[str, str] | tuple[str, str, str | None]:
        """Build an FFmpeg complex filter chain that slices the video at each warp point,
        freezes the frame using 'loop' or slows it down using 'setpts'/'atempo',
        and concats them back together.
        If audio_stream is provided (e.g. '0:a'), inserts matching silence pads / atempo
        so audio never desyncs.
        Returns (filter_string, output_video_pad) if audio_stream is None,
        or (filter_string, output_video_pad, output_audio_pad) if audio_stream is given.
        """
        clean_v_stream = video_stream.strip("[]")
        if not warps:
            return ("", clean_v_stream, audio_stream.strip("[]") if audio_stream else None) if audio_stream else ("", clean_v_stream)

        sorted_warps = sorted(warps, key=lambda w: float(w.get("media_start", w.get("time", 0.0))))
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
            w_type = w.get("type", "freeze")
            dur = float(w.get("duration", 0.0))

            if w_type == "slow":
                m_start = min(float(w.get("media_start", w.get("time", 0.0))), total_media_duration)
                m_end = min(float(w.get("media_end", m_start)), total_media_duration)
                speed = float(w.get("speed", 1.0))
                if speed <= 0:
                    speed = 1.0

                # Normal slice leading to slow segment start
                if m_start > curr_time:
                    pad_name = f"v_norm_{part_idx}"
                    filter_parts.append(
                        f"[{clean_v_stream}]trim=start={curr_time:.3f}:end={m_start:.3f},setpts=PTS-STARTPTS[{pad_name}]"
                    )
                    concat_v_inputs.append(f"[{pad_name}]")
                    if has_audio:
                        a_pad_name = f"a_norm_{part_idx}"
                        filter_parts.append(
                            f"[{clean_a_stream}]atrim=start={curr_time:.3f}:end={m_start:.3f},asetpts=PTS-STARTPTS[{a_pad_name}]"
                        )
                        concat_a_inputs.append(f"[{a_pad_name}]")
                    part_idx += 1

                # Slow slice: trim [m_start, m_end], setpts=(1/speed)*(PTS-STARTPTS)
                slow_pad = f"v_slow_{part_idx}"
                pts_factor = round(1.0 / speed, 3)
                filter_parts.append(
                    f"[{clean_v_stream}]trim=start={m_start:.3f}:end={m_end:.3f},setpts={pts_factor:.3f}*(PTS-STARTPTS)[{slow_pad}]"
                )
                concat_v_inputs.append(f"[{slow_pad}]")

                if has_audio:
                    a_slow_pad = f"a_slow_{part_idx}"
                    atempo_chain = TimeWarpService._format_atempo_chain(speed)
                    filter_parts.append(
                        f"[{clean_a_stream}]atrim=start={m_start:.3f}:end={m_end:.3f},asetpts=PTS-STARTPTS,{atempo_chain}[{a_slow_pad}]"
                    )
                    concat_a_inputs.append(f"[{a_slow_pad}]")

                part_idx += 1
                curr_time = m_end

            else:  # freeze
                warp_time = min(float(w.get("time", 0.0)), total_media_duration)
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
                trim_end = warp_time + frame_window
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
                curr_time = warp_time if warp_time < total_media_duration else total_media_duration

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

    # Backward-compatible alias
    build_ffmpeg_freeze_filtergraph = build_ffmpeg_timewarp_filtergraph

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
                    layer.end = max(layer.start + 0.1, round(end + shift_s, 3))
