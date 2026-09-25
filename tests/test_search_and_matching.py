"""
Unit tests verifying search, candidate scoring, audio matching, source classification,
and preventing raw YouTube watch URLs from reaching FFmpeg.
"""

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from player.models import Track, classify_media_source
from player.extractor import MediaExtractor, validate_and_score_track
from player.voice_chat import verify_media_file_with_ffmpeg


class TestSearchAndMatching(unittest.TestCase):
    def setUp(self):
        self.extractor = MediaExtractor()

    def test_source_classification(self):
        """Verify strict source classification separating watch URLs from media."""
        # 1. YouTube watch and shorts URLs must be classified as YOUTUBE_WATCH_URL
        self.assertEqual(
            classify_media_source("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "YOUTUBE_WATCH_URL",
        )
        self.assertEqual(
            classify_media_source("https://youtu.be/dQw4w9WgXcQ"),
            "YOUTUBE_WATCH_URL",
        )
        self.assertEqual(
            classify_media_source("https://www.youtube.com/shorts/abc12345"),
            "YOUTUBE_WATCH_URL",
        )

        # 2. Direct HTTP media URLs
        self.assertEqual(
            classify_media_source("https://aac.saavncdn.com/123/sample.mp3"),
            "DIRECT_HTTP_MEDIA",
        )
        self.assertEqual(
            classify_media_source("https://cf-media.sndcdn.com/stream.128.mp3"),
            "DIRECT_HTTP_MEDIA",
        )

        # 3. Invalid / unknown sources
        self.assertEqual(classify_media_source(None), "UNKNOWN")
        self.assertEqual(classify_media_source(""), "UNKNOWN")
        self.assertEqual(classify_media_source("barsaat"), "UNKNOWN")

    def test_track_playable_source_never_returns_watch_url(self):
        """Verify Track.playable_source never returns a raw YouTube watch URL."""
        tr = Track(
            track_id="yt_12345",
            title="Barsaat Banjaare",
            artist="Banjaare",
            duration=210,
            thumbnail="https://i.ytimg.com/vi/12345/hqdefault.jpg",
            source_url="https://www.youtube.com/watch?v=12345",
            stream_url="https://www.youtube.com/watch?v=12345",  # Invalid stream_url containing watch page
            requester_user_id=1,
            requester_name="User",
        )
        self.assertIsNone(tr.playable_source)

        # When downloaded locally, playable_source must return the local file
        test_file = "/tmp/test_barsaat_sample.mp3"
        with open(test_file, "wb") as f:
            f.write(b"ID3" + b"\x00" * 100)
        try:
            tr.local_filepath = test_file
            self.assertEqual(tr.playable_source, test_file)
            self.assertEqual(classify_media_source(tr.playable_source), "LOCAL_FILE")
        finally:
            if os.path.exists(test_file):
                os.remove(test_file)

    def test_ffmpeg_validator_rejects_watch_url(self):
        """Verify FFmpeg validator strictly rejects YouTube watch URLs."""
        async def run():
            ok, msg = await verify_media_file_with_ffmpeg("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            self.assertFalse(ok)
            self.assertIn("YOUTUBE_WATCH_URL", msg)
            self.assertIn("Rejected source", msg)
        asyncio.run(run())

    def test_validate_and_score_track_queries(self):
        """Test song matching scoring for Hindi and regional queries."""
        # 1. /play barsaat -> matching candidate
        cand_barsaat = Track(
            track_id="b1",
            title="Barsaat Banjaare (Official Song)",
            artist="Banjaare Music",
            duration=215,
            thumbnail="",
            source_url="https://www.youtube.com/watch?v=b1",
            requester_user_id=1,
            requester_name="User",
        )
        valid, score, _ = validate_and_score_track("barsaat", cand_barsaat)
        self.assertTrue(valid)
        self.assertGreaterEqual(score, 0.5)

        # 2. /play ASSAY PASSAY -> Haryanvi track
        cand_assay = Track(
            track_id="a1",
            title="Assay Passay - Masoom Sharma",
            artist="Masoom Sharma",
            duration=195,
            thumbnail="",
            source_url="https://www.youtube.com/watch?v=a1",
            requester_user_id=1,
            requester_name="User",
        )
        valid_a, score_a, _ = validate_and_score_track("ASSAY PASSAY", cand_assay)
        self.assertTrue(valid_a)
        self.assertGreaterEqual(score_a, 0.5)

        # 3. Reject YouTube Shorts and status clips
        cand_short = Track(
            track_id="s1",
            title="Barsaat Banjaare #shorts status",
            artist="Shorts Channel",
            duration=25,
            thumbnail="",
            source_url="https://www.youtube.com/shorts/s1",
            requester_user_id=1,
            requester_name="User",
        )
        valid_s, _, reason_s = validate_and_score_track("barsaat", cand_short)
        self.assertFalse(valid_s)
        self.assertIn("Too short", reason_s)

    def test_extract_pipeline_mocked_end_to_end(self):
        """Test complete extraction pipeline: query -> candidates -> download -> FFmpeg -> return track."""
        async def run():
            dummy_mp3 = "/tmp/dummy_barsaat.mp3"
            with open(dummy_mp3, "wb") as f:
                f.write(b"ID3" + b"\x00" * 200)

            try:
                cand = Track(
                    track_id="barsaat_1",
                    title="Barsaat - Banjaare",
                    artist="Banjaare",
                    duration=200,
                    thumbnail="https://i.ytimg.com/vi/barsaat_1/hqdefault.jpg",
                    source_url="https://www.youtube.com/watch?v=barsaat_1",
                    requester_user_id=10,
                    requester_name="Tester",
                )

                with patch.object(self.extractor, "_search_youtube_ytinitialdata", return_value=[cand]), \
                     patch.object(self.extractor, "download_track", new=AsyncMock(side_effect=lambda t: setattr(t, "local_filepath", dummy_mp3) or True)), \
                     patch("player.extractor.verify_media_file_with_ffmpeg", new=AsyncMock(return_value=(True, "OK"))):

                    extracted = await self.extractor.extract("barsaat", 10, "Tester")
                    self.assertIsNotNone(extracted)
                    self.assertEqual(extracted.title, "Barsaat - Banjaare")
                    self.assertEqual(extracted.playable_source, dummy_mp3)
                    self.assertEqual(classify_media_source(extracted.playable_source), "LOCAL_FILE")

            finally:
                if os.path.exists(dummy_mp3):
                    os.remove(dummy_mp3)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
