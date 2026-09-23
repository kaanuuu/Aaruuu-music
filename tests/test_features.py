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


if __name__ == "__main__":
    unittest.main()
