"""
Aaruu Music - Native Rich Message Player Builder
Constructs structured InputRichMessage payloads complying with Telegram Bot API specifications.
"""

from typing import Any, Dict, List
from player.models import PlayerState, Track
from player.queue import TrackQueue
from utils.escaping import escape_html
from utils.formatting import format_queue_badge, format_time, render_progress


def build_player_rich_message(state: PlayerState, queue: TrackQueue) -> Dict[str, Any]:
    """
    Constructs the native Rich Message for the active music player:
    - Title heading
    - Requester information
    - Large album cover photo block
    - Title, artist, audio duration, requester username
    - Visual text progress line
    - Player controls: [ ↩ Replay ] [ ⏸ Pause / ▶ Resume ] [ ≫ Skip ]
    - Queue button: [ ☷ Queue · N ]
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

    # Play/Pause button toggle
    if state.is_paused:
        play_pause_button = {
            "text": "▶ Resume",
            "style": "success",
            "callback_data": f"player:resume:{session}",
        }
    else:
        play_pause_button = {
            "text": "⏸ Pause",
            "style": "primary",
            "callback_data": f"player:pause:{session}",
        }

    # First row controls: Replay | Pause/Resume | Skip
    row_1_buttons = [
        {
            "text": "↩ Replay",
            "style": "primary",
            "callback_data": f"player:replay:{session}",
        },
        play_pause_button,
        {
            "text": "≫ Skip",
            "style": "primary",
            "callback_data": f"player:skip:{session}",
        },
    ]

    # Second row control: Queue count badge, Shuffle, and Close
    queue_label = format_queue_badge(len(queue))
    row_2_buttons = [
        {
            "text": queue_label,
            "style": "primary",
            "callback_data": f"player:queue:{session}",
        },
        {
            "text": "🔀 Shuffle",
            "style": "primary",
            "callback_data": f"player:shuffle:{session}",
        },
        {
            "text": "✖ Close",
            "style": "link",
            "callback_data": f"player:close:{session}",
        },
    ]

    song_details_text = (
        f"<b>{escape_html(track.title)}</b>\n"
        f"<i>{escape_html(track.artist)}</i>\n"
        f"AUDIO · {format_time(track.duration)}\n"
        f"REQUESTED BY @{escape_html(requester_label)}\n\n"
        f"<code>{progress_line}</code>"
    )

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": "Aaruu Music",
            "size": 1,
        },
        {
            "type": "paragraph",
            "text": f"Playback requested by <b>@{escape_html(requester_label)}</b>",
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
            "text": song_details_text,
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
    ]

    return {
        "blocks": blocks,
        "type": "rich_message",
    }


def build_queue_rich_message(state: PlayerState, queue: TrackQueue) -> Dict[str, Any]:
    """
    Constructs the rich message for the chat queue:
    Aaruu Music
    Queue · N upcoming

    Now playing
    Song title
    Duration

    Up next:
    1. ...
    2. ...
    """
    session = state.session_id
    queued_tracks = queue.to_list()
    count = len(queued_tracks)

    now_playing_text = "None"
    if state.current_track and state.is_playing:
        now_playing_text = (
            f"<b>{escape_html(state.current_track.title)}</b>\n"
            f"<i>{escape_html(state.current_track.artist)}</i> · {format_time(state.current_track.duration)}"
        )

    up_next_lines: List[str] = []
    if queued_tracks:
        for idx, tr in enumerate(queued_tracks[:10], start=1):
            up_next_lines.append(
                f"{idx}. <b>{escape_html(tr.title)}</b> ({format_time(tr.duration)}) - @{escape_html(tr.requester_name)}"
            )
        if len(queued_tracks) > 10:
            up_next_lines.append(f"... and {len(queued_tracks) - 10} more songs.")
    else:
        up_next_lines.append("<i>No upcoming tracks in queue. Use /play to add songs!</i>")

    queue_body = (
        f"<b>Now Playing:</b>\n{now_playing_text}\n\n"
        f"<b>Up Next ({count} upcoming):</b>\n" + "\n".join(up_next_lines)
    )

    action_buttons = [
        {
            "text": "🔄 Refresh",
            "style": "primary",
            "callback_data": f"player:queue:{session}",
        },
        {
            "text": "↩ Undo Last",
            "style": "primary",
            "callback_data": f"queue:undo:{session}",
        },
        {
            "text": "✖ Close",
            "style": "link",
            "callback_data": f"player:close:{session}",
        },
    ]

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": "Aaruu Music",
            "size": 1,
        },
        {
            "type": "paragraph",
            "text": f"Queue · <b>{count} upcoming</b>",
        },
        {
            "type": "paragraph",
            "text": queue_body,
        },
        {
            "type": "buttons",
            "buttons": action_buttons,
            "align": "center",
        },
    ]

    return {
        "blocks": blocks,
        "type": "rich_message",
    }
