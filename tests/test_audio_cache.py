from dataclasses import replace
import unittest
from unittest.mock import patch
import wave

from app import audio_cache
from app.config import settings
from app.conversation import CallSession, CallState
from app.csv_store import load_students
from tests.support import CsvFixtureMixin, DEMO_PHONE, scratch_dir


def speech(_text, path, _language):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x01\x00" * 800)


class AudioCacheTests(CsvFixtureMixin, unittest.TestCase):
    def test_speed_change_does_not_reuse_faster_cached_audio(self):
        with patch.object(settings,'tts_speaking_rate',1.0):
            original = audio_cache.cache_path('Known reply','en')
        with patch.object(settings,'tts_speaking_rate',0.95):
            slower = audio_cache.cache_path('Known reply','en')
        self.assertNotEqual(original,slower)

    def test_cache_separates_text_and_language_and_rejects_truncated_audio(self):
        with scratch_dir() as root, patch.object(settings, "recordings_dir", root), \
             patch.object(audio_cache, "synthesize", side_effect=speech) as generate:
            first = audio_cache.cached_speech("Total 342", "en")
            timing = {}
            self.assertEqual(first, audio_cache.cached_speech("Total 342", "en", timing))
            self.assertTrue(timing["tts_cache_hit"])
            self.assertEqual(generate.call_count, 1)
            self.assertNotEqual(first, audio_cache.cached_speech("Total 343", "en"))
            self.assertNotEqual(first, audio_cache.cached_speech("Total 342", "hi"))
            first.write_bytes(first.read_bytes()[:44])
            self.assertFalse(audio_cache.valid_audio(first))
            audio_cache.cached_speech("Total 342", "en")
            self.assertEqual(generate.call_count, 4)

    def test_failure_leaves_no_final_or_partial_audio(self):
        with scratch_dir() as root, patch.object(settings, "recordings_dir", root), \
             patch.object(audio_cache, "synthesize", side_effect=RuntimeError("speech failed")):
            with self.assertRaises(RuntimeError):
                audio_cache.cached_speech("Known reply", "en")
            self.assertFalse(list(root.rglob("*.wav")))

    def test_plan_preserves_session_and_tracks_changed_csv_facts(self):
        row = load_students()[0]
        session = CallSession(expected_phone=DEMO_PHONE, records=[row])
        with patch.object(settings, "ask_language_before_identity", True):
            plan = audio_cache.call_audio_plan(session,include_queries=True)
            self.assertEqual({language for _, language in plan}, {"en", "hi", "kn"})
            self.assertEqual(session.state, CallState.AWAITING_NAME)
            self.assertEqual(session.wards, [])
            self.assertFalse(session.was_verified)
            self.assertTrue(any("342" in text for text, lang in plan if lang == "en"))
            changed = replace(row, total=343, percentage=85.75)
            other_plan = audio_cache.call_audio_plan(CallSession(expected_phone=DEMO_PHONE, records=[changed]),include_queries=True)
            self.assertNotEqual(plan, other_plan)
            english = [text for text, lang in plan if lang == "en"]
            self.assertTrue(any("Mohammed Khan" in text and "confirm your identity" in text for text in english))

    def test_menu_is_generated_with_three_native_voices_then_joined(self):
        from app.language import phrase
        with scratch_dir() as root, patch.object(settings,'recordings_dir',root), \
             patch.object(audio_cache,'synthesize',side_effect=speech) as generate:
            text = phrase('en','choose_language',college=settings.college_name)
            path = audio_cache.cached_speech(text,'en')
            self.assertTrue(audio_cache.valid_audio(path))
            self.assertEqual([call.args[2] for call in generate.call_args_list],['en','en','hi','kn'])
            self.assertNotIn('Say English',text)
            self.assertIn('हिंदी के लिए 2',text)
            self.assertIn('ಕನ್ನಡಕ್ಕಾಗಿ 3',text)
            with wave.open(str(path)) as joined:
                self.assertEqual(joined.getnframes(),4*800+3*1200)

    def test_prepared_plan_is_ready_and_optional_answers_match_actual_turns(self):
        session = CallSession(expected_phone=DEMO_PHONE, records=[load_students()[0]])
        with scratch_dir() as root, patch.object(settings, "recordings_dir", root), \
             patch.object(settings, "ask_language_before_identity", True), \
             patch.object(audio_cache, "synthesize", side_effect=speech):
            self.assertFalse(audio_cache.call_audio_status(session)["ready"])
            audio_cache.prepare_call_audio(session, include_queries=True)
            self.assertTrue(audio_cache.call_audio_status(session)["ready"])
            session.greeting()
            session.handle_text("English", use_llm=False)
            session.handle_text("yes", use_llm=False)
            answer = session.handle_text("What is the total?", use_llm=False)
            self.assertTrue(audio_cache.valid_audio(audio_cache.cache_path(answer.reply, session.language)))

    def test_default_preparation_caches_queries_in_all_languages_without_verifying_parent(self):
        row = load_students()[0]
        session = CallSession(expected_phone=DEMO_PHONE, records=[row])
        with scratch_dir() as root, patch.object(settings, 'recordings_dir', root), \
             patch.object(settings, 'ask_language_before_identity', True), \
             patch.object(settings, 'prepare_query_audio', True), \
             patch.object(audio_cache, 'synthesize', side_effect=speech):
            audio_cache.prepare_call_audio(session)
            self.assertFalse(session.was_verified)
            self.assertEqual(session.wards, [])
            for language in ('en', 'hi', 'kn'):
                for intent in ({'intent':'total'}, {'intent':'percentage'}, {'intent':'subject','subject':'maths'}):
                    text = audio_cache.reply_for_intent(row, intent, language)
                    self.assertTrue(audio_cache.valid_audio(audio_cache.cache_path(text, language)))


if __name__ == "__main__":
    unittest.main()
