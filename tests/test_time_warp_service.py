import unittest
from app.services.time_warp_service import TimeWarpService


class TestTimeWarpService(unittest.TestCase):
    def test_create_time_warp_slow_and_freeze(self):
        freeze_warp = TimeWarpService.create_time_warp(
            time=5.0,
            duration=1.0,
            warp_type="freeze",
            segment_index=1,
            note="freeze test",
        )
        self.assertEqual(freeze_warp["type"], "freeze")
        self.assertEqual(freeze_warp["time"], 5.0)
        self.assertEqual(freeze_warp["duration"], 1.0)
        self.assertEqual(freeze_warp["speed"], 1.0)

        slow_warp = TimeWarpService.create_time_warp(
            time=2.0,
            duration=0.5,
            warp_type="slow",
            segment_index=2,
            speed=0.8,
            media_start=2.0,
            media_end=4.0,
            note="slow test",
        )
        self.assertEqual(slow_warp["type"], "slow")
        self.assertEqual(slow_warp["speed"], 0.8)
        self.assertEqual(slow_warp["media_start"], 2.0)
        self.assertEqual(slow_warp["media_end"], 4.0)

    def test_apply_segment_extension_slow(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "seg 0"},
            {"start": 2.0, "end": 4.0, "text": "seg 1"},
            {"start": 4.0, "end": 6.0, "text": "seg 2"},
        ]
        # Seg 1 orig_dur = 2.0s. Add 0.5s => new_dur = 2.5s => speed = 2.0 / 2.5 = 0.8
        warp, updated_base, _ = TimeWarpService.apply_segment_extension(
            segments, segment_index=1, added_duration=0.5, warp_type="slow"
        )
        self.assertEqual(warp["type"], "slow")
        self.assertAlmostEqual(warp["speed"], 0.8, places=2)
        self.assertEqual(warp["media_start"], 2.0)
        self.assertEqual(warp["media_end"], 4.0)

        seg1 = updated_base[1]
        self.assertEqual(seg1["end"], 4.5)
        self.assertEqual(seg1["extended_duration"], 0.5)
        self.assertEqual(seg1.get("warp_type"), "slow")
        self.assertAlmostEqual(seg1.get("warp_speed"), 0.8, places=2)

        seg2 = updated_base[2]
        self.assertEqual(seg2["start"], 4.5)
        self.assertEqual(seg2["end"], 6.5)

    def test_timeline_to_media_time_slow(self):
        # 1 slow warp: orig [2.0, 4.0] (dur 2.0) extended by 0.5s -> timeline [2.0, 4.5] (speed 0.8)
        warps = [
            {
                "id": "warp_1",
                "type": "slow",
                "time": 2.0,
                "media_start": 2.0,
                "media_end": 4.0,
                "duration": 0.5,
                "speed": 0.8,
            }
        ]
        # Before warp
        self.assertAlmostEqual(TimeWarpService.timeline_to_media_time(1.0, warps), 1.0, places=3)
        # Start of warp
        self.assertAlmostEqual(TimeWarpService.timeline_to_media_time(2.0, warps), 2.0, places=3)
        # Mid-warp: timeline t = 3.25 (offset 1.25s * 0.8 = 1.0s) => media = 3.0
        self.assertAlmostEqual(TimeWarpService.timeline_to_media_time(3.25, warps), 3.0, places=3)
        # End of warp: timeline t = 4.5 (offset 2.5s * 0.8 = 2.0s) => media = 4.0
        self.assertAlmostEqual(TimeWarpService.timeline_to_media_time(4.5, warps), 4.0, places=3)
        # After warp: timeline t = 5.5 => media = 5.5 - 0.5 = 5.0
        self.assertAlmostEqual(TimeWarpService.timeline_to_media_time(5.5, warps), 5.0, places=3)

    def test_media_to_timeline_time_slow(self):
        warps = [
            {
                "id": "warp_1",
                "type": "slow",
                "time": 2.0,
                "media_start": 2.0,
                "media_end": 4.0,
                "duration": 0.5,
                "speed": 0.8,
            }
        ]
        # Before warp
        self.assertAlmostEqual(TimeWarpService.media_to_timeline_time(1.0, warps), 1.0, places=3)
        # Start of warp
        self.assertAlmostEqual(TimeWarpService.media_to_timeline_time(2.0, warps), 2.0, places=3)
        # Mid-warp: media m = 3.0 (offset 1.0s / 0.8 = 1.25s) => timeline = 3.25
        self.assertAlmostEqual(TimeWarpService.media_to_timeline_time(3.0, warps), 3.25, places=3)
        # End of warp: media m = 4.0 => timeline = 4.5
        self.assertAlmostEqual(TimeWarpService.media_to_timeline_time(4.0, warps), 4.5, places=3)
        # After warp: media m = 5.0 => timeline = 5.0 + 0.5 = 5.5
        self.assertAlmostEqual(TimeWarpService.media_to_timeline_time(5.0, warps), 5.5, places=3)

    def test_build_ffmpeg_timewarp_filtergraph_slow_and_freeze(self):
        warps = [
            {
                "id": "warp_1",
                "type": "slow",
                "time": 2.0,
                "media_start": 2.0,
                "media_end": 4.0,
                "duration": 0.5,
                "speed": 0.8,
            },
            {
                "id": "warp_2",
                "type": "freeze",
                "time": 6.0,
                "duration": 1.0,
                "speed": 1.0,
            }
        ]
        filter_str, v_out, a_out = TimeWarpService.build_ffmpeg_timewarp_filtergraph(
            video_stream="0:v",
            warps=warps,
            total_media_duration=10.0,
            fps=30.0,
            audio_stream="0:a",
        )
        self.assertIn("v_warped", v_out)
        self.assertIn("a_warped", a_out)
        # Slow slice video filter should use setpts with 1/speed = 1.25
        self.assertTrue("1.250" in filter_str or "1.25*" in filter_str or "1/0.8" in filter_str)
        # Slow slice audio filter should use atempo
        self.assertIn("atempo=0.8", filter_str)
        # Freeze slice should use loop
        self.assertIn("loop=", filter_str)

    def test_remove_segment_extension_slow(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "seg 0"},
            {"start": 2.0, "end": 4.0, "text": "seg 1"},
            {"start": 4.0, "end": 6.0, "text": "seg 2"},
        ]
        warp, updated_base, _ = TimeWarpService.apply_segment_extension(
            segments, segment_index=1, added_duration=0.5, warp_type="slow"
        )
        delta, reverted_base, _, updated_warps = TimeWarpService.remove_segment_extension(
            updated_base, segment_index=1, warps=[warp]
        )
        self.assertEqual(delta, 0.5)
        self.assertEqual(reverted_base[1]["end"], 4.0)
    def test_slice_time_warps_for_window_empty(self):
        m_start, m_dur, local_warps = TimeWarpService.slice_time_warps_for_window([], 10.0, 5.0)
        self.assertEqual(m_start, 10.0)
        self.assertEqual(m_dur, 5.0)
        self.assertEqual(local_warps, [])

    def test_slice_time_warps_for_window_slow(self):
        # 1 slow warp: orig [2.0, 4.0] extended by 2.0s -> timeline [2.0, 6.0] (speed 0.5)
        warps = [
            {
                "id": "warp_slow",
                "type": "slow",
                "time": 4.0,
                "media_start": 2.0,
                "media_end": 4.0,
                "duration": 2.0,
                "speed": 0.5,
            }
        ]
        # Case 1: window [1.0, 6.0] (starts before warp, covers full slow segment)
        m_start, m_dur, local = TimeWarpService.slice_time_warps_for_window(warps, 1.0, 5.0)
        self.assertAlmostEqual(m_start, 1.0, places=2)
        self.assertAlmostEqual(m_dur, 3.0, places=2)
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0]["type"], "slow")
        self.assertAlmostEqual(local[0]["media_start"], 1.0, places=2)
        self.assertAlmostEqual(local[0]["media_end"], 3.0, places=2)
        self.assertAlmostEqual(local[0]["speed"], 0.5, places=2)
        self.assertAlmostEqual(local[0]["duration"], 2.0, places=2)

        # Case 2: window [3.0, 8.0] (starts in the middle of slow segment [3.0, 6.0], ends after warp)
        m_start, m_dur, local = TimeWarpService.slice_time_warps_for_window(warps, 3.0, 5.0)
        # timeline 3.0 is mid-warp: 2.0 + (3.0 - 2.0) * 0.5 = 2.5
        self.assertAlmostEqual(m_start, 2.5, places=2)
        # timeline 8.0 is after warp: 8.0 - 2.0 = 6.0 => dur = 6.0 - 2.5 = 3.5
        self.assertAlmostEqual(m_dur, 3.5, places=2)
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0]["type"], "slow")
        self.assertAlmostEqual(local[0]["media_start"], 0.0, places=2)
        self.assertAlmostEqual(local[0]["media_end"], 1.5, places=2)
        self.assertAlmostEqual(local[0]["speed"], 0.5, places=2)
        self.assertAlmostEqual(local[0]["duration"], 1.5, places=2)

        # Case 3: window [7.0, 12.0] (entirely after warp)
        m_start, m_dur, local = TimeWarpService.slice_time_warps_for_window(warps, 7.0, 5.0)
        self.assertAlmostEqual(m_start, 5.0, places=2)
        self.assertAlmostEqual(m_dur, 5.0, places=2)
        self.assertEqual(local, [])

    def test_slice_time_warps_for_window_freeze(self):
        # 1 freeze warp: anchor at 3.0, duration 2.0s -> timeline [3.0, 5.0] frozen
        warps = [
            {
                "id": "warp_freeze",
                "type": "freeze",
                "time": 3.0,
                "duration": 2.0,
                "speed": 1.0,
            }
        ]
        # Case 1: window [2.0, 7.0] (covers normal [2, 3], freeze [3, 5], normal [5, 7])
        m_start, m_dur, local = TimeWarpService.slice_time_warps_for_window(warps, 2.0, 5.0)
        self.assertAlmostEqual(m_start, 2.0, places=2)
        self.assertAlmostEqual(m_dur, 3.0, places=2)
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0]["type"], "freeze")
        self.assertAlmostEqual(local[0]["time"], 1.0, places=2)
        self.assertAlmostEqual(local[0]["duration"], 2.0, places=2)

    def test_map_segments_to_media_time_slow(self):
        # orig [3.4, 5.4] extended by 1.17s -> timeline [3.4, 6.57] (speed 0.631)
        warps = [
            {
                "id": "warp_ae",
                "type": "slow",
                "time": 5.4,
                "media_start": 3.4,
                "media_end": 5.4,
                "duration": 1.17,
                "speed": 0.631,
            }
        ]
        segments = [
            {"start": 0.0, "end": 3.4, "text": "seg 0"},
            {"start": 3.4, "end": 6.57, "text": "seg 1", "words": [{"start": 3.4, "end": 6.57, "word": "slow"}]},
            {"start": 6.57, "end": 9.12, "text": "seg 2"},
        ]
        mapped = TimeWarpService.map_segments_to_media_time(segments, warps)
        self.assertEqual(len(mapped), 3)
        # Segment 0: unchanged
        self.assertAlmostEqual(mapped[0]["start"], 0.0, places=2)
        self.assertAlmostEqual(mapped[0]["end"], 3.4, places=2)
        # Segment 1: media bounds [3.4, 5.4]
        self.assertAlmostEqual(mapped[1]["start"], 3.4, places=2)
        self.assertAlmostEqual(mapped[1]["end"], 5.4, places=2)
        self.assertAlmostEqual(mapped[1]["words"][0]["start"], 3.4, places=2)
        self.assertAlmostEqual(mapped[1]["words"][0]["end"], 5.4, places=2)
        # Segment 2: starts at media 5.4! NOT delayed to 6.57
        self.assertAlmostEqual(mapped[2]["start"], 5.4, places=2)
        self.assertAlmostEqual(mapped[2]["end"], 7.95, places=2)


if __name__ == "__main__":
    unittest.main()
