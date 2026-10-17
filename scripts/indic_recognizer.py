"""AI4Bharat per-language CTC exports for Hindi/Kannada; Meta for English.

Export and preprocessing contract by Sulabh Katiyar (CC BY 4.0):
https://huggingface.co/sulabhkatiyar/indicconformer-120m-onnx
Original AI4Bharat models: https://github.com/AI4Bharat/IndicConformerASR
"""
import hashlib
import json
from pathlib import Path
from time import perf_counter
import numpy as np


def mel_features(audio):
    import torch
    import torchaudio
    waveform = torch.from_numpy(np.ascontiguousarray(audio,dtype=np.float32))
    waveform = torch.cat((waveform[:1],waveform[1:] - .97*waveform[:-1]))
    mel = torchaudio.transforms.MelSpectrogram(sample_rate=16000,n_fft=512,
        win_length=400,hop_length=160,n_mels=80,f_min=0,f_max=8000,
        norm='slaney',mel_scale='slaney',power=2.0)(waveform)
    logged = torch.log(mel + 2**-24)
    return ((logged-logged.mean(dim=1,keepdim=True)) /
        (logged.std(dim=1,keepdim=True,unbiased=True)+1e-5)).numpy()[None].astype(np.float32)


def decode_ctc(logits, vocab):
    ids = np.asarray(logits).argmax(axis=-1)[0]
    ids = ids[np.r_[True,ids[1:] != ids[:-1]]]
    return ''.join(vocab[int(i)] for i in ids if i < len(vocab)).replace('▁',' ').strip()


class IndicHybridRecognizer:
    def __init__(self, meta_directory, indic_directory, device='cuda', threads=4,
                 english_backend='meta', whisper_directory=None):
        if english_backend == 'whisper':
            from scripts.whisper_recognizer import WhisperRecognizer
            if whisper_directory is None:
                raise ValueError('Verified local Whisper model directory required')
            self.english = WhisperRecognizer(whisper_directory,device=device)
        elif english_backend == 'meta':
            from scripts.meta_onnx import OnnxRecognizer
            self.english = OnnxRecognizer(meta_directory,device=device,threads=threads)
        else:
            raise ValueError('Unsupported English recognition backend')
        self.directory = Path(indic_directory)
        for entry in json.loads((self.directory/'verified-manifest.json').read_text()):
            path = self.directory/entry['file']
            if not path.resolve().is_relative_to(self.directory.resolve()):
                raise ValueError('Invalid Indic ASR manifest path')
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream,'sha256').hexdigest()
            if checksum != entry['sha256'] or path.stat().st_size != entry['bytes']:
                raise ValueError('Indic ASR asset integrity check failed')
        self.device,self.threads = device,threads
        self.native_sessions = {}
        self.verified_languages = set()
        self.cuda_verified = False

    def transcribe_waveform(self, waveform, sample_rate, *, language='en', **_options):
        if language == 'en':
            menu_options = {'language_menu': True} if _options.get('language_menu') else {}
            result = self.english.transcribe_waveform(waveform,sample_rate,
                language='en',constrain_script=_options.get('constrain_script',False), **menu_options)
            self.cuda_verified = self.english.cuda_verified
            if menu_options:
                from app.language_choice import language_choices
                # Short language names can be poor matches for multilingual CTC.
                # Try our already-verified native models only when the menu has
                # no explicit choice; never resolve an ambiguous choice by order.
                if not language_choices(result[0]):
                    for candidate_language in ('hi','kn'):
                        candidate = self.transcribe_waveform(waveform,sample_rate,
                                                             language=candidate_language)
                        choices = language_choices(candidate[0])
                        if choices:
                            return candidate[0], {**candidate[1],
                                'menu_candidate_language':candidate_language}
            return result
        if language not in {'hi','kn'}:
            raise ValueError('Unsupported language')
        started = perf_counter()
        audio = np.asarray(waveform,dtype=np.float32)
        if audio.ndim == 2:
            audio = audio.mean(axis=1)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
            raise ValueError('Invalid audio')
        if sample_rate != 16000:
            import torch
            import torchaudio.functional as functional
            audio = functional.resample(torch.from_numpy(audio),sample_rate,16000).numpy()
        if audio.size < 512:
            audio = np.pad(audio,(0,512-audio.size))
        if language not in self.native_sessions:
            import onnxruntime as ort
            from scripts.inference_provider import configure_session
            # Only two native languages are supported. Retain both so changing
            # menu candidates does not reload a model during a live response.
            self.verified_languages.discard(language)
            options = ort.SessionOptions()
            options.intra_op_num_threads = self.threads
            options.inter_op_num_threads = 1
            providers = configure_session(ort,options,self.device)
            session = ort.InferenceSession(str(self.directory/language/'model.onnx'),
                sess_options=options,providers=providers)
            session.disable_fallback()
            vocab = json.loads((self.directory/language/'vocab.json').read_text())
            self.native_sessions[language] = (session,vocab)
        session,vocab = self.native_sessions[language]
        features = mel_features(audio)
        prepared = perf_counter()
        logits = session.run(None,{'audio_signal':features,
            'length':np.array([features.shape[-1]],dtype=np.int64)})[0]
        if self.device == 'cuda' and language not in self.verified_languages:
            from scripts.inference_provider import verify_cuda_execution
            verify_cuda_execution(session)
            self.verified_languages.add(language)
        self.cuda_verified = self.device == 'cuda' and language in self.verified_languages
        return decode_ctc(logits,vocab), {'preprocess_seconds':prepared-started,
            'inference_seconds':perf_counter()-prepared,'explicit_language':language,
            'model':'indicconformer-120m-'+language}
