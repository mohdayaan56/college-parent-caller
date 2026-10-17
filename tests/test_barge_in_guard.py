import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.barge_in import SpeechInterruptGuard, spoken_words


class SpeechGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_breath_and_clicks_do_not_request_or_interrupt(self):
        decode = AsyncMock(return_value={'text': ''})
        gate = SpeechInterruptGuard(decode)
        try:
            for _ in range(500):
                self.assertFalse(gate.feed(b'\x01\0' * 160,
                                          SimpleNamespace(interrupt_run=0), False))
            self.assertLessEqual(len(gate.audio), 6400)
            decode.assert_not_called()
            self.assertFalse(gate.feed(b'\x01\0' * 160,
                                      SimpleNamespace(interrupt_run=1280), False))
            decode.assert_not_called()
        finally:
            await gate.close()

    async def test_noise_misclassified_by_vad_still_needs_recognized_words(self):
        for text in ('', '.', '[breathing]', '(noise)'):
            gate = SpeechInterruptGuard(AsyncMock(return_value={'text': text}))
            try:
                self.assertFalse(gate.feed(b'\x01\0' * 2560,
                                          SimpleNamespace(interrupt_run=2560), False))
                await asyncio.sleep(0)
                self.assertFalse(gate.feed(bytes(320), SimpleNamespace(interrupt_run=0), True))
                self.assertFalse(gate.candidate)
                self.assertEqual(gate.rejected, 1)
            finally:
                await gate.close()

    async def test_real_words_interrupt_and_preserve_onset_in_all_languages(self):
        for text in ('What is the total?', 'कुल अंक क्या हैं?', 'ಒಟ್ಟು ಅಂಕಗಳು ಎಷ್ಟು?', '123'):
            decode = AsyncMock(return_value={'text': text})
            gate = SpeechInterruptGuard(decode)
            try:
                onset = b'\x01\0' * 1280
                gate.feed(onset, SimpleNamespace(interrupt_run=1280), False)
                gate.feed(b'\x02\0' * 1280, SimpleNamespace(interrupt_run=2560), False)
                await asyncio.sleep(0)
                self.assertTrue(gate.feed(bytes(320), SimpleNamespace(interrupt_run=0), True))
                self.assertTrue(bytes(gate.audio).startswith(onset))
                self.assertEqual(gate.total_probes, 1)
            finally:
                await gate.close()

    async def test_failed_probe_leaves_playback_running(self):
        gate = SpeechInterruptGuard(AsyncMock(side_effect=RuntimeError('ASR busy')))
        try:
            gate.feed(bytes(5120), SimpleNamespace(interrupt_run=2560), False)
            await asyncio.sleep(0)
            self.assertFalse(gate.feed(bytes(320), SimpleNamespace(interrupt_run=0), True))
            self.assertEqual(gate.error, 'RuntimeError')
        finally:
            await gate.close()

    def test_non_speech_metadata_and_punctuation_are_rejected(self):
        for text in ('', '.', '[noise]', '(breathing)', '<silence>'):
            self.assertFalse(spoken_words(text))
