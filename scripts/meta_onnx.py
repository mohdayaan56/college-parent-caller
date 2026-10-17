"""Offline FP32 CPU inference for the format-converted Meta CTC checkpoint."""
import hashlib
import json
import unicodedata
from pathlib import Path
from time import perf_counter

import numpy as np


class OnnxRecognizer:
    def __init__(self, directory, threads=4, verify=True, precision='fp32', device='cpu'):
        import onnxruntime as ort
        import sentencepiece as spm
        directory = Path(directory)
        config = json.loads((directory / 'config.json').read_text(encoding='utf-8'))
        if config['source_model'] != 'omniASR_CTC_300M_v2':
            raise ValueError('Expected the selected CTC 300M v2 model')
        for name, metadata in config['files'].items():
            path = (directory/name).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError('Model manifest path escapes its directory')
            if path.stat().st_size != metadata['size']:
                raise ValueError(f'Incomplete model asset: {name}')
            if verify:
                with path.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != metadata['sha256']:
                    raise ValueError(f'Model checksum mismatch: {name}')
        model_path = directory/'model.onnx'
        if precision == 'int8':
            model_path = directory/'model-int8.onnx'
            metadata = json.loads((directory/'model-int8.json').read_text(encoding='utf-8'))
            if metadata['source_sha256'] != config['files']['model.onnx']['sha256']:
                raise ValueError('Quantized model was derived from a different source')
            with model_path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != metadata['sha256']:
                    raise ValueError('Quantized model checksum mismatch')
        elif precision != 'fp32':
            raise ValueError('Unsupported ONNX precision')
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        from scripts.inference_provider import configure_session
        providers = configure_session(ort, options, device)
        self.session = ort.InferenceSession(str(model_path), sess_options=options, providers=providers)
        self.session.disable_fallback()
        self.device = device
        self.cuda_verified = False
        self.tokenizer = spm.SentencePieceProcessor(model_file=str(directory/'tokenizer.model'))
        if self.tokenizer.get_piece_size() != config['vocabulary_size']:
            raise ValueError('Tokenizer vocabulary does not match checkpoint')
        self.threads = threads
        self.precision = precision
        self.script_masks = {}
        self.speech_edges = None

    def _script_mask(self, language):
        """Experimental restriction of letter tokens, preserving Latin code mixing.

        This changes decoding, not the acoustic model. Keep it opt-in until
        evaluated on the deployment's human speech, including language switches.
        """
        if language not in {'en', 'hi', 'kn', 'menu'}:
            raise ValueError('A known language is required for script restriction')
        if language not in self.script_masks:
            def allowed(piece):
                for char in piece:
                    if unicodedata.category(char)[0] not in {'L', 'M'}:
                        continue
                    if 'LATIN' in unicodedata.name(char, ''):
                        continue
                    if language in {'hi', 'menu'} and '\u0900' <= char <= '\u097f':
                        continue
                    if language in {'kn', 'menu'} and '\u0c80' <= char <= '\u0cff':
                        continue
                    return False
                return True
            mask = np.array([allowed(self.tokenizer.id_to_piece(i))
                             for i in range(self.tokenizer.get_piece_size())])
            mask[0] = True  # CTC blank must always remain available.
            self.script_masks[language] = mask
        return self.script_masks[language]

    def transcribe_waveform(self, waveform, sample_rate, *, language=None, constrain_script=False, trim_silence=False, language_menu=False):
        started = perf_counter()
        audio = np.asarray(waveform, dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
            raise ValueError('Invalid decoded audio')
        if sample_rate != 16000:
            # Preserve Meta's exact default Hann-window resampler. A different
            # filter changed borderline CTC tokens in the first comparison.
            import torch
            import torchaudio.functional as audio_functional
            audio = audio_functional.resample(torch.from_numpy(audio), sample_rate, 16000).numpy()
        edge_metrics = {}
        if trim_silence:
            if self.speech_edges is None:
                from scripts.speech_edges import SpeechEdges
                self.speech_edges = SpeechEdges()
            audio, edge_metrics = self.speech_edges.trim(audio)
        if audio.size < 400:
            audio = np.pad(audio, (0, 400-audio.size))
        audio = (audio-audio.mean()) / np.sqrt(audio.var()+np.float32(1e-5))
        audio = np.ascontiguousarray(audio[None, :], dtype=np.float32)
        preprocessed = perf_counter()
        logits = self.session.run(['logits'], {'audio': audio})[0]
        if getattr(self, 'device', 'cpu') == 'cuda' and not self.cuda_verified:
            from scripts.inference_provider import verify_cuda_execution
            verify_cuda_execution(self.session)
            self.cuda_verified = True
        inferred = perf_counter()
        if constrain_script or language_menu:
            logits[..., ~self._script_mask('menu' if language_menu else language)] = -np.inf
        ids = logits.argmax(axis=-1)[0]
        ids = ids[np.r_[True, ids[1:] != ids[:-1]]]
        ids = ids[ids != 0]  # upstream CTC blank/pad token
        text = self.tokenizer.decode(ids.tolist())
        return text, {'preprocess_seconds': preprocessed-started,
                      'inference_seconds': inferred-preprocessed,
                      'decode_seconds': perf_counter()-inferred, **edge_metrics}
