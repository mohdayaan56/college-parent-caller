import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
import numpy as np
from scripts.whisper_recognizer import WhisperRecognizer


class WhisperRecognizerTests(unittest.TestCase):
    def test_explicit_language_transcribes_and_resamples_phone_audio(self):
        for language, text in (('hi','नहीं'),('kn','ಇಲ್ಲ'),('en','no')):
            recognizer = object.__new__(WhisperRecognizer)
            recognizer.device = 'cuda'
            recognizer.model = MagicMock()
            recognizer.model.transcribe.return_value = (iter([SimpleNamespace(text=text)]), None)
            result, metrics = recognizer.transcribe_waveform(np.zeros(8000,dtype=np.float32),8000,language=language)
            self.assertEqual(result,text)
            self.assertEqual(metrics['explicit_language'],language)
            self.assertEqual(recognizer.model.transcribe.call_args.args[0].size,16000)
            options = recognizer.model.transcribe.call_args.kwargs
            self.assertEqual(options['language'],language)
            self.assertEqual(options['task'],'transcribe')
            self.assertFalse(options['condition_on_previous_text'])

    def test_invalid_audio_is_not_transcribed(self):
        recognizer = object.__new__(WhisperRecognizer)
        recognizer.model = MagicMock()
        for waveform in (np.array([]),np.array([float('nan')]),np.zeros((2,2,2))):
            with self.assertRaises(ValueError):
                recognizer.transcribe_waveform(waveform,16000,language='kn')
        recognizer.model.transcribe.assert_not_called()
