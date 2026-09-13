import importlib.util
import os
import sys
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (PROJECT_ROOT, os.path.join(PROJECT_ROOT, "app")):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication([])

timeline_path = os.path.join(PROJECT_ROOT, "ui", "views", "editor", "timeline.py")
spec = importlib.util.spec_from_file_location("editor_timeline_mod", timeline_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
EditorTimeline = mod.EditorTimeline


class TestTimelineTimeWarpVisuals(unittest.TestCase):
    def setUp(self):
        self.timeline = EditorTimeline()
        self.timeline.set_duration(20.0)

    def test_set_video_time_warps(self):
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
        self.timeline.set_video_time_warps(warps)
        self.assertEqual(len(self.timeline.get_video_time_warps()), 2)
        self.assertEqual(self.timeline.get_video_time_warps()[0]["type"], "slow")
        self.assertEqual(self.timeline.get_video_time_warps()[1]["type"], "freeze")

    def test_compute_warp_spans(self):
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
        self.timeline.set_video_time_warps(warps)
        spans = self.timeline.compute_warp_timeline_spans()
        self.assertEqual(len(spans), 2)

        # First span (slow): spans timeline [2.0, 4.5]
        span0 = spans[0]
        self.assertEqual(span0["type"], "slow")
        self.assertAlmostEqual(span0["t_start"], 2.0, places=3)
        self.assertAlmostEqual(span0["t_end"], 4.5, places=3)
        self.assertIn("🐢", span0["badge_text"])
        self.assertIn("0.80x", span0["badge_text"])

        # Second span (freeze): media 6.0 was shifted by prior slow warp (+0.5s)
        # So freeze is at timeline 6.5, duration 1.0 => spans [6.5, 7.5]
        span1 = spans[1]
        self.assertEqual(span1["type"], "freeze")
        self.assertAlmostEqual(span1["t_start"], 6.5, places=3)
        self.assertAlmostEqual(span1["t_end"], 7.5, places=3)
        self.assertIn("⏸", span1["badge_text"])
        self.assertIn("+1.0s", span1["badge_text"])


if __name__ == "__main__":
    unittest.main()
