"""
Unit tests for the Owner Block / Unblock feature and persistence.
"""

import asyncio
import os
import unittest
from database.db import Database
from bot.permissions import is_owner, is_sudo


class TestBlockFeature(unittest.TestCase):
    def setUp(self):
        # Use an isolated in-memory or test database
        self.test_db_path = f"test_block_{os.getpid()}.db"
        self.db = Database(self.test_db_path)
        asyncio.run(self.db.init())

    def tearDown(self):
        asyncio.run(self.db.close())
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def test_block_and_unblock_flow(self):
        async def run_test():
            user_id = 987654321
            owner_id = 111111111

            # Initially user should not be blocked
            self.assertFalse(self.db.is_user_blocked(user_id))

            # Block the user
            blocked = await self.db.block_user(user_id, blocked_by=owner_id, reason="Spamming audio")
            self.assertTrue(blocked)
            self.assertTrue(self.db.is_user_blocked(user_id))

            # Verify list contains user
            blocked_list = await self.db.get_blocked_users()
            self.assertEqual(len(blocked_list), 1)
            self.assertEqual(blocked_list[0]["user_id"], user_id)
            self.assertEqual(blocked_list[0]["reason"], "Spamming audio")
            self.assertEqual(blocked_list[0]["blocked_by"], owner_id)

            # Unblock the user
            unblocked = await self.db.unblock_user(user_id)
            self.assertTrue(unblocked)
            self.assertFalse(self.db.is_user_blocked(user_id))

            # Verify list is empty
            blocked_list_after = await self.db.get_blocked_users()
            self.assertEqual(len(blocked_list_after), 0)

            # Unblocking a non-blocked user returns False
            self.assertFalse(await self.db.unblock_user(999999))

        asyncio.run(run_test())

    def test_cache_preloading_on_init(self):
        async def run_test():
            # Add a user to test_db
            await self.db.block_user(55555, blocked_by=111, reason="Test preload")
            await self.db.close()

            # Create new Database instance with same db file
            new_db = Database(self.test_db_path)
            await new_db.init()
            # In-memory cache should already know 55555 is blocked
            self.assertTrue(new_db.is_user_blocked(55555))
            self.assertFalse(new_db.is_user_blocked(66666))
            await new_db.close()

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
