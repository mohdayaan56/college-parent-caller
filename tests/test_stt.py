import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import stt


class UnifiedSpeechTests(unittest.TestCase):
    def test_wrong_indic_script_is_rejected_without_blocking_code_mixing(self):
        self.assertEqual(stt._reject_wrong_indic_script("वूठ्त्तु अंता यस्टू", "kn"), "")
        self.assertEqual(stt._reject_wrong_indic_script("ಕನ್ನಡ ಪ್ರಶ್ನೆ", "hi"), "")
        self.assertEqual(stt._reject_wrong_indic_script("ಒಟ್ಟು marks", "kn"), "ಒಟ್ಟು marks")
        self.assertEqual(stt._reject_wrong_indic_script("Mohammed Khan", "hi"), "Mohammed Khan")

    def test_one_checkpoint_is_used_for_all_three_languages(self):
        class FakeWhisper:
            def transcribe(self, _path, **kwargs):
                return [SimpleNamespace(text=kwargs["language"])], None

        with patch.object(stt.settings, "stt_engine", "whisper_unified"), \
             patch.object(stt.settings, "whisper_model", "one-multilingual-checkpoint"), \
             patch.object(stt, "_load_whisper", return_value=FakeWhisper()) as load:
            self.assertEqual(
                [stt.transcribe_file(Path("audio.wav"), language) for language in ("en", "hi", "kn")],
                ["en", "hi", "kn"],
            )
            self.assertEqual([call.args[0] for call in load.call_args_list],
                             ["one-multilingual-checkpoint"] * 3)


if __name__ == "__main__":
    unittest.main()
