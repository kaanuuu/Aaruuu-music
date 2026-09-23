"""
Aaruu Music - Native Rich Player & Queue Builder
Constructs sleek, high-fidelity media player layouts using aesthetic Unicode typography
(sans-serif bold, small caps) without raw HTML tags (<b>, <code>) to avoid punchmarks.
Includes instant support redirect to @wzzkaanu.
"""

from typing import Any, Dict, List
from player.models import PlayerState, Track
from player.queue import TrackQueue
from utils.formatting import format_time, render_progress
from utils.typography import to_bold_sans, to_small_caps

SUPPORT_URL = "https://t.me/wzzkaanu"


def build_player_rich_message(state: PlayerState, queue: TrackQueue) -> Dict[str, Any]:
    """
    Constructs the rich player message:
    - Pure Unicode typography (no <b> or <code> punchmarks)
    - Full track details: title, artist, duration, requester
    - Visual progress bar
    - Sleek buttons with small-caps labels
    - Direct support button redirecting to @wzzkaanu
    """
    track: Track = state.current_track or Track(
        track_id="none",
        title="No Track Selected",
        artist="Aaruu Music",
        duration=0,
        thumbnail="https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80",
        source_url="https://telegram.org",
        requester_user_id=0,
        requester_name="System",
    )

    session = state.session_id
    curr_pos = state.current_position
    progress_line = render_progress(curr_pos, track.duration)
    requester_label = state.requested_by.get("name") or track.requester_name or "User"

    # Play/Pause toggle button
    if state.is_paused:
        play_pause_button = {
            "text": "▶ ʀᴇsᴜᴍᴇ",
            "style": "success",
            "callback_data": f"player:resume:{session}",
        }
        status_badge = "⏸ " + to_small_caps("paused")
    else:
        play_pause_button = {
            "text": "⏸ ᴘᴀᴜsᴇ",
            "style": "primary",
            "callback_data": f"player:pause:{session}",
        }
        status_badge = "▶ " + to_small_caps("playing")

    # Row 1: Replay | Pause/Resume | Skip
    row_1_buttons = [
        {
            "text": "↺ ʀᴇᴘʟᴀʏ",
            "style": "primary",
            "callback_data": f"player:replay:{session}",
        },
        play_pause_button,
        {
            "text": "⏭ sᴋɪᴘ",
            "style": "primary",
            "callback_data": f"player:skip:{session}",
        },
    ]

    # Row 2: Queue count badge and Shuffle
    queue_count = len(queue)
    queue_label = f"☷ ǫᴜᴇᴜᴇ ({queue_count})"
    row_2_buttons = [
        {
            "text": queue_label,
            "style": "primary",
            "callback_data": f"player:queue:{session}",
        },
        {
            "text": "🔀 sʜᴜғғʟᴇ",
            "style": "primary",
            "callback_data": f"player:shuffle:{session}",
        },
    ]

    # Row 3: Support (@wzzkaanu) and Close
    row_3_buttons = [
        {
            "text": "💬 sᴜᴘᴘᴏʀᴛ",
            "url": SUPPORT_URL,
        },
        {
            "text": "✖ ᴄʟᴏsᴇ",
            "style": "link",
            "callback_data": f"player:close:{session}",
        },
    ]

    time_str = f"{format_time(curr_pos)} / {format_time(track.duration)}"
    caption_text = (
        f"{to_bold_sans('AARUU MUSIC PLAYER')} • {status_badge}\n\n"
        f"🎵 {to_small_caps('title')}: {track.title}\n"
        f"👤 {to_small_caps('artist')}: {track.artist}\n"
        f"⏱ {to_small_caps('duration')}: {format_time(track.duration)}\n"
        f"🙋 {to_small_caps('requested by')}: @{requester_label}\n\n"
        f"{progress_line}\n"
        f"⏱ {time_str}"
    )

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": to_bold_sans("AARUU MUSIC"),
            "size": 1,
        },
        {
            "type": "photo",
            "photo": {
                "type": "photo",
                "media": track.thumbnail,
            },
        },
        {
            "type": "paragraph",
            "text": caption_text,
        },
        {
            "type": "buttons",
            "buttons": row_1_buttons,
            "align": "center",
        },
        {
            "type": "buttons",
            "buttons": row_2_buttons,
            "align": "center",
        },
        {
            "type": "buttons",
            "buttons": row_3_buttons,
            "align": "center",
        },
    ]

    return {
        "blocks": blocks,
        "type": "rich_message",
    }


def build_queue_rich_message(state: PlayerState, queue: TrackQueue) -> Dict[str, Any]:
    """
    Constructs the rich message for the chat queue:
    Clean aesthetic typography, no raw HTML tags.
    """
    session = state.session_id
    queued_tracks = queue.to_list()
    count = len(queued_tracks)

    now_playing_text = "None"
    if state.current_track and state.is_playing:
        now_playing_text = f"{state.current_track.title} ({format_time(state.current_track.duration)})"

    up_next_lines: List[str] = []
    if queued_tracks:
        for idx, tr in enumerate(queued_tracks[:8], start=1):
            up_next_lines.append(
                f"{idx}. {tr.title} ({format_time(tr.duration)}) - @{tr.requester_name}"
            )
        if len(queued_tracks) > 8:
            up_next_lines.append(f"... +{len(queued_tracks) - 8} " + to_small_caps("more tracks"))
    else:
        up_next_lines.append(to_small_caps("no upcoming tracks in queue. use /play to add songs!"))

    queue_body = (
        f"{to_bold_sans('AARUU MUSIC QUEUE')}\n\n"
        f"▶ {to_small_caps('now playing')}:\n{now_playing_text}\n\n"
        f"📋 {to_small_caps('up next')} ({count} {to_small_caps('upcoming')}):\n"
        + "\n".join(up_next_lines)
    )

    action_buttons_row1 = [
        {
            "text": "🔄 ʀᴇғʀᴇsʜ",
            "style": "primary",
            "callback_data": f"player:queue:{session}",
        },
        {
            "text": "↩ ᴜɴᴅᴏ ʟᴀsᴛ",
            "style": "primary",
            "callback_data": f"queue:undo:{session}",
        },
    ]

    action_buttons_row2 = [
        {
            "text": "💬 sᴜᴘᴘᴏʀᴛ",
            "url": SUPPORT_URL,
        },
        {
            "text": "✖ ᴄʟᴏsᴇ",
            "style": "link",
            "callback_data": f"player:close:{session}",
        },
    ]

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": to_bold_sans("AARUU MUSIC"),
            "size": 1,
        },
        {
            "type": "paragraph",
            "text": queue_body,
        },
        {
            "type": "buttons",
            "buttons": action_buttons_row1,
            "align": "center",
        },
        {
            "type": "buttons",
            "buttons": action_buttons_row2,
            "align": "center",
        },
    ]

    return {
        "blocks": blocks,
        "type": "rich_message",
    }
