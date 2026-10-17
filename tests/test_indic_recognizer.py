import unittest
import numpy as np
from unittest.mock import MagicMock
from scripts.indic_recognizer import decode_ctc, mel_features, IndicHybridRecognizer


class IndicRecognizerTests(unittest.TestCase):
    def test_stretched_menu_vowels_preserve_ambiguity(self):
        from app.language_choice import language_choices
        self.assertEqual(language_choices('eenglish'), {'en'})
        self.assertEqual(language_choices('Hiindii'), {'hi'})
        self.assertEqual(language_choices('eenglish or Kannada'), {'en','kn'})
        self.assertEqual(language_choices('something else'), set())

    def test_language_menu_forwards_latin_restriction_to_meta_only(self):
        recognizer = object.__new__(IndicHybridRecognizer)
        recognizer.english = MagicMock(cuda_verified=True)
        recognizer.english.transcribe_waveform.return_value = ('hindi',{})
        recognizer.transcribe_waveform(np.zeros(8000),8000,language='en',constrain_script=True)
        self.assertEqual(recognizer.english.transcribe_waveform.call_args.kwargs,
                         {'language':'en','constrain_script':True})
    def test_blank_separates_repeated_tokens(self):
        vocab = ['▁ಹೌದು','▁ಇಲ್ಲ']
        ids = [2,0,0,2,0,1,1,2]
        logits = np.zeros((1,len(ids),3),dtype=np.float32)
        logits[0,np.arange(len(ids)),ids] = 1
        self.assertEqual(decode_ctc(logits,vocab),'ಹೌದು ಹೌದು ಇಲ್ಲ')

    def test_feature_shape_and_per_bin_normalization(self):
        audio = np.random.default_rng(7).normal(0,.1,16000).astype(np.float32)
        mel = mel_features(audio)
        self.assertEqual(mel.shape,(1,80,101))
        self.assertTrue(np.isfinite(mel).all())
        self.assertTrue(np.allclose(mel.mean(axis=-1),0,atol=1e-5))
        self.assertTrue(np.allclose(mel.std(axis=-1,ddof=1),1,atol=1e-4))
