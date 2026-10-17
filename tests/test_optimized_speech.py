import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
import numpy as np

from app import llm
from app.config import settings
from scripts.meta_onnx import OnnxRecognizer
from tests.support import scratch_dir


class OptimizedSpeechTests(unittest.TestCase):
    def test_wrong_language_english_draft_is_rejected_with_identical_numbers(self):
        facts = 'Ahmed scored 85 out of 100.'
        self.assertIsNone(llm.validate_grounded_reply('Ahmed को 85 में से 100 अंक मिले।', facts, 'en', 'Ahmed'))
        self.assertIsNotNone(llm.validate_grounded_reply(facts, facts, 'en', 'Ahmed'))

    def test_speech_edges_keep_padding_internal_pauses_and_uncertain_audio(self):
        from scripts.speech_edges import speech_bounds
        audio = np.zeros(16000*5)
        self.assertEqual(speech_bounds(audio,16000,[]), (0,len(audio)))
        self.assertEqual(speech_bounds(audio,16000,[{'start':16000,'end':32000},
                                                  {'start':48000,'end':64000}]),(9600,70400))
        self.assertEqual(speech_bounds(audio,16000,[{'start':500,'end':79500}]),(0,len(audio)))

    def test_optional_script_mask_keeps_blank_and_latin_but_rejects_other_scripts(self):
        recognizer = object.__new__(OnnxRecognizer)
        recognizer.script_masks = {}
        pieces = ['<pad>', '▁marks', 'ಅಂಕ', 'अंक', 'தமிழ்', '▁85']
        recognizer.tokenizer = SimpleNamespace(get_piece_size=lambda: len(pieces), id_to_piece=lambda i: pieces[i])
        self.assertEqual(recognizer._script_mask('kn').tolist(), [True, True, True, False, False, True])
        self.assertEqual(recognizer._script_mask('hi').tolist(), [True, True, False, True, False, True])
        with self.assertRaises(ValueError):
            recognizer._script_mask('auto')

    def test_ctc_blank_separates_identical_tokens_and_audio_normalizes(self):
        recognizer = object.__new__(OnnxRecognizer)
        recognizer.session = MagicMock()
        logits = np.zeros((1, 7, 4), dtype=np.float32)
        ids = [0, 2, 2, 0, 2, 3, 3]
        logits[0, np.arange(7), ids] = 1
        recognizer.session.run.return_value = [logits]
        recognizer.tokenizer = SimpleNamespace(decode=lambda tokens: str(tokens))
        text, _ = recognizer.transcribe_waveform(np.arange(800, dtype=np.float32), 16000)
        self.assertEqual(text, '[2, 2, 3]')
        sent = recognizer.session.run.call_args.args[1]['audio']
        self.assertEqual(sent.shape, (1, 800))
        self.assertAlmostEqual(float(sent.mean()), 0, places=5)
        self.assertAlmostEqual(float(sent.var()), 1, places=5)

    def test_invalid_waveforms_never_reach_model(self):
        recognizer = object.__new__(OnnxRecognizer)
        recognizer.session = MagicMock()
        for data in [np.array([]), np.array([float('nan')]), np.zeros((2, 3, 4))]:
            with self.assertRaises(ValueError):
                recognizer.transcribe_waveform(data, 16000)
        recognizer.session.run.assert_not_called()

    def test_validated_draft_reused_but_changed_marks_force_regeneration(self):
        with scratch_dir() as root, patch.object(settings, 'recordings_dir', root), \
             patch.object(settings, 'ollama_enabled', True), \
             patch.object(llm, '_chat_text', side_effect=['Ahmed scored 342 out of 400.', 'Ahmed scored 343 out of 400.']) as chat:
            facts = 'Ahmed scored 342 out of 400.'
            self.assertEqual(llm.generate_grounded_reply(facts, 'Total?', 'en', 'Ahmed'), facts)
            self.assertEqual(llm.generate_grounded_reply(facts, 'Aggregate?', 'en', 'Ahmed'), facts)
            self.assertEqual(chat.call_count, 1)
            changed = 'Ahmed scored 343 out of 400.'
            self.assertEqual(llm.generate_grounded_reply(changed, 'Total?', 'en', 'Ahmed'), changed)
            self.assertEqual(chat.call_count, 2)

    def test_readiness_cache_lookup_does_not_generate_or_accept_invalid_drafts(self):
        with scratch_dir() as root, patch.object(settings, 'recordings_dir', root), \
             patch.object(settings, 'ollama_enabled', True), patch.object(llm, '_chat_text') as chat:
            self.assertIsNone(llm.generate_grounded_reply('Ahmed scored 85.', '', 'en', 'Ahmed', cached_only=True))
            chat.assert_not_called()
            chat.return_value = 'Ahmed scored 99.'
            self.assertIsNone(llm.generate_grounded_reply('Ahmed scored 85.', '', 'en', 'Ahmed'))
            self.assertFalse(list(root.rglob('*.json')))

    def test_name_placeholder_is_restored_without_altering_kannada_or_marks(self):
        with scratch_dir() as root, patch.object(settings, 'recordings_dir', root), \
             patch.object(settings, 'ollama_enabled', True), \
             patch.object(llm, '_chat_text', return_value='[STUDENT] ಅವರ ಒಟ್ಟು ಅಂಕಗಳು 400 ರಲ್ಲಿ 342.') as chat:
            facts = 'Ahmed Khan ಅವರ ಒಟ್ಟು ಅಂಕಗಳು 400 ರಲ್ಲಿ 342.'
            self.assertEqual(llm.generate_grounded_reply(facts, 'ಒಟ್ಟು?', 'kn', 'Ahmed Khan'), facts)
            self.assertIn('[STUDENT]', chat.call_args.args[0])
            self.assertNotIn('Ahmed Khan', chat.call_args.args[0])

    def test_gemma_bare_marker_keeps_name_and_factual_guard(self):
        with scratch_dir() as root, patch.object(settings, 'recordings_dir', root), \
             patch.object(settings, 'ollama_enabled', True), \
             patch.object(llm, '_chat_text', side_effect=[
                 'STUDENT ಅವರ ಒಟ್ಟು ಅಂಕಗಳು 400 ರಲ್ಲಿ 342.',
                 'STUDENT scored 400 out of 400.',
                 'Another Child scored 342 out of 400.',
             ]):
            facts = 'Ahmed Khan ಅವರ ಒಟ್ಟು ಅಂಕಗಳು 400 ರಲ್ಲಿ 342.'
            self.assertEqual(llm.generate_grounded_reply(facts, 'ಒಟ್ಟು?', 'kn', 'Ahmed Khan'), facts)
            self.assertIsNone(llm.generate_grounded_reply(
                'Ahmed Khan scored 342 out of 400.', 'Total?', 'en', 'Ahmed Khan'))
            self.assertIsNone(llm.generate_grounded_reply(
                'Ahmed Khan scored 342 out of 400.', 'Total?', 'en', 'Ahmed Khan'))
