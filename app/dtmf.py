"""Conservative detection of sustained in-band keypad tones in 8 kHz PCM.

Cellular/Bluetooth calls may provide tones instead of an ARI DTMF event.
Both row and column frequencies must dominate for at least 80 milliseconds.
"""

import numpy as np


class ToneDigits:
    def __init__(self, callback):
        self.callback = callback
        time = np.arange(320) / 8000
        frequencies = np.array([697, 770, 852, 941, 1209, 1336, 1477, 1633])
        phase = 2 * np.pi * frequencies[:, None] * time
        self.cosine, self.sine = np.cos(phase), np.sin(phase)
        self.pending = bytearray()
        self.candidate, self.run, self.latched, self.quiet = None, 0, None, 0

    def reset(self):
        self.pending.clear()
        self.candidate, self.run, self.latched, self.quiet = None, 0, None, 0

    def classify(self, pcm):
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64) / 32768
        samples -= samples.mean()
        energy = np.dot(samples, samples)
        if energy / 320 < 0.000064:
            return None
        powers = ((self.cosine @ samples)**2 + (self.sine @ samples)**2) / (320 * energy)
        row, column = int(powers[:4].argmax()), int(powers[4:].argmax())
        low, high = powers[row], powers[column + 4]
        if (min(low, high) < .12 or low + high < .38
                or not .25 <= low / high <= 4
                or np.partition(powers[:4], -2)[-2] > .25 * low
                or np.partition(powers[4:], -2)[-2] > .25 * high):
            return None
        return ('123A', '456B', '789C', '*0#D')[row][column]

    def feed(self, pcm):
        self.pending.extend(pcm)
        while len(self.pending) >= 640:
            digit = self.classify(bytes(self.pending[:640]))
            del self.pending[:320]  # 40 ms window, 20 ms advance
            if digit is None:
                self.candidate, self.run = None, 0
                self.quiet += 1
                if self.quiet >= 2:
                    self.latched = None
                continue
            self.quiet = 0
            self.run = self.run + 1 if digit == self.candidate else 1
            self.candidate = digit
            if self.run >= 3 and digit != self.latched:
                self.latched = digit
                self.callback(digit)
