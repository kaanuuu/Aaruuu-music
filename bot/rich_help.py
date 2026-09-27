"""
Aaruu Music - Rich Start & Help Guide
Renders aesthetic documentation and interactive guides using Unicode typography
(sans-serif bold, small caps) without raw HTML tags.
Hides owner-only commands from non-owners and provides 1-tap support redirection to @wzzkaanu.
"""

from typing import Any, Dict, List
from utils.typography import to_bold_sans, to_small_caps

SUPPORT_URL = "https://t.me/wzzkaanu"


def build_start_rich_message(guide_section: str = "home", is_owner: bool = False) -> Dict[str, Any]:
    """
    Renders the /start and /help interactive guide with clean typography.
    Owner-only sections and buttons are hidden if is_owner is False.
    """
    guides = {
        "home": (
            f"⚡ {to_bold_sans('WELCOME TO AARUU MUSIC')}\n\n"
            f"High-fidelity Telegram Music Bot with per-chat isolated queues "
            f"and 24/7 continuous worker engine.\n\n"
            f"❓ {to_bold_sans('CHOOSE A GUIDE BELOW')}:\n"
            f"Tap any button below to explore commands and features."
        ),
        "getting_started": (
            f"🚀 {to_bold_sans('GETTING STARTED')}\n\n"
            f"1. Add Aaruu Music to your group or chat in private.\n"
            f"2. Promote the bot to administrator to stream in group Voice Chats.\n"
            f"3. Send /play <song name> or /vplay <video name> to stream live.\n"
            f"4. Manage music live with the interactive control panel."
        ),
        "find_play": (
            f"🔍 {to_bold_sans('FIND & PLAY')}\n\n"
            f"• /play <song name> - Searches full audio and streams top result\n"
            f"• /vplay <video name> - Streams video directly into group Voice Chat\n"
            f"• /search <keyword> - Searches songs with interactive 1-5 selection\n"
            f"• /song <name> - Search and play a specific song\n"
            f"• /nowplaying - Opens active player control panel"
        ),
        "all_commands": (
            f"📜 {to_bold_sans('ALL USER COMMANDS')}\n\n"
            f"🎵 {to_bold_sans('PLAYBACK')}:\n"
            f"• /play <song> - Stream audio music in Voice Chat\n"
            f"• /vplay <video> - Stream video directly in Voice Chat\n"
            f"• /search <song> - Interactive 1-5 song search selection\n"
            f"• /song <name> - Search and play specific track\n"
            f"• /nowplaying - Interactive rich playback control card\n\n"
            f"🎛 {to_bold_sans('CONTROLS')}:\n"
            f"• /pause - Pause current playback\n"
            f"• /resume - Resume paused playback\n"
            f"• /replay - Replay current song from 0:00\n"
            f"• /skip - Skip to next queued track\n"
            f"• /stop - Stop playback and leave Voice Chat\n"
            f"• /seek <seconds> - Jump to timestamp (e.g. /seek 60)\n"
            f"• /volume <1-100> - Adjust playback volume\n\n"
            f"📋 {to_bold_sans('QUEUE & PLAYLIST')}:\n"
            f"• /queue - View upcoming queued tracks\n"
            f"• /shuffle - Shuffle queued tracks randomly\n"
            f"• /clear - Clear all upcoming tracks\n"
            f"• /loop <off|track|queue> - Set loop repeat mode\n\n"
            f"⚙️ {to_bold_sans('INFO & SETTINGS')}:\n"
            f"• /vc - Check Voice Chat connection & audio status\n"
            f"• /settings - View chat volume, loop status, and rules\n"
            f"• /ping - Check bot response latency and uptime\n"
            f"• /help - Interactive help guide\n"
            f"• /start - Start guide & quick setup\n\n"
            f"💡 All commands are also available by typing / in chat!"
        ),
        "controls": (
            f"🎛 {to_bold_sans('CONTROLS')}\n\n"
            f"• ↺ Replay: Restarts the current track from 0:00\n"
            f"• ⏸ Pause / ▶ Resume: Toggles audio playback\n"
            f"• ⏭ Skip: Moves immediately to the next queued track\n"
            f"• /seek <seconds>: Jumps to a specific timestamp\n"
            f"• /volume <1-100>: Adjusts playback volume"
        ),
        "queue_repeat": (
            f"📋 {to_bold_sans('QUEUE & REPEAT')}\n\n"
            f"• /queue - View upcoming tracks in chat\n"
            f"• /clear - Clears all upcoming tracks\n"
            f"• /shuffle - Shuffles queue order randomly\n"
            f"• /loop <off|track|queue> - Sets loop mode"
        ),
        "group_settings": (
            f"⚙️ {to_bold_sans('GROUP SETTINGS')}\n\n"
            f"• /settings - View chat volume, loop status, and queue rules\n"
            f"• Settings are saved permanently per chat in SQLite."
        ),
        "group_admins": (
            f"🛡 {to_bold_sans('GROUP ADMINS')}\n\n"
            f"Admins have authority to skip, pause, clear queue, and change volume.\n"
            f"Regular members can search songs and add to queue."
        ),
        "troubleshooting": (
            f"🛠 {to_bold_sans('TROUBLESHOOTING')}\n\n"
            f"• If playback freezes or desyncs, send /ping or /nowplaying\n"
            f"• For help, tap the Support button below to reach the owner directly."
        ),
    }

    # Only expose owner section if is_owner is True
    if is_owner:
        guides["owner_sudo"] = (
            f"👑 {to_bold_sans('BOT OWNER CONTROLS')}\n\n"
            f"• /stats - Global users, groups, active playback, uptime\n"
            f"• /block <user_id> [reason] - Permanently ban a user\n"
            f"• /unblock <user_id> - Remove ban\n"
            f"• /blocked - View list of all blocked users\n"
            f"• /broadcast <text> - Send announcement to all groups\n"
            f"• /sysinfo - Server CPU, RAM, and Disk metrics\n"
            f"• /restart - Gracefully reload worker process"
        )
        guides["closed"] = (
            f"✖ {to_bold_sans('GUIDE CLOSED')}\n\n"
            f"{to_small_caps('guide closed. tap any category below to reopen guides anytime.')}"
        )

    is_closed = (guide_section in ("close", "closed"))
    active_section = "closed" if is_closed else guide_section
    text = guides.get(active_section, guides["home"])

    if active_section in ("home", "closed"):
        row_0 = [
            {"text": "≡ " + to_small_caps("all user commands"), "style": "primary", "callback_data": "help:all_commands"},
        ]
        row_1 = [
            {"text": "› " + to_small_caps("getting started"), "style": "primary", "callback_data": "help:getting_started"},
            {"text": "› " + to_small_caps("find & play"), "style": "primary", "callback_data": "help:find_play"},
        ]
        row_2 = [
            {"text": "› " + to_small_caps("controls"), "style": "primary", "callback_data": "help:controls"},
            {"text": "› " + to_small_caps("queue & repeat"), "style": "primary", "callback_data": "help:queue_repeat"},
        ]
        row_3 = [
            {"text": "› " + to_small_caps("group settings"), "style": "primary", "callback_data": "help:group_settings"},
            {"text": "› " + to_small_caps("group admins"), "style": "primary", "callback_data": "help:group_admins"},
        ]
        row_4 = [
            {"text": "› " + to_small_caps("troubleshooting"), "style": "primary", "callback_data": "help:troubleshooting"},
        ]
        if is_owner:
            row_4.append({"text": "› " + to_small_caps("owner & sudo"), "style": "primary", "callback_data": "help:owner_sudo"})

        close_btn_text = "■ " + to_small_caps("closed") if is_closed else "■ " + to_small_caps("close")
        close_btn_cb = "help:home" if is_closed else "help:close"
        row_5 = [
            {"text": "≡ " + to_small_caps("support"), "url": SUPPORT_URL},
            {"text": close_btn_text, "style": "link", "callback_data": close_btn_cb},
        ]
        button_rows = [row_0, row_1, row_2, row_3, row_4, row_5]
    else:
        # Inside a specific sub-guide: provide clean back navigation without dumping all old buttons
        row_back = [
            {"text": "‹ " + to_small_caps("back to guide menu"), "style": "primary", "callback_data": "help:home"},
            {"text": "≡ " + to_small_caps("support"), "url": SUPPORT_URL},
        ]
        row_close = [
            {"text": "■ " + to_small_caps("close guide"), "style": "link", "callback_data": "help:close"},
        ]
        button_rows = [row_back, row_close]

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": to_bold_sans("AARUU MUSIC GUIDE"),
            "size": 1,
        },
        {
            "type": "photo",
            "photo": {
                "type": "photo",
                "media": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80",
            },
        },
        {
            "type": "paragraph",
            "text": text,
        },
    ]

    for row in button_rows:
        blocks.append({
            "type": "buttons",
            "buttons": row,
            "align": "center",
        })

    return {
        "blocks": blocks,
        "type": "rich_message",
    }


# Centralized alias ensuring unified Rich UI builder
build_help_rich_ui = build_start_rich_message

