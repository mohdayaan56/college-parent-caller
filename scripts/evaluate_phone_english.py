"""Evaluate the existing Whisper model on phone-band speech; never dial or train."""

import argparse
import json
from pathlib import Path
import wave

import numpy as np

STATE = Path('/workspace/runtime/deployment/bluetooth')
SAMPLES = [
    ('English','en','menu'), ('Hindi','en','menu'), ('Kannada','en','menu'),
    ('inglish','en','menu'), ('eenglish','en','menu'),
    ('Yes.','en','confirmation'), ('Yes, speaking.','en','confirmation'),
    ('No, I am not the parent.','en','negative'),
    ('What are the marks in mathematics?','en','question'),
    ('How much did my child score in total?','en','question'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    args = parser.parse_args()
    if args.prepare:
        from app.audio_cache import cached_speech
        records = [{'text':text,'language':language,'kind':kind,
                    'path':str(cached_speech(text,language))}
                   for text,language,kind in SAMPLES]
        (STATE/'english-asr-inputs.json').write_text(json.dumps(records,indent=2))
        print('Prepared general recognition samples; no call or model training')
        return
    from scripts.whisper_recognizer import WhisperRecognizer
    recognizer = WhisperRecognizer('/workspace/models/whisper-large-v3-turbo',device='cuda')
    results = []
    for record in json.loads((STATE/'english-asr-inputs.json').read_text()):
        with wave.open(record['path'],'rb') as audio:
            waveform = np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.float32)/32768
            rate = audio.getframerate()
        text, metrics = recognizer.transcribe_waveform(waveform,rate,language='en')
        result = {'input':record['text'],'kind':record['kind'],'transcript':text,**metrics}
        results.append(result)
        print(json.dumps(result,ensure_ascii=False),flush=True)
    (STATE/'english-whisper-evaluation.json').write_text(json.dumps({
        'scope':'Synthetic 8 kHz speech; not a human accent or live call evaluation',
        'device':recognizer.device,'cuda_verified':recognizer.cuda_verified,
        'results':results},indent=2))


if __name__ == '__main__':
    main()
