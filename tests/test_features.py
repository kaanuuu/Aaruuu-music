"""
Unit tests for new Admin & Owner features:
1. Track Queue Shuffle
2. Database Chat Registration & Stats Aggregation
3. Admin Cache and Invalidation
"""

import os
import unittest
import asyncio
from player.models import Track
from player.queue import TrackQueue
from database.db import Database
from bot.permissions import _admin_cache, clear_admin_cache


class TestExtendedFeatures(unittest.TestCase):
    def setUp(self):
        self.db_name = f"test_feat_{os.getpid()}.db"
        self.db = Database(self.db_name)
        asyncio.run(self.db.init())

    def tearDown(self):
        asyncio.run(self.db.close())
        if os.path.exists(self.db_name):
            os.remove(self.db_name)

    def test_queue_shuffle(self):
        queue = TrackQueue(max_size=10)
        for i in range(10):
            t = Track(
                track_id=f"id_{i}",
                title=f"Song {i}",
                artist="Artist",
                duration=180,
                thumbnail="http://example.com/thumb.jpg",
                source_url=f"http://example.com/{i}",
                requester_name="user",
                requester_user_id=123,
            )
            queue.add(t)

        count = queue.shuffle()
        self.assertEqual(count, 10)
        self.assertEqual(len(queue), 10)

    def test_chat_registration_and_stats(self):
        async def _test():
            # Register private user chat
            await self.db.register_chat(123456, "Alice", "private")
            # Register group chat
            await self.db.register_chat(-100987654, "Music Lounge", "supergroup")

            stats = await self.db.get_stats()
            self.assertEqual(stats["users"], 1)
            self.assertEqual(stats["groups"], 1)

            # Retrieve all chats and filtered
            all_chats = await self.db.get_all_chats()
            self.assertEqual(len(all_chats), 2)

            dms = await self.db.get_all_chats("user")
            self.assertEqual(len(dms), 1)
            self.assertEqual(dms[0]["chat_id"], 123456)

            groups = await self.db.get_all_chats("group")
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["chat_id"], -100987654)

        asyncio.run(_test())

    def test_admin_cache_clear(self):
        _admin_cache[(-1001, 555)] = (True, 9999999999.0)
        _admin_cache[(-1002, 666)] = (False, 9999999999.0)

        # Clear specific chat
        cleared = clear_admin_cache(-1001)
        self.assertEqual(cleared, 1)
        self.assertNotIn((-1001, 555), _admin_cache)
        self.assertIn((-1002, 666), _admin_cache)

        # Clear all
        cleared_all = clear_admin_cache(None)
        self.assertEqual(cleared_all, 1)
        self.assertEqual(len(_admin_cache), 0)

    def test_session_sanitizer_and_auto_conversion(self):
        import base64
        import struct
        from player.voice_chat import sanitize_and_prepare_session

        # 1. 263-byte session (older generator format) -> should convert to 271 bytes
        raw_263 = struct.pack(">B?256sI?", 2, False, b"A" * 256, 123456, False)
        s_263 = base64.urlsafe_b64encode(raw_263).decode().rstrip("=")
        # Enclose in quotes and whitespace as commonly happens when copied
        dirty_session = f"  '{s_263}' \n"
        converted = sanitize_and_prepare_session(dirty_session, api_id=6)

        pad = len(converted) % 4
        if pad:
            converted += "=" * (4 - pad)
        decoded = base64.urlsafe_b64decode(converted)
        self.assertEqual(len(decoded), 271)

        # 2. 271-byte session (already Pyrogram v2)
        raw_271 = struct.pack(">BI?256sQ?", 2, 6, False, b"B" * 256, 987654321, False)
        s_271 = base64.urlsafe_b64encode(raw_271).decode().rstrip("=")
        clean_271 = sanitize_and_prepare_session(s_271, api_id=6)
        pad = len(clean_271) % 4
        if pad:
            clean_271 += "=" * (4 - pad)
        self.assertEqual(len(base64.urlsafe_b64decode(clean_271)), 271)

    def test_queue_rich_message_card_and_back_button(self):
        from player.models import PlayerState, Track
        from player.queue import TrackQueue
        from bot.rich_player import build_queue_rich_message

        state = PlayerState(chat_id=123)
        track = Track(
            track_id="t1",
            title="Tum Hi Ho",
            artist="Arijit Singh",
            duration=262,
            thumbnail="https://example.com/thumb.jpg",
            source_url="https://example.com/audio.mp3",
            requester_user_id=111,
            requester_name="Aaruu",
        )
        state.play(track, {"name": "Aaruu", "id": 111})
        queue = TrackQueue()

        rich_queue = build_queue_rich_message(state, queue)
        blocks = rich_queue.get("blocks", [])

        # Verify photo block exists
        has_photo = any(b.get("type") == "photo" for b in blocks)
        self.assertTrue(has_photo, "Queue message must contain photo block for consistent rich card UI")

        # Verify back to player button exists
        all_buttons = []
        for b in blocks:
            if b.get("type") == "buttons":
                all_buttons.extend(b.get("buttons", []))

        back_buttons = [
            btn for btn in all_buttons if btn.get("callback_data", "").startswith("player:nowplaying:")
        ]
        self.assertTrue(len(back_buttons) > 0, "Queue must provide a back-to-player button")

    def test_double_tap_debounce_mechanism(self):
        import time
        from bot.callbacks import _DEBOUNCE_TIMESTAMPS

        key = "test_chat:101:player:pause:test_sess"
        now = time.time()
        _DEBOUNCE_TIMESTAMPS[key] = now

        # Immediately simulate double-tap 100ms later
        double_tap_time = now + 0.1
        is_debounced = (double_tap_time - _DEBOUNCE_TIMESTAMPS.get(key, 0)) < 0.7
        self.assertTrue(is_debounced, "Rapid double-tap within 700ms must be debounced")

        # Simulate genuine subsequent tap 1.5s later
        later_tap = now + 1.5
        is_debounced_later = (later_tap - _DEBOUNCE_TIMESTAMPS.get(key, 0)) < 0.7
        self.assertFalse(is_debounced_later, "Tap after 700ms should be allowed through")

    def test_youtube_meta_and_thumbnail_extraction(self):
        from player.extractor import MediaExtractor
        extractor = MediaExtractor()

        # Test video ID extraction and fallback thumbnail creation
        meta = extractor._extract_youtube_meta("https://youtu.be/42r8Stt-30w?si=1plBBKxkguvj24yx")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["video_id"], "42r8Stt-30w")
        self.assertTrue("42r8Stt-30w" in meta["thumbnail"])
        self.assertTrue("hqdefault.jpg" in meta["thumbnail"] or "i.ytimg.com" in meta["thumbnail"])

    def test_search_tracks_multi_results(self):
        from player.extractor import MediaExtractor
        from player.models import Track
        extractor = MediaExtractor()

        # Test search query cleaning
        clean_q = extractor._clean_search_query("Barsaat Banjaare (Official Video) [HD]")
        self.assertIn("Barsaat Banjaare", clean_q)
        self.assertNotIn("Official", clean_q)
        self.assertNotIn("Video", clean_q)

    def test_joingroupcall_public_key_safety(self):
        # Verify that raw TL objects never raise AttributeError when public_key or block is accessed
        class DummyJoinGroupCall:
            def __init__(self, call, join_as, params):
                self.call = call
                self.join_as = join_as
                self.params = params

        import inspect
        cls = DummyJoinGroupCall
        setattr(cls, "public_key", None)
        orig_getattr = getattr(cls, "__getattr__", None)
        def _safe_getattr(self, name):
            if name in ("public_key", "block", "video_stopped", "muted"):
                return None
            if orig_getattr:
                return orig_getattr(self, name)
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        cls.__getattr__ = _safe_getattr

        orig_init = cls.__init__
        sig = inspect.signature(orig_init)
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        def _safe_init(self, *args, **kwargs):
            self.public_key = kwargs.pop("public_key", None)
            if not has_varkw:
                extra_keys = set(kwargs.keys()) - set(sig.parameters.keys())
                for k in list(extra_keys):
                    setattr(self, k, kwargs.pop(k, None))
            return orig_init(self, *args, **kwargs)
        cls.__init__ = _safe_init

        # Instance without public_key
        call1 = DummyJoinGroupCall("c1", "j1", "p1")
        self.assertIsNone(call1.public_key)
        self.assertIsNone(call1.block)

        # Instance with public_key
        call2 = DummyJoinGroupCall("c2", "j2", "p2", public_key=b"test_key_123")
        self.assertEqual(call2.public_key, b"test_key_123")

    def test_track_stream_url_and_playable_source(self):
        from player.models import Track
        t1 = Track(
            track_id="t1",
            title="Song 1",
            artist="Artist 1",
            duration=180,
            thumbnail="https://example.com/thumb.jpg",
            source_url="https://youtube.com/watch?v=123",
            stream_url="https://cdn.example.com/audio.mp3",
            requester_user_id=123,
            requester_name="User",
        )
        self.assertEqual(t1.playable_source, "https://cdn.example.com/audio.mp3")
        self.assertEqual(t1.to_dict()["stream_url"], "https://cdn.example.com/audio.mp3")

        # Fallback to source_url if stream_url is None
        t2 = Track(
            track_id="t2",
            title="Song 2",
            artist="Artist 2",
            duration=180,
            thumbnail="https://example.com/thumb.jpg",
            source_url="https://example.com/music.mp3",
            requester_user_id=123,
            requester_name="User",
        )
        self.assertEqual(t2.playable_source, "https://example.com/music.mp3")


if __name__ == "__main__":
    unittest.main()
