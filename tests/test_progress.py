"""
Unit tests for progress calculation and time formatting utilities.
"""

import unittest
from utils.formatting import format_queue_badge, format_time, render_progress


class TestFormatting(unittest.TestCase):
    def test_format_time_basic(self):
        self.assertEqual(format_time(0), "0:00")
        self.assertEqual(format_time(59), "0:59")
        self.assertEqual(format_time(60), "1:00")
        self.assertEqual(format_time(186), "3:06")
        self.assertEqual(format_time(145), "2:25")
        self.assertEqual(format_time(3600), "1:00:00")
        self.assertEqual(format_time(3665), "1:01:05")

    def test_format_time_edge_cases(self):
        self.assertEqual(format_time(None), "0:00")
        self.assertEqual(format_time(-10), "0:00")
        self.assertEqual(format_time(0.4), "0:00")
        self.assertEqual(format_time(185.8), "3:06")

    def test_render_progress_format(self):
        result = render_progress(145, 186)
        # Should include start time, end time, and indicator character
        self.assertTrue(result.startswith("2:25"))
        self.assertTrue(result.endswith("3:06"))
        self.assertIn("●", result)
        self.assertIn("━", result)

    def test_render_progress_boundaries(self):
        # Start of song
        start_result = render_progress(0, 186, bar_length=10)
        self.assertTrue(start_result.startswith("0:00 ●━━━━━━━━━ 3:06"))

        # End of song
        end_result = render_progress(186, 186, bar_length=10)
        self.assertTrue(end_result.startswith("3:06 ━━━━━━━━━● 3:06"))

        # Over-duration clamp
        over_result = render_progress(200, 186, bar_length=10)
        self.assertTrue(over_result.startswith("3:06 ━━━━━━━━━● 3:06"))

    def test_render_progress_zero_total(self):
        # Stream or unknown duration
        zero_result = render_progress(0, 0, bar_length=16)
        self.assertEqual(zero_result, "0:00 ━━━━━━━━━━━━━━━━ 0:00")

    def test_format_queue_badge(self):
        self.assertEqual(format_queue_badge(0), "☷ Queue · 0")
        self.assertEqual(format_queue_badge(5), "☷ Queue · 5")
        self.assertEqual(format_queue_badge(-1), "☷ Queue · 0")


if __name__ == "__main__":
    unittest.main()
