import asyncio
from types import SimpleNamespace
import unittest

import numpy as np
import httpx
from unittest.mock import MagicMock, patch

from app import ari
from app.dtmf import ToneDigits
from app.live_stt import GrowingTranscriber
from app.rtp_audio import InboundRtp, ULAW
from scripts.meta_onnx import OnnxRecognizer


def tone(row, column, seconds=.18, amplitude=.15):
    t = np.arange(int(8000 * seconds)) / 8000
    audio = amplitude * (np.sin(2*np.pi*row*t) + np.sin(2*np.pi*column*t))
    return (audio * 32767).astype('<i2').tobytes()


class PhoneMenuToneTests(unittest.TestCase):
    def test_menu_native_fallback_does_not_override_ambiguous_choice(self):
        from scripts.indic_recognizer import IndicHybridRecognizer
        recognizer = IndicHybridRecognizer.__new__(IndicHybridRecognizer)
        recognizer.english = MagicMock(cuda_verified=True)
        recognizer.device = 'cpu'
        recognizer.verified_languages = set()
        native = MagicMock()
        native.run.return_value = [np.array([[[1.0,0.0]]])]
        recognizer.native_sessions = {'hi':(native,['▁हिंदी'])}
        recognizer.english.transcribe_waveform.return_value = ('थे धी',{})
        with patch('scripts.indic_recognizer.mel_features',return_value=np.zeros((1,80,10))):
            text, metrics = recognizer.transcribe_waveform(np.zeros(16000),16000,
                language='en',constrain_script=True,language_menu=True)
        self.assertEqual(text,'हिंदी')
        self.assertEqual(metrics['menu_candidate_language'],'hi')
        native.reset_mock()
        recognizer.english.transcribe_waveform.return_value = ('English or Hindi',{})
        text, _ = recognizer.transcribe_waveform(np.zeros(16000),16000,
            language='en',constrain_script=True,language_menu=True)
        self.assertEqual(text,'English or Hindi')
        native.run.assert_not_called()

    def test_service_and_hybrid_forward_three_script_menu_mode(self):
        from fastapi.testclient import TestClient
        from scripts import meta_stt_service as service
        from scripts.indic_recognizer import IndicHybridRecognizer
        from app.live_stt import pcm_wav
        recognizer = MagicMock()
        recognizer.transcribe_waveform.return_value = ('ಹಿಂದಿ', {})
        with patch.object(service,'pipeline',recognizer), patch.object(service,'backend','indic_hybrid'):
            response = TestClient(service.app).post('/transcribe',
                params={'language':'en','constrain_script':True,'language_menu':True},
                content=pcm_wav(bytes(16000)))
        self.assertEqual(response.status_code,200)
        self.assertEqual(recognizer.transcribe_waveform.call_args.kwargs,
            {'language':'en','constrain_script':True,'language_menu':True})
        hybrid = IndicHybridRecognizer.__new__(IndicHybridRecognizer)
        hybrid.english = MagicMock(cuda_verified=True)
        hybrid.english.transcribe_waveform.return_value = ('ಕನ್ನಡ',{})
        hybrid.transcribe_waveform(np.zeros(8000),8000,language='en',constrain_script=True,language_menu=True)
        self.assertEqual(hybrid.english.transcribe_waveform.call_args.kwargs,
            {'language':'en','constrain_script':True,'language_menu':True})

    def test_keypad_tones_survive_phone_companding_and_latch(self):
        for digit, frequencies in [('1',(697,1209)),('2',(697,1336)),('3',(697,1477))]:
            events = []
            detector = ToneDigits(events.append)
            raw = np.frombuffer(tone(*frequencies, seconds=.5),dtype='<i2').astype(np.int32)
            ulaw = np.abs(ULAW.astype(np.int32)[:,None]-raw[None,:]).argmin(axis=0)
            pcm = ULAW[ulaw].tobytes()
            for start in range(0,len(pcm),320):
                detector.feed(pcm[start:start+320])
            self.assertEqual(events,[digit])
            detector.feed(bytes(1600))
            detector.feed(pcm)
            self.assertEqual(events,[digit,digit])

    def test_short_tone_single_frequency_and_noise_are_not_choices(self):
        events = []
        detector = ToneDigits(events.append)
        detector.feed(tone(697,1209,seconds=.04))
        detector.feed(bytes(1600))
        detector.feed(tone(697,697))
        detector.feed(bytes(1600))
        noise = np.random.default_rng(1).normal(0,.1,8000)
        detector.feed((noise*32767).astype('<i2').tobytes())
        self.assertEqual(events,[])

    def test_menu_script_mask_keeps_all_three_scripts(self):
        pieces = ['<blank>','▁English','▁हिंदी','▁ಕನ್ನಡ','▁中文','▁العربية']
        recognizer = OnnxRecognizer.__new__(OnnxRecognizer)
        recognizer.tokenizer = SimpleNamespace(get_piece_size=lambda:len(pieces),
                                               id_to_piece=lambda i:pieces[i])
        recognizer.script_masks = {}
        self.assertEqual(recognizer._script_mask('menu').tolist(),[True,True,True,True,False,False])
        self.assertEqual(recognizer._script_mask('en').tolist(),[True,True,False,False,False,False])


class PhoneMenuAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_auxiliary_digit_is_forwarded_only_to_its_parent_menu(self):
        channel = 'aux-parent-menu'
        auxiliary = 'live-snoop-test-digit'
        ari._language_digits[channel] = asyncio.Queue(maxsize=1)
        ari.AUXILIARY_PARENTS[auxiliary] = channel
        try:
            ari._receive_ari_digit(auxiliary,'2')
            self.assertEqual(ari._language_digits[channel].get_nowait(),'2')
            ari._receive_ari_digit('live-snoop-unrelated','3')
            self.assertTrue(ari._language_digits[channel].empty())
            ari._language_digits.pop(channel)
            ari._receive_ari_digit(auxiliary,'1')
        finally:
            ari.AUXILIARY_PARENTS.pop(auxiliary,None)
            ari._language_digits.pop(channel,None)
    async def test_keypad_only_early_choice_does_not_start_speech_recognition(self):
        channel = 'keypad-only'
        ari._language_digits[channel] = asyncio.Queue(maxsize=1)
        tap = SimpleNamespace(collect=MagicMock(side_effect=AssertionError('No menu ASR')),receiver=MagicMock())
        async def play():
            ari._receive_language_digit(channel,'3')
            await asyncio.Future()
        try:
            with patch.object(ari.settings,'language_menu_keypad_only',True):
                capture = await asyncio.wait_for(ari._play_language_menu_listening(channel,None,tap,play()),1)
                self.assertEqual(await capture,(None,'3'))
                tap.collect.assert_not_called()
        finally:
            ari._language_digits.pop(channel,None)

    async def test_keypad_timeout_starts_after_prompt_and_returns_invalid_choice(self):
        channel = 'keypad-timeout'
        ari._language_digits[channel] = asyncio.Queue(maxsize=1)
        finished = asyncio.Event()
        try:
            with patch.object(ari.settings,'record_max_seconds',0.01):
                capture = asyncio.create_task(ari._wait_keypad_choice(channel,finished))
                await asyncio.sleep(0.02)
                self.assertFalse(capture.done())
                finished.set()
                self.assertEqual(await capture,(None,''))
        finally:
            ari._language_digits.pop(channel,None)

    async def test_digit_during_prompt_stops_playback_and_preserves_identity_gate(self):
        for choice in ('1','2','3'):
            channel = 'early-keypad-'+choice
            ari._language_digits[channel] = asyncio.Queue(maxsize=1)
            stopped = asyncio.Event()
            async def collect(session, **kwargs):
                await asyncio.Future()
            async def play():
                try:
                    ari._receive_language_digit(channel,choice)
                    await asyncio.Future()
                finally:
                    stopped.set()
            try:
                capture = await asyncio.wait_for(ari._play_language_menu_listening(
                    channel,SimpleNamespace(),SimpleNamespace(collect=collect),play()),1)
                self.assertEqual(await capture,(None,choice))
                self.assertTrue(stopped.is_set())
            finally:
                ari._language_digits.pop(channel,None)

    async def test_early_spoken_answer_is_not_discarded_after_prompt(self):
        channel = 'early-menu-speech'
        ari._language_digits[channel] = asyncio.Queue(maxsize=1)
        started, finish = asyncio.Event(), asyncio.Event()
        async def play():
            started.set()
            await asyncio.Future()
        async def collect(session, *, speech_started, playback_finished):
            await started.wait()
            speech_started.set()
            await finish.wait()
            return 'whole language choice'
        try:
            capture = await asyncio.wait_for(ari._play_language_menu_listening(
                channel,SimpleNamespace(),SimpleNamespace(collect=collect),play()),1)
            self.assertFalse(capture.done())
            finish.set()
            self.assertEqual(await capture,('whole language choice',None))
        finally:
            ari._language_digits.pop(channel,None)

    async def test_live_asr_requests_three_script_menu_mode(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'language':'en','text':'ಕನ್ನಡ'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            recognizer = GrowingTranscriber('en',client=client,constrain_script=True,language_menu=True)
            result = await recognizer._request(bytes(320))
        self.assertEqual(result['text'],'ಕನ್ನಡ')
        self.assertEqual(requests[0].url.params['language_menu'],'true')

    async def test_pcmu_keypad_fallback_selects_menu_without_ari_event(self):
        import struct
        channel = 'inband-menu'
        ari._language_digits[channel] = asyncio.Queue(maxsize=1)
        receiver = InboundRtp(on_digit=lambda d:ari._receive_language_digit(channel,d,'inband'))
        raw = np.frombuffer(tone(697,1336),dtype='<i2').astype(np.int32)
        ulaw = np.abs(ULAW.astype(np.int32)[:,None]-raw[None,:]).argmin(axis=0).astype(np.uint8)
        try:
            for start in range(0,len(ulaw),160):
                packet = struct.pack('!BBHII',128,0,start//160,start,1)+ulaw[start:start+160].tobytes()
                receiver.datagram_received(packet,('127.0.0.1',9000))
            self.assertEqual(ari._language_digits[channel].get_nowait(),'2')
            ari._language_digits.pop(channel)
            receiver.tones.reset()
            receiver.tones.feed(tone(697,1336))  # ignored after language selection
        finally:
            ari._language_digits.pop(channel,None)
