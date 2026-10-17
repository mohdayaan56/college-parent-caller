"""Resident Meta ASR service. Publish its port only on host loopback."""
from contextlib import asynccontextmanager
from io import BytesIO
import os
import threading
from time import perf_counter

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

LANGUAGES = {'en': 'eng_Latn', 'hi': 'hin_Deva', 'kn': 'kan_Knda'}
MAX_BYTES = 2 * 1024**2
MAX_SECONDS = 39  # the upstream model supports segments shorter than 40 seconds
pipeline = None
model_card = ''
backend = 'fairseq2'
inference_lock = threading.Lock()
trim_edges = False


@asynccontextmanager
async def lifespan(_app):
    global pipeline, model_card, backend, trim_edges
    backend = os.environ.get('META_ASR_BACKEND', 'fairseq2')
    if backend == 'indic_hybrid':
        from scripts.indic_recognizer import IndicHybridRecognizer
        pipeline = IndicHybridRecognizer(os.environ['META_ASR_ONNX_DIR'],os.environ['INDIC_ASR_DIR'],
            device=os.environ.get('META_ASR_DEVICE','cuda'),threads=int(os.environ.get('META_ASR_THREADS','4')),
            english_backend=os.environ.get('ENGLISH_ASR_BACKEND','meta'),
            whisper_directory=os.environ.get('WHISPER_MODEL_DIR'))
        model_card = ('whisper-turbo-en' if os.environ.get('ENGLISH_ASR_BACKEND') == 'whisper'
                      else 'meta-ctc-300m-en') + '+indicconformer-120m-hi-kn'
        pipeline.transcribe_waveform(np.zeros(8000,dtype=np.float32),8000,language='en')
        for language in ('hi','kn'):
            pipeline.transcribe_waveform(np.zeros(8000,dtype=np.float32),8000,language=language)
        yield
        pipeline = None
        return
    if backend == 'faster_whisper':
        from scripts.whisper_recognizer import WhisperRecognizer
        pipeline = WhisperRecognizer(os.environ['WHISPER_MODEL_DIR'],
            device=os.environ.get('WHISPER_DEVICE','cuda'),
            compute_type=os.environ.get('WHISPER_COMPUTE_TYPE','int8_float16'))
        model_card = 'whisper-large-v3-turbo'
        pipeline.transcribe_waveform(np.zeros(16000,dtype=np.float32),16000,language='en')
        yield
        pipeline = None
        return
    if backend == 'onnx':
        from scripts.meta_onnx import OnnxRecognizer
        precision = os.environ.get('META_ASR_PRECISION', 'fp32')
        pipeline = OnnxRecognizer(os.environ.get('META_ASR_ONNX_DIR', '/work/models/meta-onnx'),
                                  threads=int(os.environ.get('META_ASR_THREADS', '4')), precision=precision,
                                  device=os.environ.get('META_ASR_DEVICE', 'cpu'))
        model_card = 'college_omniASR_CTC_300M_v2_onnx_' + precision
        trim_edges = os.environ.get('META_ASR_TRIM_SILENCE', '0') == '1'
        if os.environ.get('META_ASR_WARMUP', '1') == '1':
            import torch
            torch.set_num_threads(int(os.environ.get('META_ASR_THREADS', '4')))
            # Import the phone resampler and initialize kernels before readiness.
            # Synthetic silence has no student information and places no call.
            pipeline.transcribe_waveform(np.zeros(8000, dtype=np.float32), 8000, trim_silence=trim_edges)
        yield
        pipeline = None
        return
    if backend != 'fairseq2':
        raise ValueError('Unsupported META_ASR_BACKEND')
    import torch
    from omnilingual_asr.models.inference.pipeline import ASRInferencePipeline, load_model, load_tokenizer

    model = os.environ.get('META_ASR_MODEL', 'omniASR_CTC_300M_v2')
    if model not in {'omniASR_CTC_300M_v2', 'omniASR_LLM_300M_v2'}:
        raise ValueError('Unsupported META_ASR_MODEL')
    dtype_name = os.environ.get('META_ASR_DTYPE', 'float32')
    if dtype_name not in {'float32', 'bfloat16'}:
        raise ValueError('Unsupported META_ASR_DTYPE')
    dtype = getattr(torch, dtype_name)
    torch.set_num_threads(int(os.environ.get('META_ASR_THREADS', '4')))
    model_card = 'college_' + model
    loaded = load_model(model_card, device=torch.device('cpu'), dtype=dtype, mmap=True)
    pipeline = ASRInferencePipeline(model_card=None, model=loaded,
        tokenizer=load_tokenizer(model_card), device='cpu', dtype=dtype)
    yield
    pipeline = None


app = FastAPI(lifespan=lifespan)


def validate_audio(body):
    try:
        info = sf.info(BytesIO(body))
        if info.format != 'WAV' or info.channels not in {1, 2} or info.frames <= 0:
            raise ValueError('Expected nonempty mono or stereo WAV')
        if info.duration > MAX_SECONDS or info.samplerate not in {8000, 16000, 24000, 44100, 48000}:
            raise ValueError('Unsupported duration or sample rate')
        waveform, sample_rate = sf.read(BytesIO(body), dtype='float32')
        if not np.isfinite(waveform).all():
            raise ValueError('Audio contains nonfinite samples')
    except (ValueError, RuntimeError, sf.LibsndfileError) as error:
        raise HTTPException(status_code=422, detail='Invalid WAV audio') from error
    return waveform, sample_rate


def infer(body, language, decoded=None, constrain_script=False, trim_silence=None, language_menu=False):
    if pipeline is None:
        raise HTTPException(status_code=503, detail='ASR not ready')
    if not inference_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail='ASR busy; retry this turn')
    try:
        if backend in {'onnx', 'faster_whisper', 'indic_hybrid'}:
            waveform, sample_rate = decoded or validate_audio(body)
            if backend == 'indic_hybrid':
                menu_options = {'language_menu': True} if language_menu else {}
                return pipeline.transcribe_waveform(waveform,sample_rate,language=language,
                    constrain_script=constrain_script, **menu_options)
            if backend == 'faster_whisper':
                return pipeline.transcribe_waveform(waveform,sample_rate,language=language)
            trim = trim_edges if trim_silence is None else trim_silence
            if constrain_script or trim or language_menu:
                menu_options = {'language_menu': True} if language_menu else {}
                return pipeline.transcribe_waveform(waveform, sample_rate,
                    language=language, constrain_script=constrain_script, trim_silence=trim, **menu_options)
            return pipeline.transcribe_waveform(waveform, sample_rate)
        hint = [LANGUAGES[language]] if 'LLM' in model_card else None
        start = perf_counter()
        text = pipeline.transcribe([body], lang=hint, batch_size=1)[0]
        return text, {'inference_seconds': perf_counter()-start}
    finally:
        inference_lock.release()


@app.get('/health')
def health():
    return {'ready': pipeline is not None, 'model': model_card, 'backend': backend, 'trim_silence': trim_edges,
            'device': getattr(pipeline, 'device', 'cpu'),
            'cuda_verified': getattr(pipeline, 'cuda_verified', False)}


@app.post('/transcribe')
async def transcribe(request: Request, language: str = 'en', constrain_script: bool = False, trim_silence: bool | None = None, language_menu: bool = False):
    if language not in LANGUAGES:
        raise HTTPException(status_code=422, detail='Unsupported language')
    if language_menu and (language != 'en' or backend not in {'onnx','indic_hybrid'}):
        raise HTTPException(status_code=422, detail='Language menu requires a multilingual menu backend')
    if (constrain_script or trim_silence) and backend not in {'onnx','faster_whisper','indic_hybrid'}:
        raise HTTPException(status_code=422, detail='Experimental decoding requires ONNX backend')
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BYTES:
            raise HTTPException(status_code=413, detail='Audio exceeds 2 MiB')
        chunks.append(chunk)
    body = b''.join(chunks)
    decoded = validate_audio(body)
    text, timings = await run_in_threadpool(infer, body, language, decoded, constrain_script, trim_silence, language_menu)
    return {'language': language, 'text': text, 'model': model_card, 'timings': timings}
