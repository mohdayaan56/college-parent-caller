import asyncio
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from app import ari, audio_cache
from app.config import settings
from tests.support import scratch_dir
from tests.test_audio_cache import speech


class StreamingAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_chunk_plays_before_rest_has_finished_generating(self):
        second_started = threading.Event()
        allow_second = threading.Event()
        events = []
        def generate(text, language, timing):
            if text == "Second sentence.":
                second_started.set()
                if not allow_second.wait(2):
                    raise AssertionError("First audio was not played while second chunk was generating")
            events.append("generated:" + text)
            timing.update(tts_cache_hit=False, tts_seconds=0.01, audio_conversion_seconds=0)
            return Path("first.wav" if text.startswith("First") else "second.wav")
        async def play(channel, path, timing=None):
            if path.name == "first.wav":
                self.assertTrue(await asyncio.to_thread(second_started.wait, 2))
                self.assertNotIn("generated:Second sentence.", events)
                events.append("played:first")
                allow_second.set()
            else:
                events.append("played:second")
        with scratch_dir() as root, patch.object(settings, "recordings_dir", root), \
             patch.object(audio_cache, "valid_audio", return_value=False), \
             patch.object(audio_cache, "cached_speech", generate), \
             patch.object(audio_cache, "_assemble_chunks"), patch.object(ari, "_play", play):
            await asyncio.wait_for(ari._play_reply("stream-test", "First sentence. Second sentence.", "en",
                                                 {"recording_finished_at": ari.monotonic()}), 4)
        self.assertLess(events.index("played:first"), events.index("generated:Second sentence."))
        self.assertEqual(events[-1], "played:second")

    async def test_prepared_reply_is_one_continuous_audio_file(self):
        with scratch_dir() as root, patch.object(settings, "recordings_dir", root), \
             patch.object(audio_cache, "synthesize", side_effect=speech) as generate:
            prepared = audio_cache.cached_speech("First sentence. Second sentence.", "en")
            chunks = [chunk async for chunk in audio_cache.reply_audio("First sentence. Second sentence.", "en")]
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0][0], prepared)
            self.assertTrue(chunks[0][1]["tts_cache_hit"])
            self.assertFalse(chunks[0][1]["tts_streaming"])
            self.assertEqual(generate.call_count, 1)

    async def test_interrupted_playback_does_not_schedule_all_remaining_chunks(self):
        produced = []
        def generate(text, language, timing):
            produced.append(text)
            return Path("chunk.wav")
        async def play(channel, path, timing=None):
            raise RuntimeError("channel ended")
        with scratch_dir() as root, patch.object(settings, "recordings_dir", root), \
             patch.object(audio_cache, "valid_audio", return_value=False), \
             patch.object(audio_cache, "cached_speech", generate), patch.object(ari, "_play", play):
            with self.assertRaises(RuntimeError):
                await ari._play_reply("interrupted-stream", "One. Two. Three. Four.", "en",
                                      {"recording_finished_at": ari.monotonic()})
            await asyncio.sleep(0)
        self.assertNotIn("Three.", produced)
        self.assertNotIn("Four.", produced)


class ChunkTextTests(unittest.TestCase):
    def test_chunks_preserve_digits_and_all_three_scripts(self):
        for text in ("Ahmed scored 85.5 percent. Total is 342 out of 400.",
                     "अहमद के अंक 85.5 प्रतिशत हैं। कुल अंक 342 हैं।",
                     "ಅಹ್ಮದ್ ಅವರ ಅಂಕಗಳು 85.5 ಶೇಕಡಾ. ಒಟ್ಟು 342 ಅಂಕಗಳು."):
            chunks = audio_cache.speech_chunks(text, max_chars=35)
            self.assertEqual(" ".join(chunks), text)
            self.assertIn("85.5", " ".join(chunks))


if __name__ == "__main__":
    unittest.main()
