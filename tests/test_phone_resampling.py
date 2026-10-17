import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np
from app.tts import write_ulaw_or_pcm8k


class PhoneResamplingTests(unittest.TestCase):
    def test_preserves_speech_band_and_rejects_aliasing(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / 'native.wav', Path(directory) / 'phone.wav'
            def converted_rms(frequency):
                samples = np.sin(2 * np.pi * frequency * np.arange(22050) / 22050) * 0.5
                with wave.open(str(source), 'wb') as wav:
                    wav.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
                    wav.writeframes((samples * 32767).astype('<i2').tobytes())
                write_ulaw_or_pcm8k(source, target)
                with wave.open(str(target), 'rb') as wav:
                    self.assertEqual(wav.getframerate(), 8000)
                    self.assertEqual(wav.getnframes(), 8000)
                    converted = np.frombuffer(wav.readframes(8000), dtype='<i2') / 32768
                return float(np.sqrt(np.mean(converted[100:-100] ** 2)))
            speech = converted_rms(1000)
            alias = converted_rms(6000)
            self.assertGreater(speech, 0.3)
            self.assertLess(alias, speech * 0.01)


if __name__ == '__main__':
    unittest.main()
