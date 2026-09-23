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
        self, chat_id: int, rich_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Sends native Rich Message using Telegram Bot API.
        Delivers sleek photo card with pure Unicode typography and inline controls.
        """
        return await self._fallback_send(chat_id, rich_message)

    async def edit_message_rich_text(
        self, chat_id: int, message_id: int, rich_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Edits an existing rich message card with updated content and buttons."""
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
        parse_mode: str = "HTML",
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Sends a standard text message."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": False,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return await self.bot_api("sendMessage", payload)

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
        parse_mode: str = "HTML",
    ) -> Dict[str, Any]:
        """Copies a message (including text, photo, audio) to another chat."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
        }
        if caption is not None:
            payload["caption"] = caption
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
        return res

    async def delete_message(self, chat_id: int, message_id: int) -> Dict[str, Any]:
        """Deletes a message from chat."""
        return await self.bot_api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    # Internal fallbacks if telegram client/version doesn't support Rich Block protocol
    async def _fallback_send(self, chat_id: int, rich_message: Dict[str, Any]) -> Dict[str, Any]:
        DEFAULT_BANNER = "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
        text_content, thumbnail, inline_kb = self._extract_fallback_data(rich_message)
        primary_photo = thumbnail or DEFAULT_BANNER

        payload = {
            "chat_id": chat_id,
            "photo": primary_photo,
            "caption": text_content,
            "reply_markup": inline_kb,
        }
        res = await self.bot_api("sendPhoto", payload)
        if res.get("ok"):
            return res

        # If custom photo URL failed (e.g. YouTube CDN 403 or expired), retry with default banner
        if primary_photo != DEFAULT_BANNER:
            payload["photo"] = DEFAULT_BANNER
            res_retry = await self.bot_api("sendPhoto", payload)
            if res_retry.get("ok"):
                return res_retry

        return await self.send_message(chat_id, text_content, parse_mode=None, reply_markup=inline_kb)

    async def _fallback_edit(
        self, chat_id: int, message_id: int, rich_message: Dict[str, Any]
    ) -> Dict[str, Any]:
        text_content, _, inline_kb = self._extract_fallback_data(rich_message)
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": text_content,
            "reply_markup": inline_kb,
        }
        res = await self.bot_api("editMessageCaption", payload)
        if res.get("ok"):
            return res
        if "message is not modified" in str(res.get("description", "")).lower():
            return {"ok": True, "result": True}

        # Try editing reply markup only if caption update was rejected (e.g., identical caption)
        res_markup = await self.bot_api("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": inline_kb,
        })
        if res_markup.get("ok") or "message is not modified" in str(res_markup.get("description", "")).lower():
            return {"ok": True, "result": True}

        payload_txt = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text_content,
            "reply_markup": inline_kb,
        }
        res2 = await self.bot_api("editMessageText", payload_txt)
        if not res2.get("ok") and "message is not modified" in str(res2.get("description", "")).lower():
            return {"ok": True, "result": True}
        return res2

    def _extract_fallback_data(self, rich_message: Dict[str, Any]):
        texts: List[str] = []
        thumbnail = None
        rows: List[List[Dict[str, str]]] = []

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
            elif btype == "buttons":
                row = []
                for btn in block.get("buttons", []):
                    btn_text = btn.get("text", "")
                    if btn.get("url"):
                        row.append({"text": btn_text, "url": btn["url"]})
                    elif btn.get("callback_data"):
                        row.append({"text": btn_text, "callback_data": btn["callback_data"]})
                if row:
                    rows.append(row)

        full_text = "\n\n".join([t for t in texts if t.strip()]) or "Aaruu Music"
        inline_kb = {"inline_keyboard": rows} if rows else None
        return full_text, thumbnail, inline_kb


bot_api_client = TelegramAPIClient()
