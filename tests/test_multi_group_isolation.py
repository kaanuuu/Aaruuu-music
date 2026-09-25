"""
Unit tests verifying multi-group isolation, per-chat state independence,
and sequential A -> B -> A -> B playback flows.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from player.models import PlayerState, Track
from player.manager import PlayerManager
from player.voice_chat import VoiceChatAssistant


class TestMultiGroupIsolation(unittest.TestCase):
    def setUp(self):
        self.manager = PlayerManager()
        self.track_a = Track(
            track_id="track_a",
            title="Group A Melody",
            artist="Artist A",
            duration=180,
            thumbnail="https://example.com/a.jpg",
            source_url="https://youtube.com/watch?v=aaa",
            requester_user_id=101,
            requester_name="Alice",
        )
        self.track_b = Track(
            track_id="track_b",
            title="Group B Anthem",
            artist="Artist B",
            duration=210,
            thumbnail="https://example.com/b.jpg",
            source_url="https://youtube.com/watch?v=bbb",
            requester_user_id=202,
            requester_name="Bob",
        )

    def test_multi_group_state_and_queue_isolation(self):
        """Verify that Group A and Group B maintain 100% isolated player states and queues."""
        async def run():
            group_a_id = -1001111111111
            group_b_id = -1003616809678

            # 1. Play in Group A
            ok_a, state_a, queue_a = await self.manager.play_or_queue(
                group_a_id, self.track_a, {"id": 101, "name": "Alice"}
            )
            self.assertTrue(ok_a)
            self.assertTrue(state_a.is_playing)
            self.assertEqual(state_a.current_track.title, "Group A Melody")
            self.assertEqual(state_a.chat_id, group_a_id)

            # 2. Play in Group B
            ok_b, state_b, queue_b = await self.manager.play_or_queue(
                group_b_id, self.track_b, {"id": 202, "name": "Bob"}
            )
            self.assertTrue(ok_b)
            self.assertTrue(state_b.is_playing)
            self.assertEqual(state_b.current_track.title, "Group B Anthem")
            self.assertEqual(state_b.chat_id, group_b_id)

            # 3. Ensure states and sessions are distinct
            self.assertNotEqual(state_a.session_id, state_b.session_id)
            self.assertNotEqual(state_a.chat_id, state_b.chat_id)

            # 4. Pause Group A -> Group B must remain playing
            pause_a_ok, _ = await self.manager.pause(group_a_id, state_a.session_id)
            self.assertTrue(pause_a_ok)
            self.assertTrue(state_a.is_paused)
            self.assertFalse(state_b.is_paused)
            self.assertTrue(state_b.is_playing)

            # 5. Stop Group B -> Group A must remain paused with its current track intact
            stop_b_ok, _ = await self.manager.stop(group_b_id, state_b.session_id)
            self.assertTrue(stop_b_ok)
            self.assertFalse(state_b.is_playing)
            self.assertIsNone(state_b.current_track)

            # Verify Group A was completely unaffected by Group B stopping
            state_a_refreshed = await self.manager.get_state(group_a_id)
            self.assertTrue(state_a_refreshed.is_paused)
            self.assertEqual(state_a_refreshed.current_track.title, "Group A Melody")

        asyncio.run(run())

    def test_sequential_ab_ab_transitions(self):
        """Test sequence A -> B -> A -> B ensuring no stale chat_id or connection leakage."""
        async def run():
            group_a_id = -1001111111111
            group_b_id = -1003616809678

            # Step 1: Play A
            ok_a1, s_a1, _ = await self.manager.play_or_queue(group_a_id, self.track_a, {"id": 1, "name": "U1"})
            self.assertTrue(ok_a1)
            self.assertEqual(s_a1.chat_id, group_a_id)

            # Step 2: Play B
            ok_b1, s_b1, _ = await self.manager.play_or_queue(group_b_id, self.track_b, {"id": 2, "name": "U2"})
            self.assertTrue(ok_b1)
            self.assertEqual(s_b1.chat_id, group_b_id)

            # Step 3: Replay / Seek in A
            seek_a, _ = await self.manager.seek(group_a_id, 45)
            self.assertTrue(seek_a)
            self.assertGreaterEqual(s_a1.current_position, 44.0)

            # Step 4: Add to queue in B and skip
            track_b2 = Track(
                track_id="b2",
                title="B Next Song",
                artist="Artist B",
                duration=150,
                thumbnail="",
                source_url="https://youtube.com/watch?v=b2",
                requester_user_id=2,
                requester_name="U2",
            )
            q_b_ok, s_b_curr, q_b = await self.manager.play_or_queue(group_b_id, track_b2, {"id": 2, "name": "U2"})
            self.assertFalse(q_b_ok)  # Queued
            self.assertEqual(len(q_b), 1)

            next_b, _ = await self.manager.skip(group_b_id, s_b_curr.session_id)
            self.assertIsNotNone(next_b)
            self.assertEqual(next_b.title, "B Next Song")

            # Final check: Group A still has its original track
            self.assertEqual(s_a1.current_track.title, "Group A Melody")

        asyncio.run(run())

    def test_per_chat_error_isolation(self):
        """Verify that a playback error in Group B does NOT leak into or affect Group A."""
        async def run():
            vc = VoiceChatAssistant()
            group_a_id = -1001111111111
            group_b_id = -1003616809678

            # Simulate error in Group B
            vc.chat_errors[group_b_id] = "CHANNEL_INVALID: Telegram returned error for group B"
            vc.chat_errors[group_a_id] = None

            self.assertIsNone(vc.get_last_error(group_a_id))
            self.assertIn("CHANNEL_INVALID", vc.get_last_error(group_b_id))

        asyncio.run(run())

    def test_call_holder_active_calls_async_handling(self):
        """Verify is_call_active handles coroutines and objects without unawaited warnings."""
        async def run():
            vc = VoiceChatAssistant()
            
            # Mock pytgcalls with async active_calls
            mock_pytg = MagicMock(spec=["active_calls"])
            async def async_active():
                return [-1001111111111]
            mock_pytg.active_calls = async_active
            vc.pytgcalls = mock_pytg

            active_a = await vc.is_call_active(-1001111111111)
            active_b = await vc.is_call_active(-1003616809678)

            self.assertTrue(active_a)
            self.assertFalse(active_b)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
