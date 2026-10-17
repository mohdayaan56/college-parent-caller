"""Check actual synthesis duration at the selected phone pace; never dial."""
from io import BytesIO
import json
import os
import wave
import torch
from scripts.vits_service import load, synthesize, SpeechRequest


def main():
    durations = {}
    speaking_rate = float(os.environ.get('TTS_SPEAKING_RATE','0.95'))
    text = "Hello. What would you like to know about your child's marks?"
    for rate in (1.0,speaking_rate):
        load('en',rate)
        # VITS samples durations as well as voice noise. Compare the same
        # random sample, rather than mistaking that variation for pace.
        torch.manual_seed(12345)
        response = synthesize(SpeechRequest(text=text,language='en',speaking_rate=rate))
        with wave.open(BytesIO(response.body)) as audio:
            assert (audio.getframerate(),audio.getnchannels(),audio.getsampwidth()) == (22050,1,2)
            durations[str(rate)] = audio.getnframes()/audio.getframerate()
    ratio = durations[str(speaking_rate)]/durations['1.0']
    assert 1.02 < ratio < 1.15, f'Unexpected modest slowdown: {ratio}'
    print(json.dumps({'speaking_rate':speaking_rate,
                      'durations_seconds':durations,'duration_ratio':ratio,
                      'scope':'Real VITS synthesis; unchanged sample rate; no call'}))


if __name__ == '__main__':
    main()
