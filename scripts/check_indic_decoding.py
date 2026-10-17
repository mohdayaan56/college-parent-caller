"""Compare script decoding on synthetic phone audio; places no calls."""
import json
from pathlib import Path
import tempfile
import time
import argparse
import httpx
from app.tts import write_ulaw_or_pcm8k


def main(language_filter=None):
    results = []
    with tempfile.TemporaryDirectory() as directory, httpx.Client(trust_env=False, timeout=120) as client:
        for _ in range(90):
            try:
                health = client.get('http://stt:8110/health')
                if health.is_success and health.json().get('ready'):
                    print(json.dumps(health.json()),flush=True)
                    break
            except httpx.HTTPError:
                pass
            time.sleep(2)
        else:
            raise RuntimeError('ASR did not become ready')
        native = Path(directory) / 'native.wav'
        phone = Path(directory) / 'phone.wav'
        for language, utterances in (
            ('hi', ('हाँ', 'जी हाँ मैं ही हूँ', 'नहीं', 'कुल अंक कितने हैं?', 'प्रतिशत कितना है?')),
            ('kn', ('ಹೌದು', 'ಹೌದು ನಾನೇ', 'ಇಲ್ಲ', 'ಒಟ್ಟು ಅಂಕಗಳು ಎಷ್ಟು?', 'ಶೇಕಡಾವಾರು ಎಷ್ಟು?')),
        ):
            if language_filter and language != language_filter:
                continue
            for utterance in utterances:
                audio = client.post('http://vits:8112/synthesize', json={'text':utterance,'language':language})
                audio.raise_for_status()
                native.write_bytes(audio.content)
                write_ulaw_or_pcm8k(native, phone)
                row = {'language':language,'input':utterance,'model':health.json()['model']}
                for constrained in (False, True):
                    response = client.post('http://stt:8110/transcribe',
                        params={'language':language,'constrain_script':constrained},
                        content=phone.read_bytes(), headers={'Content-Type':'audio/wav'})
                    response.raise_for_status()
                    row['constrained' if constrained else 'unconstrained'] = response.json()['text']
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
    Path('/workspace/runtime/deployment/indic-decoding-check.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--language',choices=('hi','kn'))
    main(parser.parse_args().language)
