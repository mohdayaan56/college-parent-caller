"""Local language-conditioned ASR alternative; no model download at runtime."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
import numpy as np


class WhisperRecognizer:
    def __init__(self, directory, device='cuda', compute_type='int8_float16'):
        import torch  # Preload the image's CUDA/cuDNN shared libraries.
        from faster_whisper import WhisperModel
        directory = Path(directory)
        for entry in json.loads((directory/'verified-manifest.json').read_text()):
            path = directory / entry['file']
            if not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError('Invalid ASR manifest path')
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            if checksum != entry['sha256'] or path.stat().st_size != entry['bytes']:
                raise ValueError(f'Invalid local ASR asset: {entry["file"]}')
        self.model = WhisperModel(str(directory), device=device, compute_type=compute_type,
                                  cpu_threads=4, num_workers=1, local_files_only=True)
        self.device = self.model.model.device
        self.cuda_verified = False

    def transcribe_waveform(self, waveform, sample_rate, *, language='en', **_options):
        if language not in {'en','hi','kn'}:
            raise ValueError('Unsupported ASR language')
        started = perf_counter()
        audio = np.asarray(waveform, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
            raise ValueError('Invalid audio')
        if sample_rate != 16000:
            from math import gcd
            from scipy.signal import resample_poly
            divisor = gcd(sample_rate,16000)
            audio = resample_poly(audio,16000//divisor,sample_rate//divisor).astype(np.float32)
        prepared = perf_counter()
        segments, _ = self.model.transcribe(audio, language=language, task='transcribe',
            beam_size=3, temperature=0, vad_filter=False, condition_on_previous_text=False,
            max_new_tokens=128)
        text = ' '.join(segment.text.strip() for segment in segments).strip()
        self.cuda_verified = self.device == 'cuda'
        return text, {'preprocess_seconds':prepared-started,
                      'inference_seconds':perf_counter()-prepared, 'explicit_language':language}
