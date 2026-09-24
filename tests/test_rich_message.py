"""
Unit tests for Telegram Bot API native Rich Message payload generation.
Tests typography, button structures, owner privacy filtering, and support link.
"""

import unittest
from bot.rich_help import build_start_rich_message
from bot.rich_player import build_player_rich_message, build_queue_rich_message
from player.models import PlayerState, Track
from player.queue import TrackQueue
from utils.typography import to_bold_sans, to_small_caps


class TestRichMessages(unittest.TestCase):
    def setUp(self):
        self.state = PlayerState(chat_id=98765)
        self.track = Track(
            track_id="tr_100",
            title="Aaruu Symphony",
            artist="Divine Melody",
            duration=186,
            thumbnail="https://example.com/cover.jpg",
            source_url="https://youtube.com/watch?v=symphony",
            requester_user_id=12345,
            requester_name="john_doe",
        )
        self.queue = TrackQueue(max_size=10)

    def test_build_player_rich_message_structure(self):
        self.state.play(self.track, {"name": "john_doe", "id": 12345})
        rich_msg = build_player_rich_message(self.state, self.queue)

        self.assertIn("blocks", rich_msg)
        blocks = rich_msg["blocks"]
        self.assertGreaterEqual(len(blocks), 5)

        # 1. Heading block
        heading_block = next((b for b in blocks if b.get("type") == "heading"), None)
        self.assertIsNotNone(heading_block)
        self.assertEqual(heading_block.get("text"), to_bold_sans("NOW PLAYING"))

        # 2. Photo block for album art
        photo_block = next((b for b in blocks if b.get("type") == "photo"), None)
        self.assertIsNotNone(photo_block)
        self.assertEqual(photo_block["photo"]["media"], "https://example.com/cover.jpg")

        # 3. Paragraph block containing details and progress
        paragraphs = [b for b in blocks if b.get("type") == "paragraph"]
        details_paragraph = next((p for p in paragraphs if "Aaruu Symphony" in p.get("text", "")), None)
        self.assertIsNotNone(details_paragraph)
        self.assertIn("Divine Melody", details_paragraph["text"])
        self.assertIn("3:06", details_paragraph["text"])
        self.assertIn("●", details_paragraph["text"])

        # 4. Buttons blocks: Row 1 [ Queue ] [ || ] [ ↻ ] and Row 2 [ » ]
        button_blocks = [b for b in blocks if b.get("type") == "buttons"]
        self.assertEqual(len(button_blocks), 2)

        # Row 1 buttons: Queue, ||, ↻
        row1 = button_blocks[0]["buttons"]
        self.assertEqual(len(row1), 3)
        self.assertEqual(row1[0]["text"], "Queue")
        self.assertEqual(row1[1]["text"], "||")
        self.assertEqual(row1[2]["text"], "↻")

        # Row 2 buttons: »
        row2 = button_blocks[1]["buttons"]
        self.assertEqual(len(row2), 1)
        self.assertEqual(row2[0]["text"], "»")

    def test_build_player_paused_toggle(self):
        self.state.play(self.track, {"name": "john_doe", "id": 12345})
        self.state.pause()
        rich_msg = build_player_rich_message(self.state, self.queue)
        button_blocks = [b for b in rich_msg["blocks"] if b.get("type") == "buttons"]
        row1 = button_blocks[0]["buttons"]

        # Middle button should now be Resume (>) with success style
        self.assertEqual(row1[1]["text"], ">")
        self.assertEqual(row1[1]["style"], "success")
        self.assertIn("player:resume:", row1[1]["callback_data"])

    def test_build_queue_rich_message(self):
        self.state.play(self.track, {"name": "john_doe", "id": 12345})
        next_track = Track(
            track_id="next_1",
            title="Next Up Track",
            artist="Another Artist",
            duration=200,
            thumbnail="https://example.com/next.jpg",
            source_url="https://youtube.com/watch?v=next",
            requester_user_id=6789,
            requester_name="jane",
        )
        self.queue.add(next_track)

        queue_msg = build_queue_rich_message(self.state, self.queue)
        self.assertIn("blocks", queue_msg)
        blocks = queue_msg["blocks"]

        paragraphs = [b for b in blocks if b.get("type") == "paragraph"]
        combined_text = " ".join([p.get("text", "") for p in paragraphs])
        self.assertIn("1", combined_text)
        self.assertIn("Aaruu Symphony", combined_text)
        self.assertIn("Next Up Track", combined_text)

    def test_build_start_rich_message_all_guides(self):
        # Non-owner view: owner button is hidden
        start_msg = build_start_rich_message("home", is_owner=False)
        self.assertIn("blocks", start_msg)
        button_blocks = [b for b in start_msg["blocks"] if b.get("type") == "buttons"]
        all_buttons = []
        for bb in button_blocks:
            all_buttons.extend(bb["buttons"])

        # Owner button MUST NOT be in public buttons
        self.assertFalse(any(b.get("callback_data") == "help:owner_sudo" for b in all_buttons))

        # Support button MUST be present and redirect to https://t.me/wzzkaanu
        support_btn = next((b for b in all_buttons if "url" in b and b["url"] == "https://t.me/wzzkaanu"), None)
        self.assertIsNotNone(support_btn)

        # Owner view: owner button is visible
        owner_start_msg = build_start_rich_message("home", is_owner=True)
        owner_blocks = [b for b in owner_start_msg["blocks"] if b.get("type") == "buttons"]
        owner_buttons = []
        for bb in owner_blocks:
            owner_buttons.extend(bb["buttons"])
        self.assertTrue(any(b.get("callback_data") == "help:owner_sudo" for b in owner_buttons))


if __name__ == "__main__":
    unittest.main()
