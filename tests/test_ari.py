import asyncio
import unittest
import wave
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, AsyncMock

from app import ari
from app.tts import write_ulaw_or_pcm8k
from tests.support import scratch_dir


class AriTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_waits_for_play_post_then_stops_actual_playback(self):
        posted, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def fake_ari(method, path):
            calls.append((method, path))
            if method == 'POST':
                posted.set()
                await release.wait()
            from types import SimpleNamespace
            return SimpleNamespace(status_code=204, raise_for_status=lambda:None)
        with patch.object(ari, '_media_uri', return_value='sound:test'), patch.object(ari, '_ari', fake_ari):
            playback = asyncio.create_task(ari._play('barge-stop', Path('test.wav')))
            await posted.wait()
            playback.cancel()
            await asyncio.sleep(0)
            self.assertEqual(len(calls), 1)
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await playback
        playback_id = calls[0][1].split('/play/')[1].split('?')[0]
        self.assertEqual(calls[1], ('DELETE', '/playbacks/' + playback_id))
        self.assertNotIn('PlaybackFinished:' + playback_id, ari._waiters)

    async def test_barge_in_cancels_reply_and_keeps_same_capture(self):
        from types import SimpleNamespace
        playing, stopped, finish_question = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def collect(session, *, speech_started, playback_finished):
            await playing.wait()
            speech_started.set()
            await finish_question.wait()
            return 'whole new question'
        async def reply(*args):
            playing.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        session = SimpleNamespace(session_id='test', language='en')
        tap, turn = SimpleNamespace(collect=collect), SimpleNamespace(reply='long answer')
        with patch.object(ari, '_play_reply', reply), patch.object(ari, '_save_timing_report') as report:
            capture = await asyncio.wait_for(ari._play_reply_listening('test', session, tap, turn, {}), 1)
            self.assertTrue(stopped.is_set())
            self.assertFalse(capture.done())
            finish_question.set()
            self.assertEqual(await capture, 'whole new question')
            self.assertEqual(report.call_args.args[0]['phase'], 'barge_in')

    async def test_call_loop_processes_interrupted_question_without_second_capture(self):
        from types import SimpleNamespace
        from app.conversation import CallState
        from app.rtp_audio import LiveTurn
        channel = 'barge-loop'
        questions, captures = [], []
        speaking, interrupted = asyncio.Event(), asyncio.Event()
        session = SimpleNamespace(session_id='test', language='en', state=CallState.AWAITING_NAME,
                                  greeting=lambda:SimpleNamespace(hangup=False),
                                  last_llm_used=False, last_llm_seconds=0.0, last_requests=[])
        def handle(text):
            questions.append(text)
            session.state = CallState.VERIFIED if text == 'yes' else CallState.ENDED
            return SimpleNamespace(reply='summary' if text == 'yes' else 'goodbye', hangup=text != 'yes')
        session.handle_text = handle
        async def collect(session, **kwargs):
            captures.append(kwargs)
            if kwargs:
                await speaking.wait()
                kwargs['speech_started'].set()
                return LiveTurn(b'question', 'no', {'final_asr_seconds':.01, 'recording_finished_at':0})
            return LiveTurn(b'yes', 'yes', {'final_asr_seconds':.01, 'recording_finished_at':0})
        async def reply(channel, text, *args):
            if text == 'summary':
                speaking.set()
                try:
                    await asyncio.Future()
                finally:
                    interrupted.set()
        with scratch_dir() as root, patch.object(ari.settings, 'recordings_dir', root), \
             patch.object(ari, '_answer', AsyncMock()), patch.object(ari, '_hangup', AsyncMock()), \
             patch.object(ari, 'prepare_greeting', return_value=root/'fake.wav'), \
             patch.object(ari, '_play', AsyncMock()), patch.object(ari, '_play_reply', reply), \
             patch.object(ari, '_save_timing_report'):
            await asyncio.wait_for(ari._handle_parent_turns(channel, session, SimpleNamespace(collect=collect)), 2)
        self.assertEqual(questions, ['yes', 'no'])
        self.assertEqual(len(captures), 2)
        self.assertTrue(interrupted.is_set())


    async def test_finished_reply_keeps_listening_without_rearming(self):
        from types import SimpleNamespace
        ready, finish_question = asyncio.Event(), asyncio.Event()
        async def collect(session, *, speech_started, playback_finished):
            await playback_finished.wait()
            ready.set()
            await finish_question.wait()
            return 'next question'
        session = SimpleNamespace(session_id='test', language='en')
        with patch.object(ari, '_play_reply', new=AsyncMock()):
            capture = await ari._play_reply_listening('test', session, SimpleNamespace(collect=collect),
                                                     SimpleNamespace(reply='answer'), {})
            await asyncio.wait_for(ready.wait(), 1)
            finish_question.set()
            self.assertEqual(await capture, 'next question')

    async def test_menu_digit_cancels_capture_and_cleans_up(self):
        queue = ari._language_digits['keypad-test'] = asyncio.Queue(maxsize=1)
        stopped = asyncio.Event()
        async def capture():
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        try:
            ari._receive_language_digit('keypad-test', '9')
            self.assertTrue(queue.empty())
            ari._receive_language_digit('keypad-test', '3')
            result, digit = await ari._capture_or_language_digit('keypad-test', capture())
            self.assertIsNone(result)
            self.assertEqual(digit, '3')
            self.assertTrue(stopped.is_set())
        finally:
            ari._language_digits.pop('keypad-test', None)
        ari._receive_language_digit('keypad-test', '1')  # ignored outside menu

    async def test_menu_speech_wins_without_pending_digit_task(self):
        ari._language_digits['speech-test'] = asyncio.Queue(maxsize=1)
        async def capture():
            return 'Kannada'
        try:
            self.assertEqual(await ari._capture_or_language_digit('speech-test', capture()), ('Kannada', None))
        finally:
            ari._language_digits.pop('speech-test', None)

    async def test_streaming_latency_uses_ingress_speech_clock_once(self):
        import json
        with scratch_dir() as root:
            ari._play_timings['live-timing']={'recording_finished_at':11,'last_voice_at':10,
                'streaming_stt':True,'recorded_audio_seconds':4,'record_silence_seconds':1}
            try:
                with patch.object(ari.settings,'recordings_dir',root),patch.object(ari,'monotonic',return_value=15):
                    ari._playback_started('live-timing')
                report=json.loads((root/'latency.jsonl').read_text())
                self.assertEqual(report['speech_end_to_playback_estimate_seconds'],5)
                self.assertEqual(report['recording_finished_to_playback_seconds'],4)
                self.assertNotIn('endpoint_silence_assumption_seconds',report)
            finally:ari._play_timings.pop('live-timing',None)

    async def test_latency_uses_playback_started_even_before_http_response(self):
        timing = {"recording_finished_at": 10.0, "recorded_audio_seconds": 5.0, "record_silence_seconds": 3}

        async def fake_ari(_method, path):
            playback_id = path.split("/play/")[1].split("?")[0]
            ari._playback_started(playback_id)
            ari._notify(f"PlaybackFinished:{playback_id}", {"type": "PlaybackFinished"})

            class Response:
                def raise_for_status(self):
                    pass
            return Response()

        with scratch_dir() as root:
            with patch.object(ari.settings, "recordings_dir", root), \
                 patch.object(ari, "_media_uri", return_value="sound:test"), \
                 patch.object(ari, "_ari", fake_ari), patch.object(ari, "monotonic", return_value=14.5):
                await ari._play("timing-test-channel", root / "test.wav", timing)
            import json
            report = json.loads((root / "latency.jsonl").read_text())
            self.assertEqual(report["recording_finished_to_playback_seconds"], 4.5)
            self.assertEqual(report["speech_end_to_playback_estimate_seconds"], 7.5)
            self.assertNotIn("recording_finished_at", report)
            self.assertFalse(ari._play_timings)

    async def test_duration_limit_does_not_assume_silence_endpoint(self):
        with scratch_dir() as root:
            ari._play_timings["duration-limit"] = {"recording_finished_at": 10, "recorded_audio_seconds": 12}
            try:
                with patch.object(ari.settings, "recordings_dir", root), \
                     patch.object(ari, "monotonic", return_value=14.5):
                    ari._playback_started("duration-limit")
                import json
                report = json.loads((root / "latency.jsonl").read_text())
                self.assertNotIn("speech_end_to_playback_estimate_seconds", report)
            finally:
                ari._play_timings.pop("duration-limit", None)

    async def test_playback_event_arriving_before_http_response(self):
        async def fake_ari(_method, path):
            playback_id = path.split("/play/")[1].split("?")[0]
            ari._notify(f"PlaybackFinished:{playback_id}", {"type": "PlaybackFinished"})

            class Response:
                def raise_for_status(self):
                    pass

            return Response()

        with scratch_dir() as root:
            wav = root / "sample.wav"
            wav.write_bytes(b"RIFF" + b"\0" * 50)
            with patch.object(ari, "_media_uri", return_value="sound:test"), patch.object(ari, "_ari", fake_ari):
                await asyncio.wait_for(ari._play("test-channel", wav), timeout=1)

    async def test_recording_is_downloaded_from_asterisk(self):
        audio = BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x01\x00" * 800)
        requests = []

        async def fake_ari(method, path, **_kwargs):
            requests.append((method, path))
            if method == "POST":
                self.assertEqual(_kwargs["params"]["maxSilenceSeconds"], ari.settings.record_silence_seconds)
                ari._notify("RecordingFinished:test-record", {"type": "RecordingFinished"})

            class Response:
                content = audio.getvalue() if method == "GET" else b""

                def raise_for_status(self):
                    pass

            return Response()

        with scratch_dir() as root:
            with patch.object(ari.settings, "recordings_dir", root), patch.object(ari, "_ari", fake_ari):
                recording = await ari._record("test-record-channel", "test-record")
            self.assertEqual(recording.read_bytes(), audio.getvalue())
            self.assertIn(("GET", "/recordings/stored/test-record/file"), requests)
            self.assertIn(("DELETE", "/recordings/stored/test-record"), requests)

    async def test_zoiper_audio_check_plays_recorded_caller_audio(self):
        operations = []

        async def answer(_channel):
            operations.append("answer")

        async def play(_channel, path):
            operations.append("play:" + path.name)

        async def record(_channel, _name):
            operations.append("record")
            return Path("caller.wav")

        async def hangup(_channel):
            operations.append("hangup")

        def synthesize(_text, path, _language):
            path.write_bytes(b"prompt")

        def convert(_original, path):
            path.write_bytes(b"converted")

        with scratch_dir() as root:
            with patch.object(ari.settings, "recordings_dir", root), patch.object(ari, "_answer", answer), \
                 patch.object(ari, "_play", play), patch.object(ari, "_record", record), \
                 patch.object(ari, "_hangup", hangup), patch.object(ari, "synthesize", synthesize), \
                 patch.object(ari, "write_ulaw_or_pcm8k", convert):
                await ari._handle_zoiper_audio_check("test-lab-channel")
        self.assertEqual(operations[0], "answer")
        self.assertEqual(operations[2:], ["record", "play:caller.wav", "hangup"])


class AudioTests(unittest.TestCase):
    def test_greeting_cache_reuses_exact_prompt_and_invalidates_changed_text(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with scratch_dir() as root:
            session = SimpleNamespace(language="en", greeting=Mock(return_value=SimpleNamespace(reply="Please say English, Hindi, or Kannada.")))
            generated = []
            def fake_synthesize(text, path, language):
                generated.append((text, language))
                with wave.open(str(path), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(8000)
                    wav.writeframes(b"\x01\x00" * 800)
            with patch.object(ari.settings, "recordings_dir", root), patch("app.audio_cache.synthesize", fake_synthesize):
                first = ari.prepare_greeting(session)
                self.assertEqual(first, ari.prepare_greeting(session))
                self.assertEqual(len(generated), 1)
                session.greeting.return_value.reply = "Please say your preferred language after the beep."
                self.assertNotEqual(first, ari.prepare_greeting(session))
                self.assertEqual(len(generated), 2)

    def test_mobile_route_uses_configured_device_and_validated_number(self):
        with patch.object(ari.settings, "outbound_channel_driver", "mobile"), \
             patch.object(ari.settings, "mobile_device", "samsung"), \
             patch.object(ari.settings, "sip_dial_format", "national"):
            self.assertEqual(ari.sip_endpoint_for("+91 98765 43210"), "Mobile/samsung/9876543210")
            with self.assertRaises(ValueError):
                ari.sip_endpoint_for("lab")
            with patch.object(ari.settings, "mobile_device", "samsung/bad"):
                with self.assertRaises(ValueError):
                    ari.sip_endpoint_for("+919876543210")

    def test_playback_file_is_copied_to_asterisk_sound_directory(self):
        with scratch_dir() as root:
            source = root / "prompt.wav"
            source.write_bytes(b"RIFF" + b"\0" * 50)
            sounds_dir = root / "sounds" / "parent-marks"
            with patch.object(ari.settings, "asterisk_sounds_dir", sounds_dir):
                self.assertEqual(ari._media_uri(source), "sound:parent-marks/prompt")
            self.assertEqual((sounds_dir / "prompt.wav").read_bytes(), source.read_bytes())

    def test_sip_trunk_number_formats(self):
        with patch.object(ari.settings, "sip_trunk", "provider"), patch.object(ari.settings, "outbound_channel_driver", "pjsip"):
            for dial_format, expected in (
                ("e164_digits", "919876543210"),
                ("e164_plus", "+919876543210"),
                ("national", "9876543210"),
            ):
                with patch.object(ari.settings, "sip_dial_format", dial_format):
                    self.assertEqual(
                        ari.sip_endpoint_for("+91 98765 43210"), f"PJSIP/{expected}@provider"
                    )

    def test_pcm_wav_downsamples_without_soundfile(self):
        with scratch_dir() as root:
            src, dest = root / "in.wav", root / "out.wav"
            with wave.open(str(src), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x01\x00" * 1600)
            write_ulaw_or_pcm8k(src, dest)
            with wave.open(str(dest), "rb") as wav:
                self.assertEqual(wav.getframerate(), 8000)
                self.assertEqual(wav.getnframes(), 800)


if __name__ == "__main__":
    unittest.main()
