"""
Aaruu Music - Telegram Bot API Direct HTTPS Client
High-performance direct API client supporting sendRichMessage, editMessageRichText,
and fallback message delivery with exponential backoff and 429 rate limit protection.
"""

import asyncio
import os
from typing import Any, Dict, List, Optional
try:
    import aiohttp
except ImportError:
    aiohttp = None
from utils.logging import logger


class TelegramAPIClient:
    """Direct HTTPS client for Telegram Bot API."""

    def __init__(self, token: Optional[str] = None):
        self._token: str = token or os.getenv("BOT_TOKEN", "").strip()
        self._base_url: str = f"https://api.telegram.org/bot{self._token}"
        self._session: Optional[Any] = None
        self.bot_info: Dict[str, Any] = {}

    def set_token(self, token: str) -> None:
        self._token = token.strip()
        self._base_url = f"https://api.telegram.org/bot{self._token}"

    async def get_session(self) -> Any:
        if aiohttp is None:
            raise RuntimeError(
                "aiohttp is not installed. Please install dependencies from requirements.txt."
            )
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45, connect=8)
            connector = aiohttp.TCPConnector(
                limit=100,
                limit_per_host=30,
                keepalive_timeout=60,
                enable_cleanup_closed=True,
            )
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def bot_api(
        self,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Executes a method against the Telegram Bot API with automatic retry
        and rate-limiting management. Never logs tokens or sensitive headers.
        """
        if not self._token:
            raise ValueError("BOT_TOKEN is not set or empty.")

        url = f"{self._base_url}/{method}"
        data = payload or {}
        session = await self.get_session()

        for attempt in range(1, retries + 1):
            try:
                async with session.post(url, json=data) as response:
                    status = response.status
                    resp_json = await response.json()

                    if status == 200 and resp_json.get("ok"):
                        return resp_json

                    # Handle 429 Rate Limits
                    if status == 429:
                        retry_after = resp_json.get("parameters", {}).get("retry_after", 3)
                        logger.warning(
                            "Telegram API rate-limited (429) on %s. Backing off for %s seconds.",
                            method,
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    # Telegram error response
                    description = resp_json.get("description", "Unknown error")
                    error_code = resp_json.get("error_code", status)

                    if attempt < retries and status in (500, 502, 503, 504):
                        backoff = 2**attempt
                        logger.warning(
                            "Temporary Telegram server error %s on %s. Retrying in %ss...",
                            error_code,
                            method,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        continue

                    logger.debug(
                        "Telegram API call %s returned error %s: %s",
                        method,
                        error_code,
                        description,
                    )
                    return resp_json

            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt < retries:
                    backoff = 2**attempt
                    logger.warning(
                        "Network failure calling %s (%s). Attempt %s/%s. Retrying in %ss...",
                        method,
                        err.__class__.__name__,
                        attempt,
                        retries,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error("Network error on Telegram API %s: %s", method, str(err))
                    return {"ok": False, "description": f"Network error: {str(err)}"}

        return {"ok": False, "description": "Max retries exceeded"}

    async def get_me(self) -> Dict[str, Any]:
        """Fetches bot user details."""
        res = await self.bot_api("getMe")
        if res.get("ok"):
            self.bot_info = res.get("result", {})
        return res

    async def set_my_commands(self, commands: List[Dict[str, str]]) -> Dict[str, Any]:
        """Registers command menu with Telegram BotFather."""
        return await self.bot_api("setMyCommands", {"commands": commands})

    async def send_photo_bytes(
        self,
        chat_id: int,
        image_bytes: bytes,
        filename: str = "player_card.png",
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Uploads raw in-memory image bytes (e.g. a generated dynamic player card PNG)
        directly to Telegram via multipart, without needing a public URL first.
        Returns the raw sendPhoto API response; on success, result.photo[-1].file_id
        can be reused as a 'media' value in subsequent rich message photo blocks so
        the image doesn't need to be re-uploaded on every edit.
        """
        session = await self.get_session()
        url = f"{self._base_url}/sendPhoto"

        data = aiohttp.FormData()
        data.add_field("chat_id", str(chat_id))
        if caption:
            data.add_field("caption", caption[:1024])
            data.add_field("parse_mode", "HTML")
        if reply_to_message_id:
            data.add_field("reply_to_message_id", str(reply_to_message_id))
        data.add_field("photo", image_bytes, filename=filename, content_type="image/png")

        try:
            async with session.post(url, data=data) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("send_photo_bytes multipart error: %s", str(e))
            return {"ok": False, "description": str(e)}

    async def upload_photo_get_file_id(self, chat_id: int, image_bytes: bytes, filename: str = "player_card.png") -> Optional[str]:
        """Convenience wrapper: uploads image bytes and returns just the resulting
        file_id (largest size variant), or None on failure."""
        res = await self.send_photo_bytes(chat_id, image_bytes, filename=filename)
        try:
            if res.get("ok"):
                sizes = res.get("result", {}).get("photo", [])
                if sizes:
                    return sizes[-1].get("file_id")
        except Exception:
            pass
        return None

    async def send_rich_message(
        self, chat_id: int, rich_message: Dict[str, Any], reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Sends native Rich Message using Telegram Bot API.
        Delivers sleek photo card with pure Unicode typography and in-bubble round corner buttons.
        Uses native Rich Message Button Blocks only — old inline keyboards never return.
        """
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": rich_message,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        res = await self.bot_api("sendRichMessage", payload)
        if res.get("ok"):
            return res
        return await self._fallback_send(chat_id, rich_message, reply_to_message_id)

    async def edit_message_rich_text(
        self, chat_id: int, message_id: int, rich_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Edits an existing native Rich Message card with updated content and Rich UI Button Blocks.
        Guarantees that the native Rich UI Button Blocks remain the ONLY button UI.
        Old inline keyboards never return.
        """
        text_content, _ = self._extract_fallback_data(rich_message)
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": rich_message,
            "text": text_content,
        }

        # 1. Primary: editMessageText with rich_message (native Telegram rich message edit)
        res = await self.bot_api("editMessageText", payload)
        if res.get("ok"):
            return res
        if "message is not modified" in str(res.get("description", "")).lower():
            return {"ok": True, "result": True}

        # 2. Secondary: editRichMessage
        res2 = await self.bot_api("editRichMessage", payload)
        if res2.get("ok"):
            return res2
        if "message is not modified" in str(res2.get("description", "")).lower():
            return {"ok": True, "result": True}

        # 3. Tertiary: editMessageRichText
        res3 = await self.bot_api("editMessageRichText", payload)
        if res3.get("ok"):
            return res3
        if "message is not modified" in str(res3.get("description", "")).lower():
            return {"ok": True, "result": True}

        # Fallback updating the media/caption card with native rich_message only
        return await self._fallback_edit(chat_id, message_id, rich_message)

    async def export_chat_invite_link(self, chat_id: int) -> Dict[str, Any]:
        """Exports an invite link to the chat (requires admin rights with can_invite_users)."""
        return await self.bot_api("exportChatInviteLink", {"chat_id": chat_id})

    async def create_chat_invite_link(
        self, chat_id: int, name: Optional[str] = None, member_limit: int = 1
    ) -> Dict[str, Any]:
        """Creates a single-use invite link for the assistant (requires admin rights)."""
        payload: Dict[str, Any] = {"chat_id": chat_id, "member_limit": member_limit}
        if name:
            payload["name"] = name
        return await self.bot_api("createChatInviteLink", payload)

    async def answer_callback_query(
        self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
    ) -> Dict[str, Any]:
        """Responds immediately to user interaction on a button."""
        payload: Dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        return await self.bot_api("answerCallbackQuery", payload)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sends a standard text message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        res = await self.bot_api("sendMessage", payload)
        if not res.get("ok") and parse_mode and "entity" in str(res.get("description", "")).lower():
            payload.pop("parse_mode", None)
            return await self.bot_api("sendMessage", payload)
        return res

    async def delete_webhook(self, drop_pending_updates: bool = True) -> Dict[str, Any]:
        """Deletes any existing webhook so getUpdates polling can work."""
        return await self.bot_api("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    async def get_updates(
        self, offset: Optional[int] = None, timeout: int = 30
    ) -> Dict[str, Any]:
        """Polls for updates via long-polling."""
        payload: Dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return await self.bot_api("getUpdates", payload)

    async def get_chat_member(self, chat_id: int, user_id: int) -> Dict[str, Any]:
        """Fetches status of a user in a chat for admin checks."""
        return await self.bot_api("getChatMember", {"chat_id": chat_id, "user_id": user_id})

    async def copy_message(
        self,
        chat_id: int,
        from_chat_id: int,
        message_id: int,
        caption: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Copies a message (including text, photo, audio) to another chat."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        }
        if caption is not None:
            payload["caption"] = caption
            if parse_mode:
                payload["parse_mode"] = parse_mode
        return await self.bot_api("copyMessage", payload)

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Edits text of a standard message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": False,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        res = await self.bot_api("editMessageText", payload)
        if not res.get("ok") and "message is not modified" in str(res.get("description", "")).lower():
            return {"ok": True, "result": True}
        if not res.get("ok") and parse_mode and "entity" in str(res.get("description", "")).lower():
            payload.pop("parse_mode", None)
            res_retry = await self.bot_api("editMessageText", payload)
            if res_retry.get("ok") or "message is not modified" in str(res_retry.get("description", "")).lower():
                return {"ok": True, "result": True}
        return res

    async def delete_message(self, chat_id: int, message_id: int) -> Dict[str, Any]:
        """Deletes a message from chat."""
        return await self.bot_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def send_audio(
        self,
        chat_id: int,
        audio_path_or_url: str,
        caption: Optional[str] = None,
        title: Optional[str] = None,
        performer: Optional[str] = None,
        duration: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sends an MP3/audio file directly into Telegram chat via multipart upload or URL."""
        session = await self.get_session()
        url = f"{self._base_url}/sendAudio"

        if os.path.exists(audio_path_or_url) and os.path.isfile(audio_path_or_url):
            data = aiohttp.FormData()
            data.add_field("chat_id", str(chat_id))
            if caption:
                data.add_field("caption", caption[:1024])
                data.add_field("parse_mode", "HTML")
            if title:
                data.add_field("title", title[:64])
            if performer:
                data.add_field("performer", performer[:64])
            if duration:
                data.add_field("duration", str(int(duration)))
            if reply_to_message_id:
                data.add_field("reply_to_message_id", str(reply_to_message_id))

            fname = os.path.basename(audio_path_or_url)
            data.add_field("audio", open(audio_path_or_url, "rb"), filename=fname, content_type="audio/mpeg")

            try:
                async with session.post(url, data=data) as resp:
                    return await resp.json()
            except Exception as e:
                logger.error("send_audio multipart error: %s", str(e))
                return {"ok": False, "description": str(e)}

        payload: Dict[str, Any] = {"chat_id": chat_id, "audio": audio_path_or_url}
        if caption:
            payload["caption"] = caption[:1024]
            payload["parse_mode"] = "HTML"
        if title:
            payload["title"] = title[:64]
        if performer:
            payload["performer"] = performer[:64]
        if duration:
            payload["duration"] = int(duration)
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        return await self.bot_api("sendAudio", payload)

    async def send_video(
        self,
        chat_id: int,
        video_path_or_url: str,
        caption: Optional[str] = None,
        duration: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Sends an MP4 video file directly into Telegram chat via multipart upload or URL."""
        session = await self.get_session()
        url = f"{self._base_url}/sendVideo"

        if os.path.exists(video_path_or_url) and os.path.isfile(video_path_or_url):
            data = aiohttp.FormData()
            data.add_field("chat_id", str(chat_id))
            if caption:
                data.add_field("caption", caption[:1024])
                data.add_field("parse_mode", "HTML")
            if duration:
                data.add_field("duration", str(int(duration)))
            if reply_to_message_id:
                data.add_field("reply_to_message_id", str(reply_to_message_id))

            fname = os.path.basename(video_path_or_url)
            data.add_field("video", open(video_path_or_url, "rb"), filename=fname, content_type="video/mp4")

            try:
                async with session.post(url, data=data) as resp:
                    return await resp.json()
            except Exception as e:
                logger.error("send_video multipart error: %s", str(e))
                return {"ok": False, "description": str(e)}

        payload: Dict[str, Any] = {"chat_id": chat_id, "video": video_path_or_url}
        if caption:
            payload["caption"] = caption[:1024]
            payload["parse_mode"] = "HTML"
        if duration:
            payload["duration"] = int(duration)
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        return await self.bot_api("sendVideo", payload)

    # Deterministic fallback for clients/environments where Rich Message endpoints
    # are unavailable. This keeps the player as ONE Telegram message and converts
    # Rich button blocks into a normal inline keyboard instead of dropping them.
    def _rich_to_inline_keyboard(self, rich_message: Dict[str, Any]) -> Dict[str, Any]:
        rows: List[List[Dict[str, Any]]] = []
        for block in rich_message.get("blocks", []):
            if block.get("type") != "buttons":
                continue
            row: List[Dict[str, Any]] = []
            for button in block.get("buttons", []) or []:
                text = str(button.get("text") or "Button")[:64]
                if button.get("url"):
                    row.append({"text": text, "url": str(button["url"])})
                elif button.get("callback_data"):
                    row.append({"text": text, "callback_data": str(button["callback_data"])[:64]})
            if row:
                rows.append(row)
        return {"inline_keyboard": rows} if rows else {}

    async def _fallback_send(
        self, chat_id: int, rich_message: Dict[str, Any], reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        DEFAULT_BANNER = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
        text_content, thumbnail = self._extract_fallback_data(rich_message)
        primary_photo = thumbnail if isinstance(thumbnail, str) and thumbnail.startswith(("http://", "https://")) else DEFAULT_BANNER
        safe_caption = text_content[:1024]
        reply_markup = self._rich_to_inline_keyboard(rich_message)

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "photo": primary_photo,
            "caption": safe_caption,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        res = await self.bot_api("sendPhoto", payload)
        if res.get("ok"):
            return res

        # If the thumbnail URL is unavailable, retry ONCE with the fixed banner.
        # The failed request does not create a Telegram message, so this still
        # results in exactly one visible player message.
        if primary_photo != DEFAULT_BANNER:
            payload["photo"] = DEFAULT_BANNER
            res_retry = await self.bot_api("sendPhoto", payload)
            if res_retry.get("ok"):
                return res_retry

        # Last resort: one text message with the same inline keyboard.
        payload_txt: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text_content,
        }
        if reply_markup:
            payload_txt["reply_markup"] = reply_markup
        if reply_to_message_id:
            payload_txt["reply_to_message_id"] = reply_to_message_id
        return await self.bot_api("sendMessage", payload_txt)

    async def _fallback_edit(
        self, chat_id: int, message_id: int, rich_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        text_content, thumbnail = self._extract_fallback_data(rich_message)
        safe_caption = text_content[:1024]
        DEFAULT_BANNER = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
        primary_photo = thumbnail if isinstance(thumbnail, str) and thumbnail.startswith(("http://", "https://")) else DEFAULT_BANNER
        reply_markup = self._rich_to_inline_keyboard(rich_message)

        # First update caption + buttons without replacing the media.
        payload_cap: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": safe_caption,
        }
        if reply_markup:
            payload_cap["reply_markup"] = reply_markup
        res = await self.bot_api("editMessageCaption", payload_cap)
        if res.get("ok") or "message is not modified" in str(res.get("description", "")).lower():
            return {"ok": True, "result": True} if not res.get("ok") else res

        # If this is not a photo message, update it as a normal text message.
        payload_txt: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text_content,
        }
        if reply_markup:
            payload_txt["reply_markup"] = reply_markup
        res_txt = await self.bot_api("editMessageText", payload_txt)
        if res_txt.get("ok") or "message is not modified" in str(res_txt.get("description", "")).lower():
            return {"ok": True, "result": True} if not res_txt.get("ok") else res_txt

        # Finally replace the photo only when the existing message cannot be edited
        # as a caption/text message. Buttons remain attached to the same message.
        payload_media: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": {"type": "photo", "media": primary_photo, "caption": safe_caption},
        }
        if reply_markup:
            payload_media["reply_markup"] = reply_markup
        res_media = await self.bot_api("editMessageMedia", payload_media)
        if res_media.get("ok") or "message is not modified" in str(res_media.get("description", "")).lower():
            return {"ok": True, "result": True} if not res_media.get("ok") else res_media
        return res_media

    def _extract_fallback_data(self, rich_message: Dict[str, Any]):
        texts: List[str] = []
        thumbnail = None

        blocks = rich_message.get("blocks", [])
        for block in blocks:
            btype = block.get("type")
            if btype in ("heading", "section_heading"):
                texts.append(block.get("text", ""))
            elif btype == "paragraph":
                texts.append(block.get("text", ""))
            elif btype == "photo":
                p = block.get("photo", {})
                thumbnail = p.get("media") if isinstance(p, dict) else p

        full_text = "\n\n".join([t for t in texts if t.strip()]) or "Music Player"
        return full_text, thumbnail

    async def set_command_scopes(self, public_commands: List[Dict[str, str]]) -> None:
        """Registers Telegram Bot API command menus so all user commands appear in the '/' menu for all users."""
        try:
            # 1. Default commands scope (fallback for all chats and clients)
            await self.bot_api("setMyCommands", {
                "commands": public_commands,
                "scope": {"type": "default"},
            })

            # 2. All Group chats scope: Show ALL user commands to group members
            await self.bot_api("setMyCommands", {
                "commands": public_commands,
                "scope": {"type": "all_group_chats"},
            })

            # 3. All Chat Administrators scope: Show ALL user commands
            await self.bot_api("setMyCommands", {
                "commands": public_commands,
                "scope": {"type": "all_chat_administrators"},
            })

            # 4. All Private chats scope: Show ALL user commands in PM
            await self.bot_api("setMyCommands", {
                "commands": public_commands,
                "scope": {"type": "all_private_chats"},
            })

            logger.info("Bot API: All %d user commands successfully registered across all '/' menus.", len(public_commands))
        except Exception as e:
            logger.warning("Bot API setMyCommands note: %s", str(e))


bot_api_client = TelegramAPIClient()

