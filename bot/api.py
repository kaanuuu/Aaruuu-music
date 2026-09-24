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

    async def send_rich_message(
        self, chat_id: int, rich_message: Dict[str, Any], reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Sends native Rich Message using Telegram Bot API.
        Delivers sleek photo card with pure Unicode typography and in-bubble round corner buttons.
        """
        components_markup = self._extract_components(rich_message)
        payload = {
            "chat_id": chat_id,
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload["reply_markup"] = components_markup
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
        Guarantees that the complete component layout is preserved and never dropped.
        """
        components_markup = self._extract_components(rich_message)
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload["reply_markup"] = components_markup

        # 1. Primary: editRichMessage (the direct update counterpart to sendRichMessage)
        res = await self.bot_api("editRichMessage", payload)
        if res.get("ok"):
            return res
        if "message is not modified" in str(res.get("description", "")).lower():
            return {"ok": True, "result": True}

        # 2. Secondary: editMessageRichText
        res2 = await self.bot_api("editMessageRichText", payload)
        if res2.get("ok"):
            return res2
        if "message is not modified" in str(res2.get("description", "")).lower():
            return {"ok": True, "result": True}

        # 3. Tertiary: editMessageRich
        res3 = await self.bot_api("editMessageRich", payload)
        if res3.get("ok"):
            return res3
        if "message is not modified" in str(res3.get("description", "")).lower():
            return {"ok": True, "result": True}

        # Fallback updating the media/caption card with complete components preserved
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

    def _extract_components(self, rich_message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extracts the complete button component structure from the Rich Message blocks.
        Always returns the complete layout of buttons so no component is ever dropped.
        """
        blocks = rich_message.get("blocks", [])
        rows = []
        for block in blocks:
            if block.get("type") == "buttons":
                btns = block.get("buttons", [])
                row = []
                for b in btns:
                    btn_dict: Dict[str, Any] = {"text": b.get("text", "")}
                    if "callback_data" in b:
                        btn_dict["callback_data"] = b["callback_data"]
                    if "url" in b:
                        btn_dict["url"] = b["url"]
                    row.append(btn_dict)
                if row:
                    rows.append(row)
        return {"inline_keyboard": rows} if rows else {}

    # Internal fallbacks if telegram client/version requires photo/text envelope while preserving Rich UI Button Blocks
    async def _fallback_send(
        self, chat_id: int, rich_message: Dict[str, Any], reply_to_message_id: Optional[int] = None
    ) -> Dict[str, Any]:
        DEFAULT_BANNER = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
        text_content, thumbnail = self._extract_fallback_data(rich_message)
        primary_photo = thumbnail or DEFAULT_BANNER
        components_markup = self._extract_components(rich_message)

        # Enforce Telegram photo caption limit (max 1024 chars)
        safe_caption = text_content[:1000] if len(text_content) > 1000 else text_content

        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "photo": primary_photo,
            "caption": safe_caption,
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload["reply_markup"] = components_markup
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id

        res = await self.bot_api("sendPhoto", payload)
        if res.get("ok"):
            return res

        # If custom photo URL failed (e.g. YouTube CDN 403 or expired), retry with default banner
        if primary_photo != DEFAULT_BANNER:
            payload["photo"] = DEFAULT_BANNER
            res_retry = await self.bot_api("sendPhoto", payload)
            if res_retry.get("ok"):
                return res_retry

        payload_txt: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text_content,
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload_txt["reply_markup"] = components_markup
        if reply_to_message_id:
            payload_txt["reply_to_message_id"] = reply_to_message_id
        return await self.bot_api("sendMessage", payload_txt)

    async def _fallback_edit(
        self, chat_id: int, message_id: int, rich_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        text_content, thumbnail = self._extract_fallback_data(rich_message)
        safe_caption = text_content[:1000] if len(text_content) > 1000 else text_content
        DEFAULT_BANNER = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
        primary_photo = thumbnail or DEFAULT_BANNER
        components_markup = self._extract_components(rich_message)

        # 1. Try editing photo caption with complete components preserved
        payload_cap: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": safe_caption,
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload_cap["reply_markup"] = components_markup
        res = await self.bot_api("editMessageCaption", payload_cap)
        if res.get("ok"):
            return res
        desc = str(res.get("description", "")).lower()
        if "message is not modified" in desc:
            return {"ok": True, "result": True}

        # 2. Try editing media (photo + caption) in-place with complete components preserved
        payload_media: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": {
                "type": "photo",
                "media": primary_photo,
                "caption": safe_caption,
            },
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload_media["reply_markup"] = components_markup
        res_media = await self.bot_api("editMessageMedia", payload_media)
        if res_media.get("ok"):
            return res_media
        if "message is not modified" in str(res_media.get("description", "")).lower():
            return {"ok": True, "result": True}

        # 3. Try editing standard text message with complete components preserved
        payload_txt: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text_content,
            "rich_message": rich_message,
            "blocks": rich_message.get("blocks", []),
            "components": rich_message.get("blocks", []),
        }
        if components_markup.get("inline_keyboard"):
            payload_txt["reply_markup"] = components_markup
        res_txt = await self.bot_api("editMessageText", payload_txt)
        if res_txt.get("ok") or "message is not modified" in str(res_txt.get("description", "")).lower():
            return {"ok": True, "result": True}

        # 4. Try updating reply markup directly if caption/text didn't change
        if components_markup.get("inline_keyboard"):
            payload_markup = {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": components_markup,
            }
            res_markup = await self.bot_api("editMessageReplyMarkup", payload_markup)
            if res_markup.get("ok") or "message is not modified" in str(res_markup.get("description", "")).lower():
                return {"ok": True, "result": True}

        return res_txt

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

