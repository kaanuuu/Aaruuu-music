"""
Aaruu Music - Voice Chat Integration
Isolates Telegram Group Voice Chat (VC) streaming logic via PyTgCalls / Pyrogram.
Supports PyTgCalls v1 and v2 APIs for live WebRTC VC audio streaming.
If ASSISTANT_SESSION (or STRING_SESSION + API_ID + API_HASH) is configured, streams live audio.
If not configured, operates in standalone Rich Message UI & queue management mode.
"""

import base64
import os
import struct
from typing import Any, Dict, Optional
from utils.logging import logger

# Flexible PyTgCalls imports with Pyrogram v2 backward compatibility patches
PYTGCALLS_AVAILABLE = False
Client = None
PyTgCalls = None
AudioPiped = None
MediaStream = None

try:
    import pyrogram
    import pyrogram.errors
    import pyrogram.utils

    # 1. Missing TL Types & Classes for PyTgCalls (e.g. InputGroupCallSlug)
    try:
        import pyrogram.raw.types
        import pyrogram.raw.base

        class _InputGroupCallSlug:
            ID = 0xDBBA8818
            QUALNAME = "types.InputGroupCallSlug"

            def __init__(self, slug: str = ""):
                self.slug = slug

            @classmethod
            def read(cls, b, *args, **kwargs):
                return cls()

            def write(self, *args, **kwargs):
                return b""

        setattr(pyrogram.raw.types, "InputGroupCallSlug", _InputGroupCallSlug)
        if hasattr(pyrogram.raw, "base"):
            setattr(pyrogram.raw.base, "InputGroupCallSlug", _InputGroupCallSlug)

        def _make_dummy_tl(name: str):
            class _DynamicTL:
                ID = 0
                QUALNAME = f"types.{name}"

                def __init__(self, *args, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

                @classmethod
                def read(cls, *args, **kwargs):
                    return cls()

                def write(self, *args, **kwargs):
                    return b""

            _DynamicTL.__name__ = name
            return _DynamicTL

        def _patched_raw_types_getattr(name: str):
            cls = _make_dummy_tl(name)
            setattr(pyrogram.raw.types, name, cls)
            return cls

        pyrogram.raw.types.__getattr__ = _patched_raw_types_getattr

        if hasattr(pyrogram.raw, "base"):
            def _patched_raw_base_getattr(name: str):
                cls = _make_dummy_tl(name)
                setattr(pyrogram.raw.base, name, cls)
                return cls
            pyrogram.raw.base.__getattr__ = _patched_raw_base_getattr
    except Exception:
        pass

    # 2. Patch missing legacy errors that PyTgCalls imports from pyrogram.errors in Pyrogram v2
    _legacy_exceptions = [
        "GroupcallForbidden",
        "GroupcallInvalid",
        "GroupcallAlreadyStarted",
        "GroupcallNotFound",
        "GroupCallNotFound",
        "GroupCallInvalid",
        "NoActiveGroupCall",
        "UserAlreadyParticipant",
        "PhoneCallDiscarded",
    ]
    for _name in _legacy_exceptions:
        if not hasattr(pyrogram.errors, _name):
            _exc = type(_name, (Exception,), {})
            setattr(pyrogram.errors, _name, _exc)
            try:
                import pyrogram.errors.exceptions
                setattr(pyrogram.errors.exceptions, _name, _exc)
            except Exception:
                pass

    def _patched_errors_getattr(name: str):
        _exc = type(name, (Exception,), {})
        setattr(pyrogram.errors, name, _exc)
        return _exc

    pyrogram.errors.__getattr__ = _patched_errors_getattr

    # 2. Modern Telegram 64-bit channel IDs patch (e.g. -1003952024411)
    if hasattr(pyrogram.utils, "MIN_CHANNEL_ID"):
        pyrogram.utils.MIN_CHANNEL_ID = -10099999999999
    if hasattr(pyrogram.utils, "MAX_CHANNEL_ID"):
        pyrogram.utils.MAX_CHANNEL_ID = -1000000000000

    _orig_get_peer_type = getattr(pyrogram.utils, "get_peer_type", None)
    if _orig_get_peer_type:
        def _safe_get_peer_type(peer_id: int) -> str:
            if peer_id < 0:
                if peer_id <= -1000000000000:
                    return "channel"
                return "chat"
            elif peer_id > 0:
                return "user"
            raise ValueError(f"Peer id invalid: {peer_id}")

        pyrogram.utils.get_peer_type = _safe_get_peer_type

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
except Exception as _pytg_err:
    logger.debug("PyTgCalls import warning: %s", str(_pytg_err))
    PYTGCALLS_AVAILABLE = False


def sanitize_and_prepare_session(session_str: str, api_id: int = 6) -> str:
    """
    Sanitizes string session:
    1. Strips leading/trailing whitespace, newlines, and quotes (' or ").
    2. Fixes base64 padding.
    3. Auto-converts older Pyrogram formats (262, 263, 266, 267 bytes) into Pyrogram v2 (271 bytes: >BI?256sQ?)
       by injecting the 4-byte API ID into the binary structure so Pyrogram v2 unpack never errors out.
    """
    if not session_str:
        return ""

    cleaned = session_str.strip().strip("'\"").strip()
    if not cleaned:
        return ""

    # Fix base64 padding if stripped during copy-paste
    rem = len(cleaned) % 4
    if rem:
        cleaned += "=" * (4 - rem)

    try:
        try:
            raw = base64.urlsafe_b64decode(cleaned)
        except Exception:
            raw = base64.b64decode(cleaned)

        raw_len = len(raw)

        # Already valid Pyrogram v2 structure (271 bytes: >BI?256sQ?)
        if raw_len == 271:
            return cleaned

        # Pyrogram v1 with 64-bit user id (267 bytes: >B?256sQ?)
        if raw_len == 267:
            dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(">B?256sQ?", raw)
            converted = struct.pack(">BI?256sQ?", dc_id, api_id, test_mode, auth_key, user_id, is_bot)
            logger.info("Voice Chat: Auto-converted 267-byte session into Pyrogram v2 (271 bytes).")
            return base64.urlsafe_b64encode(converted).decode().rstrip("=")

        # Pyrogram v1 standard (263 bytes: >B?256sI?)
        if raw_len == 263:
            dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(">B?256sI?", raw)
            converted = struct.pack(">BI?256sQ?", dc_id, api_id, test_mode, auth_key, user_id, is_bot)
            logger.info("Voice Chat: Auto-converted 263-byte session into Pyrogram v2 (271 bytes).")
            return base64.urlsafe_b64encode(converted).decode().rstrip("=")

        # Pyrogram v1 compact (262 bytes: >B?256sI)
        if raw_len == 262:
            dc_id, test_mode, auth_key, user_id = struct.unpack(">B?256sI", raw)
            converted = struct.pack(">BI?256sQ?", dc_id, api_id, test_mode, auth_key, user_id, False)
            logger.info("Voice Chat: Auto-converted 262-byte session into Pyrogram v2 (271 bytes).")
            return base64.urlsafe_b64encode(converted).decode().rstrip("=")

        # Pyrogram v1 64-bit compact (266 bytes: >B?256sQ)
        if raw_len == 266:
            dc_id, test_mode, auth_key, user_id = struct.unpack(">B?256sQ", raw)
            converted = struct.pack(">BI?256sQ?", dc_id, api_id, test_mode, auth_key, user_id, False)
            logger.info("Voice Chat: Auto-converted 266-byte session into Pyrogram v2 (271 bytes).")
            return base64.urlsafe_b64encode(converted).decode().rstrip("=")

    except Exception as e:
        logger.debug("Session string format probe error: %s", str(e))

    return cleaned


class VoiceChatAssistant:
    """Manages Telegram Group Voice Chat (VC) audio streaming via PyTgCalls / Pyrogram."""

    def __init__(self):
        raw_session = (
            os.getenv("STRING_SESSION") or os.getenv("ASSISTANT_SESSION") or ""
        )
        self.api_id: Optional[str] = os.getenv("API_ID")
        self.api_hash: Optional[str] = os.getenv("API_HASH")

        parsed_api_id = (
            int(self.api_id) if self.api_id and str(self.api_id).isdigit() else 6
        )
        self.session_string: Optional[str] = sanitize_and_prepare_session(
            raw_session, parsed_api_id
        )

        self.is_configured: bool = bool(
            self.session_string and self.session_string.strip()
        )
        self.app: Optional[Any] = None
        self.pytgcalls: Optional[Any] = None
        self.active_chats: Dict[int, Any] = {}
        self.assistant_id: Optional[int] = None
        self.assistant_username: Optional[str] = None
        self.assistant_name: Optional[str] = None
        self.is_connected: bool = False

    def check_status(self) -> Dict[str, Any]:
        """Returns assistant status without exposing secrets."""
        return {
            "configured": self.is_configured,
            "connected": self.is_connected,
            "assistant_id": self.assistant_id,
            "assistant_username": self.assistant_username,
            "pytgcalls_installed": PYTGCALLS_AVAILABLE,
            "mode": (
                "PyTgCalls VC Streaming"
                if (self.is_connected and self.pytgcalls)
                else "Bot API (Rich Message UI Mode)"
            ),
            "streaming_available": bool(self.is_connected and self.pytgcalls),
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
            try:
                me = await self.app.get_me()
                self.assistant_id = me.id
                self.assistant_username = me.username or ""
                self.assistant_name = me.first_name or "Assistant"
                self.is_connected = True
                logger.info(
                    "Voice Chat: Assistant connected as @%s (ID: %s, Name: %s)",
                    self.assistant_username,
                    self.assistant_id,
                    self.assistant_name,
                )
            except Exception as e:
                logger.warning("Voice Chat: Could not fetch assistant profile: %s", str(e))
                self.is_connected = True

            try:
                self.pytgcalls = PyTgCalls(self.app)
                await self.pytgcalls.start()
                logger.info("Voice Chat: PyTgCalls VC assistant connected successfully!")
            except Exception as vc_err:
                logger.error("Voice Chat: Failed to initialize PyTgCalls assistant: %s", str(vc_err))
                self.pytgcalls = None
        except Exception as e:
            err_msg = str(e)
            if "271 bytes" in err_msg or "unpack" in err_msg:
                logger.warning(
                    "Voice Chat: Provided STRING_SESSION format is incompatible with Pyrogram v2 (was generated using Telethon or Pyrogram v1). Please generate a Pyrogram v2 session string for VC live audio. Operating seamlessly in High-Speed Rich UI mode."
                )
            else:
                logger.error("Voice Chat: Failed to initialize PyTgCalls assistant: %s", err_msg)

    async def join_chat(self, chat_id_or_invite_link: Any) -> bool:
        """Attempts to join a group using invite link or chat ID."""
        if not self.app or not self.is_connected:
            return False
        try:
            await self.app.join_chat(chat_id_or_invite_link)
            logger.info("Voice Chat: Assistant successfully joined chat %s", chat_id_or_invite_link)
            return True
        except Exception as e:
            err_str = str(e)
            if "USER_ALREADY_PARTICIPANT" in err_str:
                logger.info("Voice Chat: Assistant is already a participant of %s", chat_id_or_invite_link)
                return True
            logger.warning(
                "Voice Chat: Assistant failed to join chat %s: %s",
                chat_id_or_invite_link,
                err_str,
            )
            return False

    async def play_audio(self, chat_id: int, audio_source: str) -> bool:
        """Streams audio_source (URL or file) into the group voice chat call."""
        if self.pytgcalls and self.is_connected:
            try:
                logger.info(
                    "Voice Chat: PyTgCalls joining VC call in chat %s with audio source...",
                    chat_id,
                )

                # PyTgCalls v1 API (join_group_call with AudioPiped)
                if hasattr(self.pytgcalls, "join_group_call"):
                    stream = AudioPiped(audio_source) if AudioPiped else audio_source
                    if chat_id in self.active_chats and hasattr(self.pytgcalls, "change_stream"):
                        try:
                            await self.pytgcalls.change_stream(chat_id, stream)
                        except Exception:
                            await self.pytgcalls.join_group_call(chat_id, stream)
                    else:
                        await self.pytgcalls.join_group_call(chat_id, stream)

                # PyTgCalls v2 API (play with MediaStream)
                elif hasattr(self.pytgcalls, "play"):
                    stream = MediaStream(audio_source) if MediaStream else audio_source
                    if chat_id in self.active_chats and hasattr(self.pytgcalls, "change_stream"):
                        try:
                            await self.pytgcalls.change_stream(chat_id, stream)
                        except Exception:
                            await self.pytgcalls.play(chat_id, stream)
                    else:
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

