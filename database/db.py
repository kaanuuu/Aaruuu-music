"""
Aaruu Music - Database Operations
Thread-safe SQLite persistent store with modular abstraction for chat settings and playback history.
"""

import asyncio
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set
from utils.logging import logger


class Database:
    """Manages SQLite storage for chats, persistent settings, and playback history."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("DATABASE_URL", "aaruu_music.db")
        # If DATABASE_URL starts with sqlite:///, strip it
        if self.db_path.startswith("sqlite:///"):
            self.db_path = self.db_path.replace("sqlite:///", "", 1)
        self._lock = asyncio.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._blocked_users_cache: set = set()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    async def init(self) -> None:
        """Initializes tables and indexes."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._create_tables)
            logger.info("Database initialized successfully at %s", self.db_path)

    def _create_tables(self) -> None:
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY,
                    chat_title TEXT,
                    chat_type TEXT DEFAULT 'group',
                    volume INTEGER DEFAULT 100,
                    loop_mode TEXT DEFAULT 'off',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    track_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT,
                    duration INTEGER,
                    source_url TEXT,
                    requested_by TEXT,
                    played_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS blocked_users (
                    user_id INTEGER PRIMARY KEY,
                    reason TEXT,
                    blocked_by INTEGER NOT NULL,
                    blocked_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_history_chat ON history(chat_id, played_at);
                """
            )
            # Migration: Ensure chat_type column exists if table was created in older version
            try:
                conn.execute("ALTER TABLE chats ADD COLUMN chat_type TEXT DEFAULT 'group'")
            except sqlite3.OperationalError:
                pass

            # Pre-load blocked users into memory cache for zero-latency lookups
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM blocked_users")
            rows = cursor.fetchall()
            self._blocked_users_cache = {row["user_id"] for row in rows}
            logger.info("Loaded %d blocked user(s) into memory cache.", len(self._blocked_users_cache))

    async def register_chat(
        self, chat_id: int, chat_title: str = "", chat_type: str = ""
    ) -> None:
        """Registers or updates a chat (user DM or group) upon activity."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._register_chat_sync, chat_id, chat_title, chat_type
            )

    def _register_chat_sync(
        self, chat_id: int, chat_title: str = "", chat_type: str = ""
    ) -> None:
        conn = self._get_connection()
        now = time.time()
        if not chat_type:
            chat_type = "private" if chat_id > 0 else "group"
        with conn:
            conn.execute(
                """
                INSERT INTO chats (chat_id, chat_title, chat_type, volume, loop_mode, created_at, updated_at)
                VALUES (?, ?, ?, 100, 'off', ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    chat_title = CASE WHEN ? != '' THEN ? ELSE chat_title END,
                    chat_type = CASE WHEN ? != '' THEN ? ELSE chat_type END,
                    updated_at = ?
                """,
                (
                    chat_id,
                    chat_title,
                    chat_type,
                    now,
                    now,
                    chat_title,
                    chat_title,
                    chat_type,
                    chat_type,
                    now,
                ),
            )

    async def get_all_chats(
        self, chat_type_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Retrieves all chats, optionally filtered by 'user'/'private' or 'group'."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._get_all_chats_sync, chat_type_filter
            )

    def _get_all_chats_sync(
        self, chat_type_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        if chat_type_filter in ("user", "private", "dm"):
            cursor.execute(
                "SELECT * FROM chats WHERE chat_id > 0 ORDER BY updated_at DESC"
            )
        elif chat_type_filter in ("group", "groups"):
            cursor.execute(
                "SELECT * FROM chats WHERE chat_id < 0 ORDER BY updated_at DESC"
            )
        else:
            cursor.execute("SELECT * FROM chats ORDER BY updated_at DESC")
        return [dict(r) for r in cursor.fetchall()]

    async def get_stats(self) -> Dict[str, Any]:
        """Returns aggregate metrics on users, groups, songs, and blocks."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._get_stats_sync)

    def _get_stats_sync(self) -> Dict[str, Any]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM chats WHERE chat_id > 0")
        users_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM chats WHERE chat_id < 0")
        groups_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM history")
        history_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM blocked_users")
        blocked_count = cursor.fetchone()[0]
        return {
            "users": users_count,
            "groups": groups_count,
            "history": history_count,
            "blocked": blocked_count,
        }

    async def get_chat_settings(self, chat_id: int) -> Dict[str, Any]:
        """Fetches settings for a specific chat."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._get_chat_settings_sync, chat_id)

    def _get_chat_settings_sync(self, chat_id: int) -> Dict[str, Any]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        now = time.time()
        cursor.execute(
            """
            INSERT OR IGNORE INTO chats (chat_id, chat_title, volume, loop_mode, created_at, updated_at)
            VALUES (?, '', 100, 'off', ?, ?)
            """,
            (chat_id, now, now),
        )
        conn.commit()
        return {"chat_id": chat_id, "chat_title": "", "volume": 100, "loop_mode": "off"}

    async def update_chat_settings(
        self, chat_id: int, volume: Optional[int] = None, loop_mode: Optional[str] = None
    ) -> None:
        """Updates chat volume or loop mode."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._update_chat_settings_sync, chat_id, volume, loop_mode
            )

    def _update_chat_settings_sync(
        self, chat_id: int, volume: Optional[int], loop_mode: Optional[str]
    ) -> None:
        conn = self._get_connection()
        now = time.time()
        # Ensure row exists
        self._get_chat_settings_sync(chat_id)
        updates = []
        params = []
        if volume is not None:
            updates.append("volume = ?")
            params.append(volume)
        if loop_mode is not None:
            updates.append("loop_mode = ?")
            params.append(loop_mode)
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(now)
        params.append(chat_id)
        query = f"UPDATE chats SET {', '.join(updates)} WHERE chat_id = ?"
        with conn:
            conn.execute(query, tuple(params))

    async def add_history(
        self,
        chat_id: int,
        track_id: str,
        title: str,
        artist: str,
        duration: int,
        source_url: str,
        requested_by: str,
    ) -> None:
        """Records a completed track in the history table."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._add_history_sync,
                chat_id,
                track_id,
                title,
                artist,
                duration,
                source_url,
                requested_by,
            )

    def _add_history_sync(
        self,
        chat_id: int,
        track_id: str,
        title: str,
        artist: str,
        duration: int,
        source_url: str,
        requested_by: str,
    ) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO history (chat_id, track_id, title, artist, duration, source_url, requested_by, played_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    track_id,
                    title,
                    artist,
                    duration,
                    source_url,
                    requested_by,
                    time.time(),
                ),
            )

    def is_user_blocked(self, user_id: int) -> bool:
        """Instant zero-latency check against in-memory cache."""
        return user_id in self._blocked_users_cache

    async def block_user(self, user_id: int, blocked_by: int, reason: str = "") -> bool:
        """Blocks a user and persists to database and cache."""
        async with self._lock:
            self._blocked_users_cache.add(user_id)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._block_user_sync, user_id, blocked_by, reason
            )

    def _block_user_sync(self, user_id: int, blocked_by: int, reason: str) -> bool:
        conn = self._get_connection()
        now = time.time()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO blocked_users (user_id, reason, blocked_by, blocked_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, reason, blocked_by, now),
            )
        logger.info("User %d blocked by %d. Reason: %s", user_id, blocked_by, reason or "None")
        return True

    async def unblock_user(self, user_id: int) -> bool:
        """Unblocks a user and removes them from database and cache."""
        async with self._lock:
            self._blocked_users_cache.discard(user_id)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._unblock_user_sync, user_id)

    def _unblock_user_sync(self, user_id: int) -> bool:
        conn = self._get_connection()
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM blocked_users WHERE user_id = ?", (user_id,))
            deleted = cursor.rowcount > 0
        logger.info("User %d unblocked (found=%s)", user_id, deleted)
        return deleted

    async def get_blocked_users(self) -> list:
        """Returns list of all blocked user records."""
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, self._get_blocked_users_sync)

    def _get_blocked_users_sync(self) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, reason, blocked_by, blocked_at FROM blocked_users ORDER BY blocked_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    async def close(self) -> None:
        """Closes the active database connection."""
        async with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
