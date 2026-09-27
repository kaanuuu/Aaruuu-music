"""
Unit tests for per-chat TrackQueue and capacity management.
"""

import unittest
from player.models import Track
from player.queue import TrackQueue


class TestTrackQueue(unittest.TestCase):
    def setUp(self):
        self.queue = TrackQueue(max_size=5)
        self.track1 = Track(
            track_id="tr1",
            title="Song 1",
            artist="Artist 1",
            duration=180,
            thumbnail="https://example.com/1.jpg",
            source_url="https://youtube.com/watch?v=1",
            requester_user_id=101,
            requester_name="Alice",
        )
        self.track2 = Track(
            track_id="tr2",
            title="Song 2",
            artist="Artist 2",
            duration=210,
            thumbnail="https://example.com/2.jpg",
            source_url="https://youtube.com/watch?v=2",
            requester_user_id=102,
            requester_name="Bob",
        )

    def test_queue_initial_empty(self):
        self.assertEqual(len(self.queue), 0)
        self.assertIsNone(self.queue.pop())
        self.assertIsNone(self.queue.peek())

    def test_queue_add_and_fifo_pop(self):
        self.assertTrue(self.queue.add(self.track1))
        self.assertTrue(self.queue.add(self.track2))
        self.assertEqual(len(self.queue), 2)

        popped = self.queue.pop()
        self.assertIsNotNone(popped)
        self.assertEqual(popped.track_id, "tr1")
        self.assertEqual(len(self.queue), 1)

        popped2 = self.queue.pop()
        self.assertEqual(popped2.track_id, "tr2")
        self.assertEqual(len(self.queue), 0)

    def test_queue_undo_last(self):
        self.queue.add(self.track1)
        self.queue.add(self.track2)
        undone = self.queue.undo_last()
        self.assertEqual(undone.track_id, "tr2")
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(self.queue.peek().track_id, "tr1")

    def test_queue_capacity_limit(self):
        small_queue = TrackQueue(max_size=2)
        self.assertTrue(small_queue.add(self.track1))
        self.assertTrue(small_queue.add(self.track2))
        # Exceeds max_size
        track3 = Track("tr3", "Song 3", "Art", 120, "", "", 1, "User")
        self.assertFalse(small_queue.add(track3))
        self.assertEqual(len(small_queue), 2)

    def test_queue_clear(self):
        self.queue.add(self.track1)
        self.queue.add(self.track2)
        cleared_count = self.queue.clear()
        self.assertEqual(cleared_count, 2)
        self.assertEqual(len(self.queue), 0)


if __name__ == "__main__":
    unittest.main()
