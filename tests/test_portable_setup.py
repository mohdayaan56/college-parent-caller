"""Deployment contracts: no secrets in source, preserve state, explicit GPU failures."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from scripts import portable_setup as setup
from scripts.inference_provider import configure_session, verify_cuda_execution
from tests.support import scratch_dir


class PortableSetupTests(unittest.TestCase):
    def test_profiles_preserve_credentials_csv_and_provider_without_host_dependencies(self):
        with scratch_dir() as root:
            (root / 'data').mkdir()
            (root / 'data/students.example.csv').write_text('demo fixture')
            state = root / 'runtime/deployment'
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'STATE', state):
                args = SimpleNamespace(profile='cpu', host_root='C:/A folder/College', sip_address='127.0.0.1')
                setup.configure(args)
                credentials = (state / 'credentials.json').read_text()
                (state / 'students.csv').write_text('private CSV retained')
                (state / 'asterisk/provider.conf').write_text('; private provider retained')
                args.profile = 'gpu'
                setup.configure(args)
                self.assertEqual(credentials, (state / 'credentials.json').read_text())
                self.assertEqual('private CSV retained', (state / 'students.csv').read_text())
                self.assertEqual('; private provider retained', (state / 'asterisk/provider.conf').read_text())
                services = json.loads((state / 'compose.json').read_text())['services']
                for name in ('stt', 'vits', 'ollama'):
                    self.assertEqual(services[name]['deploy']['resources']['reservations']['devices'][0]['capabilities'], ['gpu'])
                self.assertNotIn('deploy', services['caller'])
                self.assertEqual(services['stt']['environment']['META_ASR_DEVICE'], 'cuda')
                self.assertEqual(services['vits']['environment']['VITS_DECODER_DEVICE'], 'cuda')
                self.assertTrue(any(m['target'] == '/workspace/models' and not m['read_only'] for m in services['voice-export']['volumes']))
                self.assertEqual(services['caller']['environment']['OLLAMA_THINK'], 'false')
                self.assertIn('/runtime/deployment/', services['caller']['environment']['CAMPAIGN_DB_PATH'])
                self.assertTrue(all('/hermes/' not in json.dumps(service) for service in services.values()))

    def test_ini_address_injection_is_rejected(self):
        with self.assertRaises(ValueError):
            setup.configure(SimpleNamespace(sip_address='127.0.0.1\npassword=x'))

    def test_manifest_cannot_write_outside_models(self):
        with scratch_dir() as root:
            lock = root / 'lock.json'
            lock.write_text(json.dumps({'assets': [{'path': 'models/../../outside'}]}))
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'LOCK', lock), self.assertRaises(ValueError):
                setup.assets()

    def test_model_reuse_requires_checksum_not_just_size(self):
        with scratch_dir() as root:
            path = root / 'models/weight'
            path.parent.mkdir()
            path.write_bytes(b'good')
            entry = {'path': 'models/weight', 'bytes': 4, 'sha256': setup.digest(path)}
            with patch.object(setup, 'ROOT', root):
                self.assertTrue(setup.valid(entry))
                path.write_bytes(b'evil')
                self.assertFalse(setup.valid(entry))

    def test_gpu_profile_does_not_silently_accept_cpu(self):
        import torch
        with patch.object(torch.cuda, 'is_available', return_value=False), self.assertRaises(RuntimeError):
            configure_session(MagicMock(), MagicMock(), 'cuda')
        session = MagicMock()
        session.get_providers.return_value = ['CPUExecutionProvider']
        with self.assertRaises(RuntimeError):
            verify_cuda_execution(session)

    def test_gpu_probe_requires_real_cuda_kernel_event_and_deletes_trace(self):
        with scratch_dir() as root:
            trace = root / 'profile.json'
            session = MagicMock()
            session.get_providers.return_value = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            session.end_profiling.return_value = str(trace)
            for provider in ('CPUExecutionProvider', 'CUDAExecutionProvider'):
                trace.write_text(json.dumps([{'args': {'provider': provider}}]))
                if provider == 'CPUExecutionProvider':
                    with self.assertRaises(RuntimeError):
                        verify_cuda_execution(session)
                else:
                    verify_cuda_execution(session)
                self.assertFalse(trace.exists())


if __name__ == '__main__':
    unittest.main()
