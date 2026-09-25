"""
Unit tests verifying robust playback flow, NoneType safety, and error handling.
Ensures 'NoneType' object has no attribute 'startswith' can never occur.
"""

import asyncio
import unittest
from player.models import PlayerState, Track
from player.manager import PlayerManager
from player.voice_chat import VoiceChatAssistant, verify_media_file_with_ffmpeg
from utils.escaping import sanitize_url, sanitize_text


class TestPlaybackValidation(unittest.TestCase):
    def setUp(self):
        self.manager = PlayerManager()

    def test_none_safe_verify_ffmpeg(self):
        """Test verify_media_file_with_ffmpeg with None and empty inputs."""
        async def run():
            ok, msg = await verify_media_file_with_ffmpeg(None)
            self.assertFalse(ok)
            self.assertIn("Invalid or missing", msg)

            ok2, msg2 = await verify_media_file_with_ffmpeg("")
            self.assertFalse(ok2)

            ok3, msg3 = await verify_media_file_with_ffmpeg("not_existing_file.mp3")
            self.assertFalse(ok3)

        asyncio.run(run())

    def test_none_safe_play_audio(self):
        """Test VoiceChatAssistant.play_audio with None and invalid sources."""
        async def run():
            vc = VoiceChatAssistant()
            ok = await vc.play_audio(12345, None)
            self.assertFalse(ok)
            self.assertIsNotNone(vc.last_error)
            self.assertIn("No playable audio source", vc.last_error)
            self.assertNotIn("startswith", vc.last_error)

            ok_empty = await vc.play_audio(12345, "")
            self.assertFalse(ok_empty)

        asyncio.run(run())

    def test_track_playable_source_with_none_fields(self):
        """Test Track models when various optional fields are None."""
        t1 = Track(
            track_id="t1",
            title="Song 1",
            artist="Artist 1",
            duration=120,
            thumbnail="",
            source_url="https://youtube.com/watch?v=123",
            requester_user_id=1,
            requester_name="User",
            stream_url=None,
            local_filepath=None,
        )
        self.assertIsNotNone(t1.playable_source)
        self.assertTrue(isinstance(t1.playable_source, str))

        t2 = Track(
            track_id="t2",
            title="Song 2",
            artist="Artist 2",
            duration=120,
            thumbnail="https://example.com/thumb.jpg",
            source_url="https://saavncdn.com/song.mp3",
            requester_user_id=1,
            requester_name="User",
            stream_url="https://saavncdn.com/song.mp3",
        )
        self.assertEqual(t2.playable_source, "https://saavncdn.com/song.mp3")

    def test_playback_flow_and_transitions(self):
        """Test consecutive playback, queue, skip, replay, and seek without exceptions."""
        async def run():
            chat_id = 99901
            t1 = Track(
                track_id="track_1",
                title="Song A",
                artist="Artist A",
                duration=200,
                thumbnail="https://example.com/a.jpg",
                source_url="https://youtube.com/watch?v=aaa",
                requester_user_id=10,
                requester_name="Alice",
            )
            t2 = Track(
                track_id="track_2",
                title="Song B",
                artist="Artist B",
                duration=180,
                thumbnail="https://example.com/b.jpg",
                source_url="https://youtube.com/watch?v=bbb",
                requester_user_id=20,
                requester_name="Bob",
            )

            # 1. Play first song
            is_now_playing, state, queue = await self.manager.play_or_queue(
                chat_id, t1, {"id": 10, "name": "Alice"}
            )
            self.assertTrue(is_now_playing)
            self.assertTrue(state.is_playing)
            self.assertEqual(state.current_track.track_id, "track_1")

            # 2. Queue second song
            is_now_playing2, state, queue = await self.manager.play_or_queue(
                chat_id, t2, {"id": 20, "name": "Bob"}
            )
            self.assertFalse(is_now_playing2)
            self.assertEqual(len(queue), 1)

            # 3. Skip to next song
            next_track, msg = await self.manager.skip(chat_id, state.session_id)
            self.assertIsNotNone(next_track)
            self.assertEqual(next_track.track_id, "track_2")
            self.assertEqual(len(queue), 0)

            # 4. Replay current song
            replay_ok, r_msg = await self.manager.replay(chat_id, state.session_id)
            self.assertTrue(replay_ok)
            self.assertEqual(state.current_track.track_id, "track_2")

            # 5. Seek in current song
            seek_ok, s_msg = await self.manager.seek(chat_id, 30)
            self.assertTrue(seek_ok)
            self.assertGreaterEqual(state.current_position, 29.0)

            # 6. Pause and resume
            p_ok, _ = await self.manager.pause(chat_id, state.session_id)
            self.assertTrue(p_ok)
            self.assertTrue(state.is_paused)

            res_ok, _ = await self.manager.resume(chat_id, state.session_id)
            self.assertTrue(res_ok)
            self.assertFalse(state.is_paused)

            # 7. Stop playback
            st_ok, _ = await self.manager.stop(chat_id, state.session_id)
            self.assertTrue(st_ok)
            self.assertFalse(state.is_playing)

        asyncio.run(run())

    def test_sanitize_url_none_safety(self):
        """Verify sanitize_url handles None, numbers, invalid strings cleanly."""
        self.assertIsNone(sanitize_url(None))
        self.assertIsNone(sanitize_url(""))
        self.assertIsNone(sanitize_url("ftp://example.com"))
        self.assertEqual(sanitize_url("https://example.com/test"), "https://example.com/test")

    def test_assistant_member_peer_resolution(self):
        """Verify assistant member resolution without requiring admin status."""
        async def run():
            vc = VoiceChatAssistant()
            # Non-connected fallback
            self.assertFalse(await vc.is_member_of_chat(12345))
            # Manually adding resolved peer simulates normal member resolution
            vc._resolved_peers.add(12345)
            self.assertTrue(await vc.is_member_of_chat(12345))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
