"""
Aaruu Music - Rich Start & Help Guide
Renders interactive Rich Message documentation and guide blocks with RichMessageButton navigation.
"""

from typing import Any, Dict, List


def build_start_rich_message(guide_section: str = "home") -> Dict[str, Any]:
    """
    Renders the /start and /help interactive guide with Rich Message buttons.
    """
    guides = {
        "home": (
            "<b>Welcome to Aaruu Music!</b>\n\n"
            "High-fidelity Telegram Music Bot powered by native Rich Messages, "
            "per-chat isolated queues, and 24/7 worker continuity.\n\n"
            "<b>What would you like to do?</b>\n"
            "Choose a guide below to explore commands and features."
        ),
        "getting_started": (
            "<b>🚀 Getting Started</b>\n\n"
            "1. Add <b>Aaruu Music</b> to your Telegram group or use in private.\n"
            "2. Make the bot an administrator if group voice-chat playback is needed.\n"
            "3. Send <code>/play &lt;song name or URL&gt;</code> to begin.\n"
            "4. Control playback with native Rich Message buttons."
        ),
        "find_play": (
            "<b>🔍 Find & Play</b>\n\n"
            "• <code>/play &lt;song name&gt;</code> - Searches YouTube and plays the top result.\n"
            "• <code>/play &lt;link&gt;</code> - Resolves direct YouTube or audio URL.\n"
            "• If audio is already active, songs are automatically added to the queue."
        ),
        "controls": (
            "<b>🎛 Controls</b>\n\n"
            "• <b>↩ Replay</b>: Starts the current track from 0:00.\n"
            "• <b>⏸ Pause / ▶ Resume</b>: Toggles active stream.\n"
            "• <b>≫ Skip</b>: Moves directly to the next queued track.\n"
            "• <code>/seek &lt;seconds&gt;</code>: Jumps to a specific timestamp.\n"
            "• <code>/volume &lt;1-100&gt;</code>: Sets output volume."
        ),
        "queue_repeat": (
            "<b>📋 Queue & Repeat</b>\n\n"
            "• <code>/queue</code>: View upcoming tracks in the chat.\n"
            "• <code>/clear</code>: Clears upcoming songs.\n"
            "• <code>/loop &lt;off|track|queue&gt;</code>: Loops current song or the entire queue.\n"
            "• Tap <b>☷ Queue · N</b> on the player to see real-time queue."
        ),
        "group_settings": (
            "<b>⚙️ Group Settings</b>\n\n"
            "• <code>/settings</code>: Inspect volume, loop mode, and active configuration.\n"
            "• Settings are persisted per chat in SQLite."
        ),
        "troubleshooting": (
            "<b>🛠 Troubleshooting</b>\n\n"
            "• <b>Stale Player Button</b>: If a button says 'This player is no longer active', send <code>/nowplaying</code> to fetch a fresh player.\n"
            "• <b>Unavailable Song</b>: The bot safely catches geo-restricted or deleted videos.\n"
            "• <b>Voice Chat Streaming</b>: Ensure <code>ASSISTANT_SESSION</code> is provided if voice streaming is desired."
        ),
        "group_admins": (
            "<b>🛡 Group Admins</b>\n\n"
            "Chat administrators can manage skip, stop, clear, volume, and settings commands. "
            "Regular group members can request songs and browse the queue."
        ),
        "owner_sudo": (
            "<b>👑 Bot Owner & Sudo</b>\n\n"
            "Configured via <code>OWNER_ID</code> and <code>SUDO_USERS</code>. "
            "Sudo users have global administrative control across all chats.\n\n"
            "<b>User Management:</b>\n"
            "• <code>/block &lt;user_id or reply&gt; [reason]</code> - Ban user from bot\n"
            "• <code>/unblock &lt;user_id or reply&gt;</code> - Unban user\n"
            "• <code>/blocked</code> - List all permanently blocked users"
        ),
    }

    text = guides.get(guide_section, guides["home"])

    # 10 guide buttons in structured rows
    button_rows = [
        [
            {"text": "🚀 Getting started", "style": "primary", "callback_data": "help:getting_started"},
            {"text": "🔍 Find & play", "style": "primary", "callback_data": "help:find_play"},
        ],
        [
            {"text": "🎛 Controls", "style": "primary", "callback_data": "help:controls"},
            {"text": "📋 Queue & repeat", "style": "primary", "callback_data": "help:queue_repeat"},
        ],
        [
            {"text": "⚙️ Group settings", "style": "primary", "callback_data": "help:group_settings"},
            {"text": "🛠 Troubleshooting", "style": "primary", "callback_data": "help:troubleshooting"},
        ],
        [
            {"text": "🛡 Group admins", "style": "primary", "callback_data": "help:group_admins"},
            {"text": "👑 Bot owner & sudo", "style": "primary", "callback_data": "help:owner_sudo"},
        ],
        [
            {"text": "🏠 Home", "style": "primary", "callback_data": "help:home"},
            {"text": "✖ Close", "style": "link", "callback_data": "help:close"},
        ],
    ]

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": "Aaruu Music",
            "size": 1,
        },
        {
            "type": "paragraph",
            "text": "<b>What would you like to do?</b>\nChoose a guide below.",
        },
        {
            "type": "paragraph",
            "text": text,
        },
    ]

    for row in button_rows:
        blocks.append(
            {
                "type": "buttons",
                "buttons": row,
                "align": "center",
            }
        )

    return {
        "blocks": blocks,
        "type": "rich_message",
    }
