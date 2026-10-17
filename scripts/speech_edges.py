"""Optional conservative edge trimming. Keep pauses inside the utterance."""
import numpy as np


def speech_bounds(audio, sample_rate, timestamps, padding_seconds=0.4):
    if not timestamps:
        # Silence detection can miss a quiet affirmation; let ASR decide.
        return 0, len(audio)
    padding = int(padding_seconds * sample_rate)
    start = max(0, min(int(item['start']) for item in timestamps)-padding)
    end = min(len(audio), max(int(item['end']) for item in timestamps)+padding)
    if end <= start or len(audio)-(end-start) < int(0.5*sample_rate):
        return 0, len(audio)
    return start, end


class SpeechEdges:
    def __init__(self):
        from silero_vad import load_silero_vad, get_speech_timestamps
        self.model = load_silero_vad(onnx=True)
        self.timestamps = get_speech_timestamps

    def trim(self, audio):
        import torch
        # Called under the ASR worker's lock: Silero's recurrent state must not
        # be shared across simultaneous requests. Input is already 16 kHz.
        marks = self.timestamps(torch.from_numpy(np.ascontiguousarray(audio)), self.model,
            sampling_rate=16000, threshold=0.35, min_speech_duration_ms=100,
            min_silence_duration_ms=250, speech_pad_ms=0)
        start, end = speech_bounds(audio, 16000, marks)
        return audio[start:end], {'trimmed_seconds': (len(audio)-(end-start))/16000,
                                  'retained_seconds': (end-start)/16000}
