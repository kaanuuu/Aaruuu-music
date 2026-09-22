"""
Aaruu Music - Voice Chat Integration
Isolates Telegram Group Voice Chat (VC) streaming logic via PyTgCalls / Pyrogram.
Supports PyTgCalls v1 and v2 APIs for live WebRTC VC audio streaming.
If ASSISTANT_SESSION (or STRING_SESSION + API_ID + API_HASH) is configured, streams live audio.
If not configured, operates in standalone Rich Message UI & queue management mode.
"""

import os
from typing import Any, Dict, Optional
from utils.logging import logger

# Flexible PyTgCalls imports for v1 & v2 compatibility
PYTGCALLS_AVAILABLE = False
Client = None
PyTgCalls = None
AudioPiped = None
MediaStream = None

try:
    from pyrogram import Client
    from pytgcalls import PyTgCalls

    # Try importing PyTgCalls v1 AudioPiped
    try:
        from pytgcalls.types import AudioPiped
    except ImportError:
        AudioPiped = None

    # Try importing PyTgCalls v2 MediaStream
    try:
        from pytgcalls.types import MediaStream
    except ImportError:
        MediaStream = None

    PYTGCALLS_AVAILABLE = True
except ImportError:
    PYTGCALLS_AVAILABLE = False


class VoiceChatAssistant:
    """Manages Telegram Group Voice Chat (VC) audio streaming via PyTgCalls / Pyrogram."""

    def __init__(self):
        self.session_string: Optional[str] = (
            os.getenv("STRING_SESSION") or os.getenv("ASSISTANT_SESSION")
        )
        self.api_id: Optional[str] = os.getenv("API_ID")
        self.api_hash: Optional[str] = os.getenv("API_HASH")

        self.is_configured: bool = bool(
            self.session_string and self.session_string.strip()
        )
        self.app: Optional[Any] = None
        self.pytgcalls: Optional[Any] = None
        self.active_chats: Dict[int, Any] = {}

    def check_status(self) -> Dict[str, Any]:
        """Returns assistant status without exposing secrets."""
        return {
            "configured": self.is_configured,
            "pytgcalls_installed": PYTGCALLS_AVAILABLE,
            "mode": (
                "PyTgCalls VC Streaming"
                if (self.is_configured and PYTGCALLS_AVAILABLE)
                else "Bot API (Rich Message UI Mode)"
            ),
            "streaming_available": bool(self.is_configured and PYTGCALLS_AVAILABLE),
        }

    async def start(self) -> None:
        """Initializes Pyrogram userbot client and PyTgCalls listener if session provided."""
        if not self.is_configured:
            logger.info(
                "Voice Chat: STRING_SESSION / ASSISTANT_SESSION not set. Operating in standalone Bot API UI mode."
            )
            return

        if not PYTGCALLS_AVAILABLE:
            logger.warning(
                "Voice Chat: pyrogram / pytgcalls libraries not loaded. Operating in standalone Bot API UI mode."
            )
            return

        try:
            logger.info("Voice Chat: Initializing Pyrogram & PyTgCalls VC Assistant...")
            self.app = Client(
                "AaruuAssistant",
                api_id=int(self.api_id) if self.api_id and str(self.api_id).isdigit() else 6,
                api_hash=self.api_hash or "eb06d4abfb49dc3eeb1aeb98ae0f581e",
                session_string=self.session_string,
            )
            await self.app.start()
            self.pytgcalls = PyTgCalls(self.app)
            await self.pytgcalls.start()
            logger.info("Voice Chat: PyTgCalls VC assistant connected successfully!")
        except Exception as e:
            logger.error("Voice Chat: Failed to initialize PyTgCalls assistant: %s", str(e))

    async def play_audio(self, chat_id: int, audio_source: str) -> bool:
        """Streams audio_source (URL or file) into the group voice chat call."""
        if self.pytgcalls:
            try:
                logger.info(
                    "Voice Chat: PyTgCalls joining VC call in chat %s with audio source...",
                    chat_id,
                )

                # PyTgCalls v1 API (join_group_call with AudioPiped)
                if hasattr(self.pytgcalls, "join_group_call"):
                    stream = AudioPiped(audio_source) if AudioPiped else audio_source
                    await self.pytgcalls.join_group_call(chat_id, stream)
                # PyTgCalls v2 API (play with MediaStream)
                elif hasattr(self.pytgcalls, "play"):
                    stream = MediaStream(audio_source) if MediaStream else audio_source
                    await self.pytgcalls.play(chat_id, stream)
                elif hasattr(self.pytgcalls, "join_call"):
                    await self.pytgcalls.join_call(chat_id, audio_source)

                self.active_chats[chat_id] = {"source": audio_source, "status": "playing"}
                return True
            except Exception as e:
                logger.error(
                    "Voice Chat: PyTgCalls error playing audio in chat %s: %s",
                    chat_id,
                    str(e),
                )

        logger.debug("Voice Chat: Playing track in chat %s (UI mode active)", chat_id)
        self.active_chats[chat_id] = {"source": audio_source, "status": "playing"}
        return True

    async def pause_audio(self, chat_id: int) -> bool:
        """Pauses stream in group VC."""
        if self.pytgcalls and chat_id in self.active_chats:
            try:
                if hasattr(self.pytgcalls, "pause_stream"):
                    await self.pytgcalls.pause_stream(chat_id)
                elif hasattr(self.pytgcalls, "pause"):
                    await self.pytgcalls.pause(chat_id)
            except Exception as e:
                logger.warning(
                    "Voice Chat: Error pausing VC in chat %s: %s", chat_id, str(e)
                )
        if chat_id in self.active_chats:
            self.active_chats[chat_id]["status"] = "paused"
            return True
        return False

    async def resume_audio(self, chat_id: int) -> bool:
        """Resumes stream in group VC."""
        if self.pytgcalls and chat_id in self.active_chats:
            try:
                if hasattr(self.pytgcalls, "resume_stream"):
                    await self.pytgcalls.resume_stream(chat_id)
                elif hasattr(self.pytgcalls, "resume"):
                    await self.pytgcalls.resume(chat_id)
            except Exception as e:
                logger.warning(
                    "Voice Chat: Error resuming VC in chat %s: %s", chat_id, str(e)
                )
        if chat_id in self.active_chats:
            self.active_chats[chat_id]["status"] = "playing"
            return True
        return False

    async def stop_audio(self, chat_id: int) -> bool:
        """Leaves or stops streaming in group VC."""
        if self.pytgcalls and chat_id in self.active_chats:
            try:
                if hasattr(self.pytgcalls, "leave_group_call"):
                    await self.pytgcalls.leave_group_call(chat_id)
                elif hasattr(self.pytgcalls, "leave_call"):
                    await self.pytgcalls.leave_call(chat_id)
                elif hasattr(self.pytgcalls, "leave"):
                    await self.pytgcalls.leave(chat_id)
            except Exception as e:
                logger.warning(
                    "Voice Chat: Error leaving VC in chat %s: %s", chat_id, str(e)
                )
        if chat_id in self.active_chats:
            del self.active_chats[chat_id]
            return True
        return False


voice_assistant = VoiceChatAssistant()

