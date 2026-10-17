"""Windows MicroSIP test: prerecorded parent speech through Stereo Mix input.

Uses a separate portable profile, actual SIP/RTP, native ASR and the real caller.
Restores the normal MicroSIP instance after both calls. No mobile numbers are dialed.
"""
import argparse
import asyncio
import base64
import configparser
import json
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4
import winsound

import httpx
import websockets

from scripts.windows_audio_capture import capture_playback, stereo_mix_input

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'runtime/deployment'
DIRECTORY = STATE / 'local-sip/audio-test'
NORMAL = STATE / 'demo-softphone/MicroSIP.exe'
TEST = DIRECTORY / 'softphone/MicroSIP.exe'


def launch(executable, *arguments, wait=True):
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    process = subprocess.Popen([str(executable), *arguments], cwd=executable.parent,
                               startupinfo=startup)
    if wait:
        process.wait(timeout=15)
    return process


class Observer:
    def __init__(self, socket):
        self.socket = socket
        self.events = []
        self.changed = asyncio.Condition()

    async def read(self):
        async for message in self.socket:
            event = json.loads(message)
            async with self.changed:
                self.events.append(event)
                self.changed.notify_all()

    async def playback(self, channel, event_type, media=None, offset=0, timeout=45):
        async def find():
            cursor = offset
            async with self.changed:
                while True:
                    for event in self.events[cursor:]:
                        if event.get('type') != event_type:
                            continue
                        playback = event.get('playback', {})
                        if playback.get('target_uri') == 'channel:' + channel:
                            if media is None or playback.get('media_uri') == media:
                                return playback
                    cursor = len(self.events)
                    await self.changed.wait()
        return await asyncio.wait_for(find(), timeout)


def prepare_profile(audible=False):
    TEST.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(NORMAL, TEST)
    config = configparser.ConfigParser(interpolation=None, strict=False)
    config.optionxform = str
    config.read(NORMAL.with_suffix('.ini'), encoding='utf-16')
    recordings = DIRECTORY / 'received-calls'
    recordings.mkdir(exist_ok=True)
    config['Settings'].update({
        'audioInputDevice': '"Stereo Mix (Realtek(R) Audio)"',
        'swLevelAdjustment': '1', 'volumeOutput': '100' if audible else '0', 'volumeInput': '100',
        'volumeRing': '0', 'EC': '0', 'VAD': '0', 'micAmplification': '0',
        'autoRecording': '1', 'recordingFormat': 'wav',
        'recordingPath': '"' + str(recordings) + '"',
        'autoAnswer': '0', 'AA': '0', 'autoHangUpTime': '0',
    })
    config['Account1']['label'] = 'Synthetic parent audio test'
    config['Account1']['displayName'] = 'Synthetic parent'
    with TEST.with_suffix('.ini').open('w', encoding='utf-16') as output:
        config.write(output, space_around_delimiters=False)
    TEST.with_suffix('.ini').chmod(0o600)


def save_evidence(result):
    session = result.get('session_id')
    if not session:
        return
    lines = (STATE / 'recordings/latency.jsonl').read_text(encoding='utf-8').splitlines()
    result['timings'] = [json.loads(line) for line in lines if session in line]
    logs = subprocess.run(['docker', 'logs', '--since', '15m',
                           'college-parent-portable-caller-1'], capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=20)
    # Keep only this call's playback identifiers; protocol/auth logs are excluded.
    result['playback_log'] = [line for line in (logs.stdout + logs.stderr).splitlines()
                              if line.startswith('[ARI PLAY]') and result['channel_id'] in line]


async def run_language(language, spec, observer, caller, microphone, audible=False):
    result = {'language': language, 'passed': False, 'steps': [],
              'scope': 'Synthetic parent WAV -> Windows Stereo Mix -> MicroSIP microphone -> SIP/RTP -> real ASR/caller/PBX'}
    offset = len(observer.events)
    recordings_before = set((DIRECTORY / 'received-calls').glob('*.wav'))
    if audible:
        # Hear the real college audio, but do not send it back as parent speech.
        microphone.SetMute(1, None)
    response = await caller.post('/calls/originate', json={'student_id': 'demo-001'})
    response.raise_for_status()
    call = response.json()
    channel = call['channel']['id']
    result.update(session_id=call['session_id'], channel_id=channel)
    try:
        await asyncio.sleep(1)
        await asyncio.to_thread(launch, TEST, '/answer')
        await observer.playback(channel, 'PlaybackStarted', offset=offset, timeout=20)
        if audible:
            await asyncio.sleep(1.5)
        await asyncio.to_thread(launch, TEST, '/dtmf:' + spec['digit'])
        await observer.playback(channel, 'PlaybackFinished',
                                spec['identity_prompt_media'], offset=offset)
        print(f'{language}: keypad {spec["digit"]} reached identity prompt.', flush=True)
        for step in spec['steps']:
            await asyncio.sleep(.4)
            offset = len(observer.events)
            if audible:
                microphone.SetMute(0, None)
            try:
                await asyncio.to_thread(winsound.PlaySound,
                                        str(DIRECTORY / step['input_wav']),
                                        winsound.SND_FILENAME | winsound.SND_NODEFAULT)
                if audible:
                    # Let the last played samples reach the input before gating.
                    await asyncio.sleep(.25)
            finally:
                if audible:
                    microphone.SetMute(1, None)
            await observer.playback(channel, 'PlaybackFinished', step['expected_media'],
                                    offset=offset, timeout=40)
            result['steps'].append({**step, 'passed': True,
                                    'verification': 'Real PBX played the exact prepared response audio for this stage'})
            print(f'{language}: {step["stage"]} passed.', flush=True)
        result['passed'] = True
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        print(f'{language}: stopped at stage {len(result["steps"])}: {type(error).__name__}', flush=True)
    finally:
        if audible:
            microphone.SetMute(1, None)
        await asyncio.to_thread(launch, TEST, '/hangupall')
        await asyncio.sleep(1)
        await asyncio.to_thread(save_evidence, result)
        result['call_recordings'] = [str(path.relative_to(ROOT))
                                     for path in sorted(set((DIRECTORY / 'received-calls').glob('*.wav'))
                                                        - recordings_before)]
    return result


async def main(languages, microphone, audible=False):
    fixtures = json.loads((DIRECTORY / 'fixtures.json').read_text(encoding='utf-8'))
    credentials = json.loads((STATE / 'credentials.json').read_text())
    channels = subprocess.check_output(['docker', 'exec', 'college-parent-portable-pbx-1',
                                       'asterisk', '-rx', 'core show channels count'], text=True)
    if '0 active channels' not in channels:
        raise RuntimeError('Finish the active call before the audio test')
    route = json.loads(subprocess.check_output([
        'docker', 'exec', 'college-parent-portable-caller-1', 'python', '-c',
        "import json; from app.config import settings; print(json.dumps({'driver':settings.outbound_channel_driver,'trunk':settings.sip_trunk}))"], text=True))
    if route != {'driver': 'pjsip', 'trunk': 'zoiper1'}:
        raise RuntimeError('The test requires the loopback-only local SIP route')
    report = {'student_name': fixtures['student_name'], 'parent_name': fixtures['parent_name'],
              'synthetic_speech': True, 'audible_both_parties': audible, 'calls': []}
    loopback = await asyncio.to_thread(capture_playback,
                                      DIRECTORY / fixtures['languages'][languages[0]]['steps'][0]['input_wav'],
                                      DIRECTORY / 'stereo-mix-check.wav')
    report['loopback_capture'] = loopback
    print('Stereo Mix preflight:', json.dumps(loopback), flush=True)
    if loopback['rms'] < 50:
        raise RuntimeError('Stereo Mix did not receive the played speech; no call placed')
    if loopback.get('clipped_fraction', 0) > .001:
        raise RuntimeError('Test audio is clipping; reduce the Stereo Mix test level before dialing')
    previous = DIRECTORY / 'result.json'
    if previous.exists():
        shutil.copy2(previous, DIRECTORY / 'result.before-latest-test.json')
    prepare_profile(audible)
    await asyncio.to_thread(launch, NORMAL, '/exit')
    await asyncio.sleep(2)
    test_process = launch(TEST, '/minimized', wait=False)
    try:
        await asyncio.sleep(3)
        auth = base64.b64encode(('college:' + credentials['ari_password']).encode()).decode()
        uri = ('ws://127.0.0.1:8188/ari/events?app=audio-test-' + uuid4().hex + '&subscribeAll=true')
        async with websockets.connect(uri, additional_headers={'Authorization': 'Basic ' + auth}) as socket:
            observer = Observer(socket)
            reader = asyncio.create_task(observer.read())
            try:
                async with httpx.AsyncClient(base_url='http://127.0.0.1:8113',
                                             headers={'X-Admin-Token': credentials['admin_token']},
                                             timeout=240, trust_env=False) as caller:
                    response = await caller.post('/calls/prepare', json={'student_id': 'demo-001'})
                    response.raise_for_status()
                    for language in languages:
                        result = await run_language(language, fixtures['languages'][language], observer,
                                                    caller, microphone, audible)
                        report['calls'].append(result)
                        (DIRECTORY / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                reader.cancel()
    finally:
        await asyncio.to_thread(launch, TEST, '/hangupall')
        await asyncio.to_thread(launch, TEST, '/exit')
        try:
            await asyncio.to_thread(test_process.wait, 10)
        except subprocess.TimeoutExpired:
            test_process.terminate()
        await asyncio.sleep(1)
        launch(NORMAL, '/minimized', wait=False)
        report['normal_softphone_restored'] = True
        (DIRECTORY / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not all(call['passed'] for call in report['calls']):
        raise RuntimeError('Audio test incomplete; inspect local-sip/audio-test/result.json')
    print('Audio tests completed successfully for ' + ', '.join(languages)
          + '; normal MicroSIP profile restored.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['en', 'hi', 'kn', 'both', 'all'], default='all')
    parser.add_argument('--audible', action='store_true',
                        help='Hear college and parent voices; gate microphone input between their turns')
    args = parser.parse_args()
    with stereo_mix_input() as microphone:
        languages = ['en', 'hi', 'kn'] if args.language == 'all' else ['hi', 'kn'] if args.language == 'both' else [args.language]
        asyncio.run(main(languages,
                        microphone, args.audible))
