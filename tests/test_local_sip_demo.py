import configparser
import json
import tempfile
import unittest
from pathlib import Path

from scripts.select_local_sip_demo import configure


class LocalSipConfigurationTests(unittest.TestCase):
    def test_route_preserves_models_data_ports_and_private_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / 'asterisk').mkdir()
            (state / 'demo-softphone').mkdir()
            compose = {'services': {'caller': {'environment': {'OUTBOUND_CHANNEL_DRIVER': 'mobile',
                                                               'META_STT_URL': 'http://stt:8110',
                                                               'TTS_SPEAKING_RATE': '0.95'},
                                               'ports': ['127.0.0.1:8113:8113']},
                                    'stt': {'unchanged': True}}}
            (state / 'compose.json').write_text(json.dumps(compose))
            (state / 'caller-overrides.json').write_text(json.dumps({'OLLAMA_MODEL': 'fixture'}))
            (state / 'credentials.json').write_text(json.dumps({'ari_password': 'test-ari', 'sip_password': 'test-sip'}))
            (state / 'asterisk/pjsip.conf').write_text('[zoiper1]\ntype=endpoint\ntransport=transport-udp\n')
            provider = state / 'asterisk/provider.conf'
            provider.write_text('; preserve provider configuration')
            csv = state / 'students.csv'
            csv_text = 'parent_name,student_id\nzulkharnain,demo-001\n'
            csv.write_text(csv_text)
            ini = state / 'demo-softphone/MicroSIP.ini'
            ini.write_text('[Settings]\naudioInputDevice=fixture device\n[Account1]\npassword=old\n', encoding='utf-16')
            configure(state)
            configure(state)
            result = json.loads((state / 'compose.json').read_text())
            self.assertEqual(result['services']['stt'], compose['services']['stt'])
            self.assertEqual(result['services']['caller']['ports'], compose['services']['caller']['ports'])
            env = result['services']['caller']['environment']
            self.assertEqual(env['OUTBOUND_CHANNEL_DRIVER'], 'pjsip')
            self.assertEqual(env['SIP_TRUNK'], 'zoiper1')
            self.assertEqual(env['ARI_URL'], 'http://pbx:8088/ari')
            self.assertEqual(env['STREAMING_AUDIO_ADVERTISE_HOST'], 'caller')
            self.assertEqual(env['META_STT_URL'], 'http://stt:8110')
            self.assertEqual(env['TTS_SPEAKING_RATE'], '0.95')
            self.assertEqual(json.loads((state / 'local-sip/compose.json.before-local-sip').read_text()), compose)
            self.assertEqual((state / 'asterisk/pjsip.conf').read_text().count('dtmf_mode=rfc4733'), 1)
            self.assertEqual(provider.read_text(), '; preserve provider configuration')
            self.assertEqual(csv.read_text(), csv_text)
            softphone = configparser.ConfigParser(interpolation=None)
            softphone.read(ini, encoding='utf-16')
            self.assertEqual(softphone['Settings']['audioinputdevice'], 'fixture device')
            self.assertEqual(softphone['Settings']['autoanswer'], '0')
            self.assertEqual(softphone['Account1']['password'], 'test-sip')
            self.assertEqual(softphone['Account1']['displayname'], 'zulkharnain')
