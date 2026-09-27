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

    def test_is_youtube_watch_url_and_is_direct_media_url(self):
        """Verify URL classification helpers."""
        from player.models import is_youtube_watch_url, is_direct_media_url

        self.assertTrue(is_youtube_watch_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertTrue(is_youtube_watch_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertTrue(is_youtube_watch_url("https://www.youtube.com/shorts/abc12345"))
        self.assertFalse(is_youtube_watch_url("https://rr1---sn-abc.googlevideo.com/videoplayback?id=123"))
        self.assertFalse(is_youtube_watch_url("https://aac.saavncdn.com/123/sample.mp3"))
        self.assertFalse(is_youtube_watch_url(None))

        self.assertTrue(is_direct_media_url("https://rr1---sn-abc.googlevideo.com/videoplayback?id=123"))
        self.assertTrue(is_direct_media_url("https://aac.saavncdn.com/123/sample.mp3"))
        self.assertTrue(is_direct_media_url("https://cf-media.sndcdn.com/stream.128.mp3"))
        self.assertFalse(is_direct_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ"))
        self.assertFalse(is_direct_media_url("https://youtu.be/dQw4w9WgXcQ"))
        self.assertFalse(is_direct_media_url(None))

    def test_stream_url_priority_in_download_track(self):
        """Verify that download_track downloads direct stream_url and does not fall back to watch URL."""
        async def run():
            direct_stream = "https://rr1---sn-abc.googlevideo.com/videoplayback?expire=12345&itag=140"
            tr = Track(
                track_id="direct_yt_test",
                title="Direct Stream Song",
                artist="Artist",
                duration=200,
                thumbnail="https://example.com/thumb.jpg",
                source_url="https://www.youtube.com/watch?v=direct_yt_test",
                stream_url=direct_stream,
                requester_user_id=1,
                requester_name="User",
            )

            # Mock _download_direct_url to create the file and return True
            def fake_direct_dl(url, dest_path):
                self.assertEqual(url, direct_stream)
                with open(dest_path, "wb") as f:
                    f.write(b"ID3" + b"\x00" * 100)
                return True

            with patch.object(self.extractor, "_download_direct_url", side_effect=fake_direct_dl) as mock_direct, \
                 patch.object(self.extractor, "_download_ytdlp") as mock_ytdlp:

                ok = await self.extractor.download_track(tr)
                self.assertTrue(ok)
                self.assertTrue(mock_direct.called)
                self.assertFalse(mock_ytdlp.called)
                self.assertIsNotNone(tr.local_filepath)
                self.assertTrue(os.path.exists(tr.local_filepath))

                # Clean up
                if os.path.exists(tr.local_filepath):
                    os.remove(tr.local_filepath)

        asyncio.run(run())

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
                     patch.object(self.extractor, "prepare_track", new=AsyncMock(side_effect=lambda t, is_video=False: setattr(t, "local_filepath", dummy_mp3) or dummy_mp3)), \
                     patch("player.extractor.verify_media_file_with_ffmpeg", new=AsyncMock(return_value=(True, "OK"))):

                    extracted = await self.extractor.extract("barsaat", 10, "Tester")
                    self.assertIsNotNone(extracted)
                    self.assertEqual(extracted.title, "Barsaat - Banjaare")
                    
                    local_path = await self.extractor.prepare_track(extracted)
                    self.assertEqual(local_path, dummy_mp3)
                    self.assertEqual(extracted.playable_source, dummy_mp3)
                    self.assertEqual(classify_media_source(extracted.playable_source), "LOCAL_FILE")

            finally:
                if os.path.exists(dummy_mp3):
                    os.remove(dummy_mp3)

        asyncio.run(run())

    def test_youtube_download_video_id_42r8Stt_30w(self):
        """Verify prepare_track for specific video_id '42r8Stt-30w' with cache check and fallback."""
        async def run():
            tr = Track(
                track_id="yt_42r8Stt-30w",
                title="Banjaare | Barsaat x Spider-Man",
                artist="Artist",
                duration=220,
                thumbnail="https://i.ytimg.com/vi/42r8Stt-30w/hqdefault.jpg",
                source_url="https://www.youtube.com/watch?v=42r8Stt-30w",
                requester_user_id=10,
                requester_name="Tester",
            )

            test_file = "cache/audio/42r8Stt-30w.webm"
            os.makedirs("cache/audio", exist_ok=True)
            try:
                # 1. Create fake downloaded cached file
                with open(test_file, "wb") as f:
                    f.write(b"\x1a\x45\xdf\xa3" + b"\x00" * 500)

                # First call: should hit cache immediately without downloading
                with patch.object(self.extractor, "_download_ytdlp") as mock_dl:
                    res = await self.extractor.prepare_track(tr)
                    self.assertEqual(res, test_file)
                    self.assertEqual(tr.local_filepath, test_file)
                    self.assertFalse(mock_dl.called)

            finally:
                if os.path.exists(test_file):
                    os.remove(test_file)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
