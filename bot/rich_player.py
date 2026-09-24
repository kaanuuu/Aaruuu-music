"""
Aaruu Music - Native Rich Player & Queue Builder
Constructs sleek, high-fidelity media player layouts using aesthetic Unicode typography
(sans-serif bold, small caps) without raw HTML tags to avoid punchmarks.
"""

from typing import Any, Dict, List
from player.models import PlayerState, Track
from player.queue import TrackQueue
from utils.escaping import escape_html
from utils.formatting import format_time, get_user_mention, render_progress
from utils.typography import to_bold_sans, to_small_caps

SUPPORT_URL = "https://t.me/wzzkaanu"


def build_player_rich_ui(state: PlayerState, queue: TrackQueue) -> Dict[str, Any]:
    """
    Centralized native Rich UI builder for Aaruu Music.
    Constructs the rich player message using Telegram Rich UI Button Blocks:
    - Pure Unicode typography
    - Full track details: title, artist, duration, user mention
    - Visual progress bar
    - Minimal symbol controls: [ Queue ] [ || / > ] [ ↻ ] and [ » ]
    """
    track: Track = state.current_track or Track(
        track_id="none",
        title="No Track Selected",
        artist="Music Player",
        duration=0,
        thumbnail="https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80",
        source_url="https://telegram.org",
        requester_user_id=0,
        requester_name="System",
    )

    session = state.session_id
    curr_pos = state.current_position
    progress_line = render_progress(curr_pos, track.duration, bar_length=12)
    
    req_id = state.requested_by.get("id") or getattr(track, "requester_user_id", 0)
    req_name = state.requested_by.get("name") or getattr(track, "requester_name", "User")
    req_user = state.requested_by.get("username") or getattr(track, "requester_username", None)
    # Ensure requester mention uses username or clickable mention, never raw Telegram user ID
    requester_label = get_user_mention(req_id, req_name, req_user)

    # Play/Pause toggle button (|| for playing, > for paused)
    if state.is_paused:
        play_pause_button = {
            "text": ">",
            "style": "success",
            "callback_data": f"player:resume:{session}",
        }
        status_badge = "⏸ " + to_small_caps("paused")
    else:
        play_pause_button = {
            "text": "||",
            "style": "primary",
            "callback_data": f"player:pause:{session}",
        }
        pb_status = getattr(state, "playback_status", "playing")
        if pb_status == "searching":
            status_badge = "🔎 " + to_small_caps("searching")
        elif pb_status == "preparing":
            status_badge = "⬇️ " + to_small_caps("preparing")
        elif pb_status == "starting":
            status_badge = "🎧 " + to_small_caps("starting")
        else:
            status_badge = "▶ " + to_small_caps("playing")

    # Row 1: [ Queue ] [ || / > ] [ ↻ ]
    row_1_buttons = [
        {
            "text": "Queue",
            "style": "primary",
            "callback_data": f"player:queue:{session}",
        },
        play_pause_button,
        {
            "text": "↻",
            "style": "primary",
            "callback_data": f"player:replay:{session}",
        },
    ]

    # Row 2: [ » ]
    row_2_buttons = [
        {
            "text": "»",
            "style": "primary",
            "callback_data": f"player:skip:{session}",
        },
    ]

    # Timeline with unicode progress bar
    time_str = progress_line
    is_video_track = getattr(track, "is_video", False) or getattr(track, "media_type", "audio") == "video"

    track_title = track.title or "Unknown Track"
    track_artist = track.artist or "Unknown Artist"

    if is_video_track:
        heading_title = to_bold_sans("NOW PLAYING VIDEO")
        caption_text = (
            f"🎬 {to_small_caps('video')}: {track_title}\n"
            f"👤 {to_small_caps('channel')}: {track_artist}\n"
            f"🙋 {to_small_caps('requested by')}: {requester_label}\n"
            f"⚡ {to_small_caps('status')}: {status_badge} (🎥 {to_small_caps('video stream')})\n\n"
            f"{time_str}"
        )
    else:
        heading_title = to_bold_sans("NOW PLAYING")
        caption_text = (
            f"🎵 {to_small_caps('title')}: {track_title}\n"
            f"👤 {to_small_caps('artist')}: {track_artist}\n"
            f"🙋 {to_small_caps('requested by')}: {requester_label}\n"
            f"⚡ {to_small_caps('status')}: {status_badge}\n\n"
            f"{time_str}"
        )

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": heading_title,
            "size": 1,
        },
    ]

    # Clean no-image player: skip photo block if thumbnail is not a valid URL
    if track.thumbnail and track.thumbnail.startswith("http"):
        blocks.append({
            "type": "photo",
            "photo": {
                "type": "photo",
                "media": track.thumbnail,
            },
        })

    blocks.extend([
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
    ])

    return {
        "blocks": blocks,
        "type": "rich_message",
    }


# Centralized alias ensuring unified Rich UI builder
build_player_rich_message = build_player_rich_ui


def build_queue_rich_message(state: PlayerState, queue: TrackQueue) -> Dict[str, Any]:
    """
    Constructs the rich message for the chat queue using Rich UI Button Blocks.
    Clean aesthetic typography, no raw HTML tags except timeline.
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
            req_tag = get_user_mention(
                getattr(tr, "requester_user_id", 0),
                getattr(tr, "requester_name", "User"),
                getattr(tr, "requester_username", None),
            )
            up_next_lines.append(
                f"{idx}. {tr.title} ({format_time(tr.duration)}) - {req_tag}"
            )
        if len(queued_tracks) > 8:
            up_next_lines.append(f"... +{len(queued_tracks) - 8} " + to_small_caps("more tracks"))
    else:
        up_next_lines.append(to_small_caps("no upcoming tracks in queue. use /play to add songs!"))

    queue_body = (
        f"{to_bold_sans('CURRENT PLAYLIST')}\n\n"
        f"▶ {to_small_caps('now playing')}:\n{now_playing_text}\n\n"
        f"📋 {to_small_caps('up next')} ({count} {to_small_caps('upcoming')}):\n"
        + "\n".join(up_next_lines)
    )

    thumbnail = (
        state.current_track.thumbnail
        if state.current_track and state.current_track.thumbnail
        else "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
    )

    action_buttons_row1 = [
        {
            "text": "Player",
            "style": "primary",
            "callback_data": f"player:nowplaying:{session}",
        },
        {
            "text": "↻",
            "style": "primary",
            "callback_data": f"player:queue:{session}",
        },
        {
            "text": "Undo",
            "style": "primary",
            "callback_data": f"queue:undo:{session}",
        },
    ]

    action_buttons_row2 = [
        {
            "text": "Support",
            "url": SUPPORT_URL,
        },
        {
            "text": "Close",
            "style": "link",
            "callback_data": f"player:close:{session}",
        },
    ]

    blocks: List[Dict[str, Any]] = [
        {
            "type": "heading",
            "text": to_bold_sans("MUSIC QUEUE"),
            "size": 1,
        },
    ]

    if thumbnail and thumbnail.startswith("http"):
        blocks.append({
            "type": "photo",
            "photo": {
                "type": "photo",
                "media": thumbnail,
            },
        })

    blocks.extend([
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
    ])

    return {
        "blocks": blocks,
        "type": "rich_message",
    }
