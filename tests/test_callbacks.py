"""
Unit tests for callback routing, session protection, and state transitions.
"""

import asyncio
import unittest
from player.models import PlayerState, Track
from player.manager import PlayerManager


class TestCallbacksAndState(unittest.TestCase):
    def setUp(self):
        self.state = PlayerState(chat_id=12345)
        self.track = Track(
            track_id="test_track",
            title="Barsaat Banjaare",
            artist="Aaruu Artist",
            duration=186,
            thumbnail="https://example.com/art.jpg",
            source_url="https://youtube.com/watch?v=xyz",
            requester_user_id=999,
            requester_name="music_fan",
        )

    def test_state_transitions(self):
        # Initial state
        self.assertFalse(self.state.is_playing)
        self.assertFalse(self.state.is_paused)

        # Start playback
        sess = self.state.play(self.track, {"name": "music_fan", "id": 999})
        self.assertTrue(self.state.is_playing)
        self.assertFalse(self.state.is_paused)
        self.assertEqual(self.state.duration, 186)
        self.assertEqual(self.state.session_id, sess)

        # Pause playback
        self.assertTrue(self.state.pause())
        self.assertTrue(self.state.is_paused)
        self.assertFalse(self.state.pause())  # Already paused

        # Resume playback
        self.assertTrue(self.state.resume())
        self.assertFalse(self.state.is_paused)

        # Replay resets offset
        self.state.replay()
        self.assertLessEqual(self.state.current_position, 1.0)

        # Seek to 90 seconds
        seeked = self.state.seek(90)
        self.assertEqual(seeked, 90.0)
        self.assertAlmostEqual(self.state.current_position, 90.0, delta=1.0)

        # Stop playback
        self.state.stop()
        self.assertFalse(self.state.is_playing)
        self.assertIsNone(self.state.current_track)

    def test_stale_callback_protection(self):
        async def run_check():
            manager = PlayerManager()
            await manager.play_or_queue(
                12345, self.track, {"name": "music_fan", "id": 999}
            )
            state = await manager.get_state(12345)
            real_session = state.session_id

            # Valid pause
            ok, msg = await manager.pause(12345, real_session)
            self.assertTrue(ok)

            # Stale session pause must be rejected
            stale_ok, stale_msg = await manager.pause(12345, "stale_session_123")
            self.assertFalse(stale_ok)
            self.assertEqual(stale_msg, "This player is no longer active.")

            # Stale session resume must be rejected
            stale_resume_ok, stale_resume_msg = await manager.resume(12345, "old_session")
            self.assertFalse(stale_resume_ok)
            self.assertEqual(stale_resume_msg, "This player is no longer active.")

        asyncio.run(run_check())

    def test_callback_data_length_limit(self):
        """Telegram limits callback_data to 64 bytes."""
        sess = self.state.session_id
        actions = ["replay", "pause", "resume", "skip", "queue", "close"]
        for act in actions:
            cb = f"player:{act}:{sess}"
            self.assertLessEqual(
                len(cb.encode("utf-8")),
                64,
                f"Callback data '{cb}' exceeds 64-byte Telegram limit!",
            )


if __name__ == "__main__":
    unittest.main()
