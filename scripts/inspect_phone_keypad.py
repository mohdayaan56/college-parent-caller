"""Summarize local menu audio tone evidence; never dial or transmit audio."""
import argparse
from collections import Counter
import json
from pathlib import Path
import wave
import numpy as np
from app.dtmf import ToneDigits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    args = parser.parse_args()
    with wave.open(str(args.path)) as audio:
        assert (audio.getframerate(),audio.getsampwidth(),audio.getnchannels()) == (8000,2,1)
        pcm = audio.readframes(audio.getnframes())
    digits = []
    detector = ToneDigits(digits.append)
    detector.feed(pcm)
    candidates = Counter(detector.classify(pcm[i:i+640]) for i in range(0,len(pcm)-639,320))
    samples = np.frombuffer(pcm,dtype='<i2').astype(float)/32768
    normalized = ToneDigits(lambda digit:None)
    quiet_candidates = Counter()
    for i in range(0,len(samples)-319,160):
        block = samples[i:i+320]
        rms = np.sqrt(np.mean(block*block))
        if rms > 0.00001:
            scaled = (np.clip(block*.03/rms,-1,1)*32767).astype('<i2').tobytes()
            quiet_candidates[normalized.classify(scaled)] += 1
    print(json.dumps({'seconds':len(samples)/8000,'rms':float(np.sqrt(np.mean(samples*samples))),
                      'peak':float(np.max(np.abs(samples))),
                      'accepted_digits':digits,'tone_windows':dict((str(k),v) for k,v in candidates.items()),
                      'gain_normalized_windows':dict((str(k),v) for k,v in quiet_candidates.items())}))


if __name__ == '__main__':
    main()
