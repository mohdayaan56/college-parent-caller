"""Portable deployment management; bootstrap actions need only the standard library.

Runs in the Python bootstrap container on Windows, or directly on Linux.
Never places calls, installs OS drivers, prunes Docker or replaces a WSL kernel.
"""
from __future__ import annotations
import argparse
import copy
import csv
import base64
import hashlib
import io
import json
import platform
import secrets
import shutil
import ssl
import sys
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # also work when executed by filename from any cwd
STATE = ROOT / 'runtime/deployment'
LOCK = ROOT / 'deploy/models.lock.json'
GIB = 1024**3
MODEL = 'gemma4-e2b-college:qat4'


def trust_context():
    context = ssl.create_default_context()
    for certificate in (ROOT / 'deploy/ca').glob('*.crt'):
        context.load_verify_locations(cafile=str(certificate))
    return context


def assets():
    entries = json.loads(LOCK.read_text(encoding='utf-8'))['assets']
    for entry in entries:
        target = (ROOT / entry['path']).resolve()
        if not target.is_relative_to((ROOT / 'models').resolve()):
            raise ValueError('Model manifest path escapes models directory')
    return entries


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def valid(entry, hashes=True):
    target = ROOT / entry['path']
    return target.is_file() and target.stat().st_size == entry['bytes'] and (
        not hashes or digest(target) == entry['sha256'])


def url(entry):
    filename = urllib.parse.quote(entry['file'], safe='/')
    return f"https://huggingface.co/{entry['repo']}/resolve/{entry['revision']}/{filename}?download=true"


def request_json(address, body=None, headers=None, timeout=20, method=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(address, data=data, method=method,
        headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout, context=trust_context()) as response:
        return json.load(response)


def check(args):
    missing = [entry for entry in assets() if not valid(entry, hashes=args.hashes)]
    download_bytes = sum(entry['bytes'] for entry in missing)
    # Conservative fresh-install allowance: image layers, build/export workspace,
    # Ollama's separate import, and reserve. Docker may store its disk elsewhere.
    required = download_bytes + (24 if args.profile == 'gpu' else 14) * GIB
    free = shutil.disk_usage(ROOT).free
    report = {'architecture': platform.machine(), 'profile': args.profile,
        'model_bytes_total': sum(entry['bytes'] for entry in assets()),
        'missing_download_bytes': download_bytes, 'free_project_disk_bytes': free,
        'recommended_free_bytes_for_install': required,
        'disk_ok': free >= required, 'missing_assets': [entry['path'] for entry in missing],
        'network': [], 'scope': 'Preflight, not a speech-quality or phone-call acceptance test'}
    if args.online:
        endpoints = [('package-index', 'https://pypi.org/simple/onnxruntime-gpu/'),
            ('pytorch-wheels', 'https://download.pytorch.org/whl/cu128/torch/'),
            ('docker-registry', 'https://registry-1.docker.io/v2/')]
        # Probe every selected artifact's download redirect without fetching weights.
        endpoints += [(entry['path'], url(entry)) for entry in assets()]
        for name, address in endpoints:
            try:
                req = urllib.request.Request(address, method='HEAD')
                with urllib.request.urlopen(req, timeout=20, context=trust_context()) as response:
                    status = response.status
                ok = 200 <= status < 400
            except urllib.error.HTTPError as error:
                status = error.code
                ok = name == 'docker-registry' and status == 401
            except (OSError, urllib.error.URLError) as error:
                status, ok = 'unreachable', False
                report['network'].append({'asset': name, 'status': status, 'ok': ok, 'reason': str(error)[:500]})
                continue
            report['network'].append({'asset': name, 'status': status, 'ok': ok})
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / 'preflight.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0 if report['disk_ok'] and all(item['ok'] for item in report['network']) else 1


def download_entries(entries):
    verified = {entry['path']: valid(entry) for entry in entries}
    missing = [entry for entry in entries if not verified[entry['path']]]
    needed = sum(entry['bytes'] for entry in missing)
    if shutil.disk_usage(ROOT).free < needed + 3 * GIB:
        raise RuntimeError('Insufficient space for missing models and a 3 GiB reserve')
    for entry in entries:
        if verified[entry['path']]:
            print('Verified ' + entry['path'], flush=True)
            continue
        target = ROOT / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.part')
        print('Downloading ' + entry['path'], flush=True)
        # Bootstrap container needs no pip packages or curl. Resume via HTTP Range.
        for attempt in range(3):
            if partial.is_file() and partial.stat().st_size == entry['bytes']:
                if digest(partial) == entry['sha256']:
                    break
                partial.unlink()
            offset = partial.stat().st_size if partial.exists() else 0
            req = urllib.request.Request(url(entry), headers={'Range': f'bytes={offset}-'} if offset else {})
            try:
                with urllib.request.urlopen(req, timeout=90, context=trust_context()) as response:
                    resumed = offset and response.status == 206
                    if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                        raise RuntimeError('Server returned the wrong resume offset')
                    with partial.open('ab' if resumed else 'wb') as stream:
                        while chunk := response.read(4 * 1024**2):
                            stream.write(chunk)
                break
            except (OSError, urllib.error.URLError):
                if attempt == 2:
                    raise
                time.sleep(2)
        if partial.stat().st_size != entry['bytes'] or digest(partial) != entry['sha256']:
            raise RuntimeError('Downloaded asset failed size/SHA256: ' + entry['path'])
        partial.replace(target)
def download(_args):
    download_entries(assets())
    directory = ROOT / 'models/gemma-4-E2B-text-qat'
    gguf = next(entry for entry in assets() if entry['path'].endswith('.gguf'))
    (directory / 'Modelfile.docker').write_text(
        f'FROM "/workspace/{gguf["path"]}"\nPARAMETER num_ctx 2048\n'
        'PARAMETER num_predict 96\nPARAMETER num_thread 4\nPARAMETER temperature 0.2\n')
    print('All selected model assets verified; no other models downloaded.', flush=True)


def configure(args):
    # Reject special characters in addresses used inside Asterisk INI files.
    address = args.sip_address
    if not address or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for c in address):
        raise ValueError('SIP address must be a host IPv4 address or DNS name')
    host_root = args.host_root or str(ROOT)
    STATE.mkdir(parents=True, exist_ok=True)
    credentials = STATE / 'credentials.json'
    if credentials.exists():
        secret = json.loads(credentials.read_text())
    else:
        secret = {key: secrets.token_hex(24) for key in ('ari_password', 'sip_password', 'admin_token')}
        credentials.write_text(json.dumps(secret, indent=2))
        credentials.chmod(0o600)
    config = STATE / 'asterisk'
    config.mkdir(exist_ok=True)
    for name in ('sounds', 'recordings', 'ollama', 'checks'):
        (STATE / name).mkdir(exist_ok=True)
    example = ROOT / 'data/students.example.csv'
    csv = STATE / 'students.csv'
    if not csv.exists():
        shutil.copyfile(example, csv)
    (config / 'http.conf').write_text('[general]\nenabled=yes\nbindaddr=0.0.0.0\nbindport=8088\n')
    (config / 'ari.conf').write_text('[general]\nenabled=yes\npretty=no\n[college]\ntype=user\nread_only=no\npassword=' + secret['ari_password'] + '\n')
    (config / 'rtp.conf').write_text('[general]\nrtpstart=10100\nrtpend=10120\n')
    (config / 'extensions.conf').write_text('[from-internal]\nexten => 1002,1,Answer()\n same => n,Stasis(parent-marks)\n same => n,Hangup()\nexten => 600,1,Answer()\n same => n,Echo()\n same => n,Hangup()\n[from-provider]\nexten => _X.,1,Hangup()\n')
    (config / 'pjsip.conf').write_text(f'''[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
external_signaling_address={address}
external_signaling_port=15070
external_media_address={address}
local_net=127.0.0.1/32
[zoiper1-auth]
type=auth
auth_type=userpass
username=zoiper1
password={secret['sip_password']}
[zoiper1]
type=aor
max_contacts=1
remove_existing=yes
qualify_frequency=30
[zoiper1]
type=endpoint
dtmf_mode=rfc4733
transport=transport-udp
media_address={address}
context=from-internal
disallow=all
allow=ulaw,alaw
auth=zoiper1-auth
aors=zoiper1
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
# Add the college's provider here using deploy/sip-provider.example.conf.
#include provider.conf
''')
    if not (config / 'provider.conf').exists():
        (config / 'provider.conf').write_text('; No PSTN provider provisioned yet.\n')
    def volume(relative, target, readonly=False):
        source = str(Path(host_root) / relative).replace('\\', '/')
        return {'type': 'bind', 'source': source, 'target': target, 'read_only': readonly}
    def speech(command, port, environment):
        return {'build': {'context': host_root, 'dockerfile': 'deploy/Dockerfile', 'target': args.profile},
            'image': 'college-parent-speech:' + args.profile, 'working_dir': '/workspace',
            'command': command, 'ports': [f'127.0.0.1:{port}:{port}'],
            'volumes': [volume('app', '/workspace/app', True), volume('scripts', '/workspace/scripts', True),
                        volume('models', '/workspace/models', True), volume('data', '/workspace/data', True),
                        volume('runtime/deployment', '/workspace/runtime/deployment')],
            'environment': environment, 'restart': 'unless-stopped'}
    def gpu(service):
        if args.profile == 'gpu':
            service['deploy'] = {'resources': {'reservations': {'devices': [
                {'driver': 'nvidia', 'count': 1, 'capabilities': ['gpu']}]}}}
    device = 'cuda' if args.profile == 'gpu' else 'cpu'
    stt = speech(['uvicorn', 'scripts.meta_stt_service:app', '--host', '0.0.0.0', '--port', '8110'], 8110,
        {'META_ASR_BACKEND': 'onnx', 'META_ASR_DEVICE': device,
         'META_ASR_ONNX_DIR': '/workspace/models/meta-onnx', 'META_ASR_PRECISION': 'fp32',
         'META_ASR_THREADS': '4', 'META_ASR_WARMUP': '1'})
    vits = speech(['uvicorn', 'scripts.vits_service:app', '--host', '0.0.0.0', '--port', '8112'], 8112,
        {'VITS_BACKEND': 'onnx', 'VITS_DECODER_DEVICE': device, 'VITS_THREADS': '4',
         'CSV_PATH': '/workspace/runtime/deployment/students.csv'})
    gpu(stt); gpu(vits)
    voice_export = copy.deepcopy(vits)
    voice_export.pop('ports')
    voice_export.pop('deploy', None)
    voice_export['build']['target'] = 'cpu'
    voice_export['image'] = 'college-parent-speech:cpu'
    voice_export['profiles'] = ['setup']
    voice_export['restart'] = 'no'
    voice_export['environment']['VITS_BACKEND'] = 'torch'
    voice_export['environment']['VITS_DECODER_DEVICE'] = 'cpu'
    voice_export['command'] = ['python', '-m', 'scripts.prepare_deployment_voices']
    for mount in voice_export['volumes']:
        if mount['target'] == '/workspace/models':
            mount['read_only'] = False
    ollama = {'image': 'ollama/ollama:0.40.0', 'ports': ['127.0.0.1:11444:11434'],
        'volumes': [volume('runtime/deployment/ollama', '/root/.ollama'), volume('models', '/workspace/models', True)],
        'environment': {'OLLAMA_KEEP_ALIVE': '30m', 'OLLAMA_NUM_PARALLEL': '1', 'OLLAMA_MAX_LOADED_MODELS': '1'},
        'restart': 'unless-stopped'}
    gpu(ollama)
    caller_env = {'APP_HOST': '0.0.0.0', 'APP_PORT': '8113', 'ADMIN_API_TOKEN': secret['admin_token'],
        'CSV_PATH': '/workspace/runtime/deployment/students.csv',
        'RECORDINGS_DIR': '/workspace/runtime/deployment/recordings', 'COLLEGE_NAME': 'the college',
        'CAMPAIGN_DB_PATH': '/workspace/runtime/deployment/campaigns.sqlite3',
        'ARI_ENABLED': 'true', 'ARI_URL': 'http://pbx:8088/ari', 'ARI_USER': 'college',
        'ARI_PASSWORD': secret['ari_password'], 'ARI_APP': 'parent-marks',
        'SIP_TRUNK': 'zoiper1', 'OUTBOUND_CHANNEL_DRIVER': 'pjsip', 'SIP_DIAL_FORMAT': 'e164_digits',
        'STT_ENGINE': 'meta_omnilingual', 'META_STT_URL': 'http://stt:8110',
        'TTS_ENGINE': 'voiceapi_vits', 'VITS_URL': 'http://vits:8112',
        'OLLAMA_URL': 'http://ollama:11434', 'OLLAMA_MODEL': MODEL,
        'OLLAMA_ENABLED': 'true', 'LLM_GENERATE_REPLIES': 'true', 'OLLAMA_THINK': 'false',
        'OLLAMA_TIMEOUT_SECONDS': '45', 'OLLAMA_NUM_CTX': '4096', 'OLLAMA_NUM_PREDICT': '96',
        'LLM_UNDERSTAND_EVERY_INPUT': 'true',
        'REQUIRE_PREPARED_CALL_AUDIO': 'true', 'PREPARE_QUERY_AUDIO': 'true',
        'ASK_LANGUAGE_BEFORE_IDENTITY': 'true', 'RECORD_MAX_SECONDS': '12', 'RECORD_SILENCE_SECONDS': '1',
        'STREAMING_STT_ENABLED': 'true', 'STREAMING_AUDIO_ADVERTISE_HOST': 'caller',
        'STREAMING_AUDIO_BIND_HOST': '0.0.0.0', 'STREAMING_ENDPOINT_SECONDS': '1.0',
        'STREAMING_PARTIAL_MAX_REQUESTS': '1', 'STREAMING_PREFETCH_LLM': 'false',
        'ASTERISK_SOUNDS_DIR': '/var/lib/asterisk/sounds/parent-marks'}
    override = STATE / 'caller-overrides.json'
    if override.exists():
        values = json.loads(override.read_text())
        if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
            raise ValueError('caller-overrides.json must contain string environment values')
        caller_env.update(values)
    caller = speech(['python', '-m', 'app.main'], 8113, caller_env)
    # Caller does CPU resampling/VAD; allocating GPU to it wastes resources.
    caller['build']['target'] = 'cpu'
    caller['image'] = 'college-parent-speech:cpu'
    if caller_env.get('OUTBOUND_CHANNEL_DRIVER') == 'mobile':
        # The native WSL phone PBX sends RTP through local published UDP ports.
        first = int(caller_env.get('STREAMING_AUDIO_PORT', '20100'))
        count = int(caller_env.get('STREAMING_AUDIO_PORTS', '4'))
        caller['ports'].extend(f'127.0.0.1:{port}:{port}/udp' for port in range(first, first+count))
    caller['volumes'].append(volume('runtime/deployment/sounds', '/var/lib/asterisk/sounds/parent-marks'))
    caller['depends_on'] = ['pbx', 'stt', 'vits', 'ollama']
    pbx = {'build': {'context': str(Path(host_root) / 'asterisk')},
        'ports': [f'{"127.0.0.1" if address == "127.0.0.1" else "0.0.0.0"}:15070:5060/udp',
                  '127.0.0.1:8188:8088',
                  f'{"127.0.0.1" if address == "127.0.0.1" else "0.0.0.0"}:10100-10120:10100-10120/udp'],
        'volumes': [volume('runtime/deployment/asterisk/' + name, '/etc/asterisk/' + name, True)
                    for name in ['http.conf', 'ari.conf', 'pjsip.conf', 'extensions.conf', 'rtp.conf', 'provider.conf']]
                  + [volume('runtime/deployment/sounds', '/usr/share/asterisk/sounds/parent-marks')],
        'restart': 'unless-stopped'}
    compose = {'name': 'college-parent-portable', 'services': {
        'pbx': pbx, 'stt': stt, 'vits': vits, 'ollama': ollama, 'caller': caller, 'voice-export': voice_export}}
    (STATE / 'compose.json').write_text(json.dumps(compose, indent=2))
    (STATE / 'profile.json').write_text(json.dumps({'profile': args.profile, 'sip_address': address}))
    print('Configured runtime/deployment/compose.json; credentials generated locally and retained on rerun.')


def verify(args):
    """Smoke checks use synthetic speech and typed sessions, never /calls/originate."""
    credentials = json.loads((STATE / 'credentials.json').read_text())
    headers = {'X-Admin-Token': credentials['admin_token']}
    internal = args.internal
    stt = 'http://stt:8110' if internal else 'http://127.0.0.1:8110'
    tts = 'http://vits:8112' if internal else 'http://127.0.0.1:8112'
    caller = 'http://caller:8113' if internal else 'http://127.0.0.1:8113'
    llm = 'http://ollama:11434' if internal else 'http://127.0.0.1:11444'
    stt = args.stt_url or stt
    llm = args.llm_url or llm
    pbx = 'http://pbx:8088/ari' if internal else 'http://127.0.0.1:8188/ari'
    report = {'profile': args.profile, 'scope': 'No-call smoke test; synthetic ASR is not human accuracy',
              'checks': [], 'speech': []}
    def wait_health(name, address, predicate, auth=None):
        last = None
        for _ in range(60):
            try:
                last = request_json(address, headers=auth)
                if predicate(last):
                    report['checks'].append({'name': name, 'ok': True})
                    return last
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError(name + ' is not ready; inspect Docker logs')
    try:
        wait_health('STT', stt + '/health', lambda item: item.get('ready'))
        wait_health('VITS assets', tts + '/health', lambda item: set(item.get('languages', [])) == {'en', 'hi', 'kn'})
        wait_health('Ollama model', llm + '/api/tags', lambda item: any(x['name'] == MODEL for x in item.get('models', [])))
        wait_health('Caller + CSV', caller + '/health', lambda item: item.get('csv') and item.get('ok'))
        basic = base64.b64encode(('college:' + credentials['ari_password']).encode()).decode()
        wait_health('ARI authentication', pbx + '/asterisk/info', lambda item: 'system' in item,
            {'Authorization': 'Basic ' + basic})
        # Admin authentication must not accidentally become optional.
        try:
            request_json(caller + '/sessions', {'student_id': 'demo-001'})
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
            report['checks'].append({'name': 'Unauthorized session rejected', 'ok': True})
        else:
            raise RuntimeError('Admin authentication was not enforced')
        texts = {'en': 'The recorded total is 342 out of 400.',
                 'hi': 'कुल अंक 400 में से 342 हैं।',
                 'kn': 'ಒಟ್ಟು ಅಂಕಗಳು 400 ರಲ್ಲಿ 342.'}
        (STATE / 'checks').mkdir(exist_ok=True)
        for language, text in texts.items():
            started = time.monotonic()
            req = urllib.request.Request(tts + '/synthesize', data=json.dumps({'text': text, 'language': language}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=180) as response:
                wav_bytes = response.read()
            with wave.open(io.BytesIO(wav_bytes)) as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getnframes() < 2205:
                    raise RuntimeError('Invalid synthesized WAV')
                seconds = wav.getnframes() / wav.getframerate()
            native = STATE / 'checks' / (language + '-smoke.wav')
            native.write_bytes(wav_bytes)
            # Test the actual phone-bandwidth boundary, not VITS's native 22050 Hz.
            from app.tts import write_ulaw_or_pcm8k
            phone_audio = STATE / 'checks' / (language + '-smoke-8k.wav')
            write_ulaw_or_pcm8k(native, phone_audio)
            speech_seconds = time.monotonic() - started
            req = urllib.request.Request(stt + '/transcribe?language=' + language, data=phone_audio.read_bytes(),
                headers={'Content-Type': 'audio/wav'})
            with urllib.request.urlopen(req, timeout=180) as response:
                decoded = json.load(response)
            if not decoded.get('text', '').strip():
                raise RuntimeError(language + ': ASR returned an empty transcript')
            generated = request_json(llm + '/api/chat', {
                'model': MODEL, 'stream': False, 'think': False,
                'messages': [{'role': 'user', 'content': 'Repeat exactly this sentence, without adding anything: ' + text}],
                'options': {'num_ctx': 2048, 'num_predict': 96, 'temperature': 0}}, timeout=180)
            output = generated.get('message', {}).get('content', '').strip()
            if not output:
                raise RuntimeError(language + ': LLM generated an empty reply')
            language_ok = (any('\u0900' <= c <= '\u097f' for c in output) if language == 'hi' else
                          any('\u0c80' <= c <= '\u0cff' for c in output) if language == 'kn' else
                          not any('\u0900' <= c <= '\u0cff' for c in output))
            if not language_ok or any(number not in output for number in ('342', '400')):
                raise RuntimeError(language + ': LLM smoke reply lost required script or facts')
            health = request_json(tts + '/health')
            if args.profile == 'gpu' and not health['decoder']['cuda_verified']:
                raise RuntimeError(language + ': VITS CUDA kernels were not verified')
            report['speech'].append({'language': language, 'input': text, 'transcript': decoded['text'],
                'llm_output': output, 'tts_seconds': round(speech_seconds, 3), 'audio_seconds': round(seconds, 3),
                'stt_timings': decoded.get('timings'), 'tts_health': health})
        # Avoid exercising or reporting private student records after demo CSV replacement.
        with (STATE / 'students.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        demo = len(rows) == 1 and rows[0].get('student_id') == 'demo-001' and rows[0].get('parent_phone') == '+19995550123'
        # Exercise the caller's language -> affirmation -> marks path, not just raw LLM.
        dialogues = [
            ('en', 'English', 'Yes I am', 'What are the total marks?'),
            ('hi', 'Hindi', 'हाँ', 'कुल अंक कितने हैं?'),
            ('kn', 'Kannada', 'ಹೌದು', 'ಒಟ್ಟು ಅಂಕಗಳು ಎಷ್ಟು?')]
        if not demo:
            report['checks'].append({'name': 'Demo caller dialogue', 'skipped': True,
                                    'reason': 'Student CSV has been replaced; private records are not smoke-test inputs'})
        for language, choice, affirmative, question in dialogues if demo else []:
            session = request_json(caller + '/sessions', {'student_id': 'demo-001'}, headers)
            path = caller + '/sessions/' + session['session_id'] + '/turn'
            for text in (choice, affirmative, question):
                turn = request_json(path, {'text': text}, headers, timeout=180)
            if not turn['verified'] or '342' not in turn['reply'] or '400' not in turn['reply']:
                raise RuntimeError(language + ': grounded caller dialogue failed')
            report['checks'].append({'name': language + ' grounded caller dialogue', 'ok': True, 'reply': turn['reply']})
        if args.profile == 'gpu':
            if not request_json(stt + '/health').get('cuda_verified'):
                raise RuntimeError('STT CUDA execution unverified')
            loaded = request_json(llm + '/api/ps')['models']
            if not any(item['name'] == MODEL and item.get('size_vram', 0) > 0 for item in loaded):
                raise RuntimeError('Ollama model has no GPU allocation')
            report['checks'].append({'name': 'CUDA STT/TTS kernels + Ollama VRAM allocation', 'ok': True})
        report['ok'] = True
    except Exception as error:
        report['ok'] = False
        report['error'] = str(error)
        if isinstance(error, urllib.error.HTTPError):
            report['error_detail'] = error.read(1600).decode('utf-8', errors='replace')
    (STATE / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'download', 'configure', 'verify'])
    parser.add_argument('--profile', choices=['cpu', 'gpu'], default='cpu')
    parser.add_argument('--online', action='store_true')
    parser.add_argument('--hashes', action='store_true')
    parser.add_argument('--host-root')
    parser.add_argument('--sip-address', default='127.0.0.1')
    parser.add_argument('--internal', action='store_true')
    parser.add_argument('--stt-url', help='Advanced smoke test: reuse an existing ASR service')
    parser.add_argument('--llm-url', help='Advanced smoke test: reuse an existing Ollama service')
    args = parser.parse_args()
    return globals()[args.action](args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
