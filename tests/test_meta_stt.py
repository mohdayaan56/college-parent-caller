from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from app import stt
from scripts import meta_stt_service as service


def wav(seconds=1):
    buffer = BytesIO()
    sf.write(buffer, np.zeros(8000 * seconds), 8000, format='WAV', subtype='PCM_16')
    return buffer.getvalue()


class MetaServiceTests(unittest.TestCase):
    def test_native_backend_preserves_menu_script_constraint(self):
        recognizer = MagicMock()
        recognizer.transcribe_waveform.return_value = ('hindi',{})
        with patch.object(service,'pipeline',recognizer), patch.object(service,'backend','indic_hybrid'):
            response = TestClient(service.app).post('/transcribe',
                params={'language':'en','constrain_script':True}, content=wav())
        self.assertEqual(response.status_code,200)
        self.assertEqual(recognizer.transcribe_waveform.call_args.kwargs,
                         {'language':'en','constrain_script':True})
    def test_whisper_backend_passes_explicit_selected_language(self):
        recognizer = MagicMock()
        recognizer.transcribe_waveform.return_value = ('हाँ', {'inference_seconds':0.2})
        with patch.object(service,'pipeline',recognizer), patch.object(service,'backend','faster_whisper'):
            response = TestClient(service.app).post('/transcribe',
                params={'language':'hi','constrain_script':True}, content=wav())
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['text'],'हाँ')
        self.assertEqual(recognizer.transcribe_waveform.call_args.kwargs,{'language':'hi'})

    def test_same_ctc_instance_handles_all_languages_using_in_memory_audio(self):
        inputs = []
        class Pipeline:
            def transcribe(self, audio, **options):
                inputs.extend(audio)
                self.assertions(audio, options)
                return ['local transcript']

            def assertions(self, audio, options):
                assert isinstance(audio[0], bytes)
                assert audio[0][:4] == b'RIFF'
                assert options['lang'] is None

        client = TestClient(service.app)
        with patch.object(service, 'pipeline', Pipeline()), \
             patch.object(service, 'model_card', 'college_omniASR_CTC_300M_v2'), \
             patch.object(service, 'backend', 'fairseq2'), \
             patch.dict('os.environ', {'META_ASR_TEMP_DIR': str(stt.settings.recordings_dir)}):
            for language in ('en', 'hi', 'kn'):
                response = client.post('/transcribe', params={'language': language}, content=wav())
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['language'], language)
                self.assertEqual(response.json()['text'], 'local transcript')
        self.assertEqual(len(inputs), 3)

    def test_invalid_audio_language_duration_and_busy_service_fail_explicitly(self):
        client = TestClient(service.app)
        self.assertEqual(client.post('/transcribe', content=b'not WAV').status_code, 422)
        self.assertEqual(client.post('/transcribe?language=fr', content=wav()).status_code, 422)
        self.assertEqual(client.post('/transcribe', content=wav(61)).status_code, 422)
        self.assertEqual(client.post('/transcribe', content=b'x' * (service.MAX_BYTES + 1)).status_code, 413)
        with patch.object(service, 'pipeline', SimpleNamespace()):
            service.inference_lock.acquire()
            try:
                self.assertEqual(client.post('/transcribe', content=wav()).status_code, 429)
            finally:
                service.inference_lock.release()

    def test_app_transport_disables_proxy_and_rejects_mismatched_language(self):
        response = MagicMock()
        response.json.return_value = {'language': 'hi', 'text': 'कुल अंक'}
        client = MagicMock()
        client.post.return_value = response
        manager = MagicMock()
        manager.__enter__.return_value = client
        with patch('httpx.Client', return_value=manager) as factory, \
             patch.object(Path, 'read_bytes', return_value=b'wav bytes'):
            self.assertEqual(stt._meta_omnilingual('turn.wav', 'hi'), 'कुल अंक')
            self.assertFalse(factory.call_args.kwargs['trust_env'])
            self.assertEqual(client.post.call_args.kwargs['content'], b'wav bytes')
            self.assertTrue(client.post.call_args.kwargs['params']['constrain_script'])
            with self.assertRaises(RuntimeError):
                stt._meta_omnilingual('turn.wav', 'kn')


if __name__ == '__main__':
    unittest.main()
