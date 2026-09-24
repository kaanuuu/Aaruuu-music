"""
Aaruu Music - Voice Chat Integration
Isolates Telegram Group Voice Chat (VC) streaming logic via PyTgCalls / Pyrogram.
Supports PyTgCalls v1 and v2 APIs for live WebRTC VC audio streaming.
If ASSISTANT_SESSION (or STRING_SESSION + API_ID + API_HASH) is configured, streams live audio.
If not configured, operates in standalone Rich Message UI & queue management mode.
"""

import asyncio
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

    # 1. Missing TL Types & Classes for PyTgCalls (e.g. InputGroupCallSlug) removed to prevent Circular reference/Pydantic serialization errors.
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


async def verify_media_file_with_ffmpeg(path_or_url: str) -> tuple[bool, str]:
    """
    Verifies if FFmpeg can successfully decode the media file or stream.
    Runs a fast test: ffmpeg -ss 00:00:00 -t 1 -i <file/URL> -f null -
    Returns (success, log_or_error_message).
    """
    import asyncio
    import os
    import shutil

    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        return False, "FFmpeg binary is not found on the system path."

    if not path_or_url or not isinstance(path_or_url, str):
        return False, "Invalid or missing media path/URL."

    is_http = path_or_url.startswith(("http://", "https://"))
    if not is_http:
        if not os.path.exists(path_or_url):
            return False, f"Local file does not exist: {path_or_url}"
        if os.path.getsize(path_or_url) == 0:
            return False, f"Local file is empty: {path_or_url}"

    try:
        # Build command. For HTTP streams, we can add User-Agent & Referer headers to match playback
        cmd = ["ffmpeg", "-y"]
        if is_http:
            # Match the headers and reconnect parameters used in PyTgCalls
            cmd.extend([
                "-headers", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\nReferer: https://www.jiosaavn.com/\r\n",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5"
            ])
        
        cmd.extend([
            "-ss", "00:00:00",
            "-t", "1",
            "-i", path_or_url,
            "-f", "null",
            "-"
        ])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            stderr_decoded = stderr.decode(errors="ignore")
            if proc.returncode == 0:
                return True, "FFmpeg successfully validated the audio stream/file decoding."
            else:
                err_lines = stderr_decoded.splitlines()[-10:]
                return False, f"FFmpeg validation failed (code {proc.returncode}). Errors:\n" + "\n".join(err_lines)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return False, "FFmpeg validation timed out after 5 seconds."
    except Exception as e:
        return False, f"Error spawning FFmpeg verification: {str(e)}"


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
        self._resolved_peers: set = set()
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

            # Register real-time update listener so Pyrogram constantly caches peer access_hashes
            @self.app.on_message()
            async def _auto_peer_cache_handler(client, message):
                pass

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
                # Comprehensive pre-caching of assistant's dialogs and access hashes
                try:
                    logger.info("Voice Chat: Pre-caching assistant dialogs and access hashes...")
                    async for dialog in self.app.get_dialogs():
                        if dialog.chat:
                            self._resolved_peers.add(dialog.chat.id)
                    logger.info("Voice Chat: Assistant dialogs pre-cached successfully. Cached %s resolved peers.", len(self._resolved_peers))
                except Exception as d_err:
                    logger.debug("Voice Chat: Dialog pre-caching note: %s", str(d_err))
            except Exception as e:
                logger.warning("Voice Chat: Could not fetch assistant profile: %s", str(e))
                self.is_connected = True

            try:
                self.pytgcalls = PyTgCalls(self.app)

                if hasattr(self.pytgcalls, "on_stream_end"):
                    @self.pytgcalls.on_stream_end()
                    async def _stream_end_handler(client, update):
                        try:
                            chat_id = getattr(update, "chat_id", None)
                            if not chat_id and hasattr(update, "call"):
                                chat_id = getattr(update.call, "chat_id", None)
                            if chat_id:
                                logger.info("Voice Chat: Stream ended in chat %s. Auto-advancing...", chat_id)
                                from player.manager import player_manager
                                await player_manager.auto_advance(chat_id)
                        except Exception as se_err:
                            logger.debug("Voice Chat stream end handler note: %s", str(se_err))

                if hasattr(self.pytgcalls, "on_update"):
                    @self.pytgcalls.on_update()
                    async def _stream_update_handler(client, update):
                        try:
                            up_type = type(update).__name__
                            if any(k in up_type for k in ("StreamEnded", "StreamAudioEnded", "StreamVideoEnded", "CallEnded")):
                                chat_id = getattr(update, "chat_id", None)
                                if not chat_id and hasattr(update, "call"):
                                    chat_id = getattr(update.call, "chat_id", None)
                                if chat_id:
                                    logger.info("Voice Chat: %s in chat %s. Auto-advancing...", up_type, chat_id)
                                    from player.manager import player_manager
                                    await player_manager.auto_advance(chat_id)
                        except Exception:
                            pass

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
            chat = await self.app.join_chat(chat_id_or_invite_link)
            logger.info("Voice Chat: Assistant successfully joined chat %s", chat_id_or_invite_link)
            try:
                await self.app.get_chat(chat.id)
            except Exception:
                pass
            return True
        except Exception as e:
            err_str = str(e)
            if "USER_ALREADY_PARTICIPANT" in err_str:
                logger.info("Voice Chat: Assistant is already a participant of %s", chat_id_or_invite_link)
                try:
                    await self.app.get_chat(chat_id_or_invite_link)
                except Exception:
                    pass
                return True
            logger.warning(
                "Voice Chat: Assistant failed to join chat %s: %s",
                chat_id_or_invite_link,
                err_str,
            )
            return False

    async def is_call_active(self, chat_id: int) -> bool:
        """Checks if there is an active PyTgCalls call session for the given chat_id."""
        if not self.pytgcalls:
            return False
        import inspect
        # 1. Check pytgcalls.active_calls (standard in modern PyTgCalls)
        if hasattr(self.pytgcalls, "active_calls"):
            try:
                calls = self.pytgcalls.active_calls
                if inspect.iscoroutine(calls) or inspect.iscoroutinefunction(calls):
                    calls = await calls
                elif inspect.isawaitable(calls):
                    calls = await calls
                
                if hasattr(calls, "__contains__"):
                    try:
                        if chat_id in calls:
                            return True
                    except Exception:
                        pass
                for c in calls:
                    if getattr(c, "chat_id", None) == chat_id:
                        return True
            except Exception:
                pass
        # 2. Check pytgcalls.calls
        if hasattr(self.pytgcalls, "calls"):
            try:
                calls = self.pytgcalls.calls
                if inspect.iscoroutine(calls) or inspect.iscoroutinefunction(calls):
                    calls = await calls
                elif inspect.isawaitable(calls):
                    calls = await calls
                
                if hasattr(calls, "__contains__"):
                    try:
                        if chat_id in calls:
                            return True
                    except Exception:
                        pass
                if isinstance(calls, list):
                    for c in calls:
                        if getattr(c, "chat_id", None) == chat_id:
                            return True
                elif isinstance(calls, dict):
                    return chat_id in calls
            except Exception:
                pass
        # 3. Fallback to our internal active_chats tracker
        return chat_id in self.active_chats

    async def play_audio(self, chat_id: int, audio_source: str, seek_seconds: float = 0.0, is_video: bool = False) -> bool:
        """Streams audio_source or video into the group voice chat call."""
        if not audio_source or not isinstance(audio_source, str):
            err_msg = "No playable audio source or downloaded file available for streaming."
            self.last_error = err_msg
            logger.warning("Voice Chat: Cannot play audio in chat %s: %s", chat_id, err_msg)
            return False

        if self.pytgcalls and self.is_connected:
            try:
                # Playable stream URL
                playable_stream = audio_source

                # Fast peer verification & access hash caching before PyTgCalls call
                peer_cached = False
                if chat_id in self._resolved_peers:
                    peer_cached = True

                if not peer_cached:
                    try:
                        await self.app.get_chat(chat_id)
                        self._resolved_peers.add(chat_id)
                        peer_cached = True
                    except Exception as peer_err:
                        logger.info("Voice Chat: get_chat(%s) note: %s. Attempting member lookup and dialog scan...", chat_id, str(peer_err))
                        try:
                            if self.assistant_id:
                                await self.app.get_chat_member(chat_id, self.assistant_id)
                                self._resolved_peers.add(chat_id)
                                peer_cached = True
                        except Exception:
                            pass

                        if not peer_cached:
                            try:
                                async for dialog in self.app.get_dialogs(limit=50):
                                    if dialog.chat and dialog.chat.id == chat_id:
                                        self._resolved_peers.add(chat_id)
                                        peer_cached = True
                                        break
                            except Exception as d_err:
                                logger.debug("Voice Chat: get_dialogs scan note: %s", str(d_err))

                if not peer_cached:
                    try:
                        from bot.api import bot_api_client
                        inv_res = await bot_api_client.export_chat_invite_link(chat_id)
                        inv_link = inv_res.get("result") if inv_res.get("ok") else None
                        if inv_link:
                            await self.app.join_chat(inv_link)
                            await self.app.get_chat(chat_id)
                            self._resolved_peers.add(chat_id)
                            peer_cached = True
                    except Exception as inv_err:
                        logger.debug("Voice Chat: Invite link peer resolution note: %s", str(inv_err))

                # Pipeline Diagnostics & Logs
                is_http = playable_stream.startswith(("http://", "https://"))
                import shutil
                ffmpeg_found = shutil.which("ffmpeg") or "Not found"

                logger.info("[MEDIA-PIPELINE DEBUG] 1. Extracted audio URL/source: %s", playable_stream)
                logger.info("[MEDIA-PIPELINE DEBUG] 2. Source type: %s", "remote" if is_http else "local")
                logger.info("[MEDIA-PIPELINE DEBUG] 3. Downloaded file path: %s", playable_stream if not is_http else "N/A")
                logger.info("[MEDIA-PIPELINE DEBUG] 4. File existence: %s", os.path.exists(playable_stream) if not is_http else "N/A")
                logger.info("[MEDIA-PIPELINE DEBUG] 5. File size: %s bytes", os.path.getsize(playable_stream) if not is_http and os.path.exists(playable_stream) else "N/A")
                logger.info("[MEDIA-PIPELINE DEBUG] 6. FFmpeg availability: %s", ffmpeg_found)

                # Run FFmpeg decode ability test
                logger.info("[MEDIA-PIPELINE DEBUG] 7. Testing FFmpeg ability to decode the file...")
                success, ffmpeg_log = await verify_media_file_with_ffmpeg(playable_stream)
                logger.info("[MEDIA-PIPELINE DEBUG] FFmpeg verification result: %s - %s", "SUCCESS" if success else "FAILED", ffmpeg_log)

                if not success:
                    raise RuntimeError(f"FFmpeg decoding test failed: {ffmpeg_log}")

                # Construct stream with FFmpeg headers & reconnect flags so HTTP audio CDNs (JioSaavn / YouTube) don't send 403 or silence
                ffmpeg_params = ""
                if is_http:
                    ffmpeg_params += (
                        "-headers "
                        "'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\nReferer: https://www.jiosaavn.com/\r\n' "
                        "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
                    )
                if seek_seconds > 0.0:
                    if ffmpeg_params:
                        ffmpeg_params += " "
                    ffmpeg_params += f"-ss {seek_seconds}"
                
                def _build_stream(target_url: str):
                    if not target_url or not isinstance(target_url, str):
                        return None
                    is_remote = target_url.startswith(("http://", "https://"))
                    # If local, we must NOT pass http specific parameters like headers or reconnect unless seeking is active
                    if is_remote:
                        params = ffmpeg_params
                    else:
                        params = f"-ss {seek_seconds}" if seek_seconds > 0.0 else ""
                    
                    if MediaStream:
                        try:
                            from pytgcalls.types import AudioQuality
                            v_params = None
                            if is_video:
                                try:
                                    from pytgcalls.types import VideoQuality
                                    v_params = getattr(VideoQuality, "HD_720p", None)
                                except Exception:
                                    pass

                            kw = {"audio_parameters": AudioQuality.HIGH}
                            if v_params is not None:
                                kw["video_parameters"] = v_params
                            if params:
                                kw["ffmpeg_parameters"] = params

                            return MediaStream(target_url, **kw)
                        except Exception:
                            try:
                                if params:
                                    return MediaStream(target_url, ffmpeg_parameters=params)
                                else:
                                    return MediaStream(target_url)
                            except Exception:
                                pass
                    if is_video:
                        try:
                            from pytgcalls.types import AudioVideoPiped
                            if AudioVideoPiped:
                                if params:
                                    return AudioVideoPiped(target_url, additional_ffmpeg_parameters=params)
                                return AudioVideoPiped(target_url)
                        except Exception:
                            pass
                    if AudioPiped:
                        try:
                            if params:
                                return AudioPiped(target_url, additional_ffmpeg_parameters=params)
                            else:
                                return AudioPiped(target_url)
                        except Exception:
                            try:
                                if params:
                                    return AudioPiped(target_url, ffmpeg_parameters=params)
                                else:
                                    return AudioPiped(target_url)
                            except Exception:
                                pass
                    return target_url

                logger.info("[MEDIA-PIPELINE DEBUG] 8. Creating PyTgCalls media stream object...")
                stream_obj = _build_stream(playable_stream)
                logger.info("[MEDIA-PIPELINE DEBUG] Created media stream object of type: %s", type(stream_obj).__name__)

                async def _do_stream():
                    already_connected = await self.is_call_active(chat_id)
                    # PyTgCalls v1 API (join_group_call)
                    if hasattr(self.pytgcalls, "join_group_call"):
                        if already_connected and hasattr(self.pytgcalls, "change_stream"):
                            logger.info("[MEDIA-PIPELINE DEBUG] Already connected in PyTgCalls v1. Changing stream...")
                            await self.pytgcalls.change_stream(chat_id, stream_obj)
                        else:
                            logger.info("[MEDIA-PIPELINE DEBUG] Invoking PyTgCalls join_group_call() for chat %s", chat_id)
                            await self.pytgcalls.join_group_call(chat_id, stream_obj)

                    # PyTgCalls v2 API (play)
                    elif hasattr(self.pytgcalls, "play"):
                        logger.info("[MEDIA-PIPELINE DEBUG] Invoking PyTgCalls play() for chat %s (already_connected=%s)", chat_id, already_connected)
                        await self.pytgcalls.play(chat_id, stream_obj)

                    elif hasattr(self.pytgcalls, "join_call"):
                        logger.info("[MEDIA-PIPELINE DEBUG] Invoking PyTgCalls join_call() for chat %s", chat_id)
                        await self.pytgcalls.join_call(chat_id, stream_obj)
                    else:
                        raise AttributeError("PyTgCalls instance has no join_group_call, play, or join_call method.")

                try:
                    await _do_stream()
                    logger.info("[MEDIA-PIPELINE DEBUG] 10. PyTgCalls playback/streaming call completed successfully!")
                except Exception as inner_e:
                    # If CHANNEL_INVALID or peer missing on first try, attempt 1 retry after forcing peer resolution
                    err_str = str(inner_e).lower()
                    logger.warning("[MEDIA-PIPELINE DEBUG] PyTgCalls stream call encountered exception: %s", str(inner_e))
                    if "channel_invalid" in err_str or "peer" in err_str or "400" in err_str or "group_call" in err_str:
                        logger.info("Voice Chat: Initial stream join failed (%s). Retrying after peer sync...", str(inner_e))
                        await asyncio.sleep(0.5)
                        try:
                            from bot.api import bot_api_client
                            inv_res = await bot_api_client.export_chat_invite_link(chat_id)
                            inv_link = inv_res.get("result") if inv_res.get("ok") else None
                            if inv_link:
                                await self.app.join_chat(inv_link)
                        except Exception as peer_retry_err:
                            logger.warning("[MEDIA-PIPELINE DEBUG] Peer sync invite link join error: %s", str(peer_retry_err))
                        
                        logger.info("[MEDIA-PIPELINE DEBUG] Retrying PyTgCalls playback call...")
                        try:
                            await _do_stream()
                            logger.info("[MEDIA-PIPELINE DEBUG] 10. PyTgCalls playback/streaming call completed successfully on retry!")
                        except Exception as retry_err:
                            retry_err_str = str(retry_err).lower()
                            if "channel_invalid" in retry_err_str or "peer" in retry_err_str or "400" in retry_err_str or "group_call" in retry_err_str:
                                raise RuntimeError(
                                    "ASSISTANT_NOT_IN_GROUP / CHANNEL_INVALID: Telegram returned CHANNEL_INVALID. "
                                    "The assistant userbot must be added to the group and promoted to administrator with "
                                    "'Manage Video Chats' permission, and the group Voice Chat must be started manually first!"
                                )
                            raise retry_err
                    else:
                        raise inner_e

                self.active_chats[chat_id] = {"source": audio_source, "status": "playing"}
                self.last_error = None
                return True
            except Exception as e:
                err_text = str(e)
                self.last_error = err_text
                logger.error(
                    "Voice Chat: PyTgCalls error playing audio in chat %s: %s",
                    chat_id,
                    err_text,
                )
                return False

        logger.debug("Voice Chat: Playing track in chat %s (UI mode active)", chat_id)
        self.active_chats[chat_id] = {"source": audio_source, "status": "playing"}
        self.last_error = None
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

    async def leave_chat(self, chat_id: int) -> bool:
        """Alias for stopping audio and leaving group voice chat."""
        return await self.stop_audio(chat_id)

    async def leave_call(self, chat_id: int) -> bool:
        """Alias for stopping audio and leaving group voice chat."""
        return await self.stop_audio(chat_id)

    async def run_vc_diagnostic(self, chat_id: int) -> dict:
        """
        Runs a comprehensive diagnostic of the PyTgCalls playback pipeline on /tmp/aaruu_cache/test.mp3.
        Returns a dictionary of diagnostic results.
        """
        results = {
            "file_exists": False,
            "file_size": 0,
            "ffmpeg_valid": False,
            "ffmpeg_log": "",
            "pytgcalls_connected": False,
            "vc_state": "disconnected",
            "mediastream_created": False,
            "play_success": False,
            "error": None
        }
        
        try:
            # 1. Setup cache & test file
            cache_dir = "/tmp/aaruu_cache"
            test_path = os.path.join(cache_dir, "test.mp3")
            
            # If test.mp3 doesn't exist, try to copy any existing mp3 from cache
            if not os.path.exists(test_path):
                if os.path.exists(cache_dir):
                    mp3_files = [f for f in os.listdir(cache_dir) if f.endswith(".mp3") and f != "test.mp3"]
                    if mp3_files:
                        import shutil
                        shutil.copy(os.path.join(cache_dir, mp3_files[0]), test_path)
                        logger.info("Diagnostic: Copied %s to test.mp3", mp3_files[0])
            
            # Check file presence
            results["file_exists"] = os.path.exists(test_path)
            if results["file_exists"]:
                results["file_size"] = os.path.getsize(test_path)
                
                # 2. FFmpeg validation
                success, ffmpeg_log = await verify_media_file_with_ffmpeg(test_path)
                results["ffmpeg_valid"] = success
                results["ffmpeg_log"] = ffmpeg_log
            else:
                results["error"] = "No test file found in cache."
                return results

            # 3. PyTgCalls connected check
            results["pytgcalls_connected"] = bool(self.pytgcalls and self.is_connected)
            if not results["pytgcalls_connected"]:
                results["error"] = "PyTgCalls or Assistant is not connected/started."
                return results
                
            # 4. VC / Call State
            results["vc_state"] = "active" if chat_id in self.active_chats else "inactive"
            
            # 5. MediaStream creation
            stream_obj = None
            try:
                if MediaStream:
                    from pytgcalls.types import AudioQuality
                    stream_obj = MediaStream(test_path, audio_parameters=AudioQuality.HIGH)
                elif AudioPiped:
                    stream_obj = AudioPiped(test_path)
                else:
                    stream_obj = test_path
                    
                results["mediastream_created"] = (stream_obj is not None)
            except Exception as stream_err:
                results["error"] = f"MediaStream creation failed: {str(stream_err)}"
                return results

            # 6. PyTgCalls.play()
            try:
                if hasattr(self.pytgcalls, "play"):
                    await self.pytgcalls.play(chat_id, stream_obj)
                    results["play_success"] = True
                elif hasattr(self.pytgcalls, "join_group_call"):
                    await self.pytgcalls.join_group_call(chat_id, stream_obj)
                    results["play_success"] = True
                else:
                    results["error"] = "No play or join_group_call method found on PyTgCalls client."
            except Exception as play_err:
                results["error"] = f"PyTgCalls play invocation failed: {str(play_err)}"
                return results
                
            self.active_chats[chat_id] = {"source": test_path, "status": "playing"}
            
        except Exception as general_err:
            results["error"] = f"Unexpected diagnostic error: {str(general_err)}"
            
        return results


voice_assistant = VoiceChatAssistant()

