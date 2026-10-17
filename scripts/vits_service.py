"""Resident offline VITS service. Rejects unsupported characters rather than dropping facts."""
import importlib.util
import csv
import io
import json
import os
import re
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from scripts.vits_dynamic import FullLengthVits
from scripts.number_speech import DIGITS, normalize_numbers

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT/'models/voiceapi-vits'
THREADS = int(os.environ.get('VITS_THREADS', '4'))
BACKEND = os.environ.get('VITS_BACKEND', 'torch')
if BACKEND not in {'torch', 'onnx'}:
    raise ValueError('Unsupported VITS_BACKEND')
torch.set_num_threads(THREADS)
LOCK = threading.Lock()
CACHE = {}
PERCENT = {'en':'percent','hi':'प्रतिशत','kn':'ಶೇಕಡಾ'}
LETTER_NAMES = {
 'hi': 'ए|बी|सी|डी|ई|एफ|जी|एच|आई|जे|के|एल|एम|एन|ओ|पी|क्यू|आर|एस|टी|यू|वी|डब्ल्यू|एक्स|वाई|जेड'.split('|'),
 'kn': 'ಎ|ಬಿ|ಸಿ|ಡಿ|ಇ|ಎಫ್|ಜಿ|ಎಚ್|ಐ|ಜೆ|ಕೆ|ಎಲ್|ಎಂ|ಎನ್|ಒ|ಪಿ|ಕ್ಯೂ|ಆರ್|ಎಸ್|ಟಿ|ಯು|ವಿ|ಡಬ್ಲ್ಯೂ|ಎಕ್ಸ್|ವೈ|ಜೆಡ್'.split('|'),
}

def normalize(text, language):
    text = normalize_numbers(text, language)
    text = text.replace('%', ' '+PERCENT[language]).replace('’', "'").replace('—', ',').replace('–', ',')
    if language in {'hi','kn'}:
        text = re.sub(r'(?<![A-Za-z])the college(?![A-Za-z])',
                      'कॉलेज' if language == 'hi' else 'ಕಾಲೇಜು', text, flags=re.I)
        # Kannada attaches case suffixes directly: "collegeದಿಂದ". Unicode
        # word boundaries treat that suffix as part of the same word.
        text = re.sub(r'(?<![A-Za-z])college(?![A-Za-z])', 'कॉलेज' if language=='hi' else 'ಕಾಲೇಜು', text, flags=re.I)
        csv_path = Path(os.environ.get('CSV_PATH', str(ROOT/'data/students.csv')))
        if csv_path.is_file():
            with csv_path.open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):
                    for field in ['parent_name','student_name']:
                        original=row.get(field,'').strip()
                        spoken=row.get(field+'_'+language,'').strip()
                        if original and spoken:
                            text=re.sub(re.escape(original),lambda _:spoken,text,flags=re.I)
    mapping_path = ROOT/'data/vits-pronunciations.json'
    if mapping_path.is_file():
        mapping = json.loads(mapping_path.read_text(encoding='utf-8')).get(language,{})
        for original, spoken in sorted(mapping.items(),key=lambda item:-len(item[0])):
            text = re.sub(re.escape(original),lambda _:spoken,text,flags=re.I)
    if language in LETTER_NAMES:
        # Class sections and short acronyms contain Latin letters; pronounce
        # those codes rather than dropping them or rejecting prepared replies.
        text = re.sub(r'(?<![A-Za-z])[A-Z]{1,4}(?![A-Za-z])',
                      lambda m: ' '.join(LETTER_NAMES[language][ord(c)-65] for c in m[0]), text)
    return text

def load(language, speaking_rate=1.0):
    key = (language,speaking_rate)
    if key in CACHE:
        return CACHE[key]
    # Sequential laptop calls: bound model residency rather than retaining all voices.
    CACHE.clear()
    directory = DIRECTORY/(language+'_female')
    spec = importlib.util.spec_from_file_location('voiceapi_extra',directory/'extra.py')
    extra = importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=extra
    spec.loader.exec_module(extra)
    letters=(directory/'chars.txt').read_text(encoding='utf-8').strip('\n')
    config=extra.VitsConfig(text_cleaner='multilingual_cleaners',characters=extra.CharactersConfig(
        characters_class=extra.VitsCharacters,pad='<PAD>',eos='<EOS>',bos='<BOS>',blank='<BLNK>',
        characters=letters,punctuations="!¡'(),-.:;¿? ",phonemes=None))
    tokenizer,_=extra.TTSTokenizer.init_from_config(config)
    compact = directory/'frontend.pt'
    if BACKEND == 'onnx' and compact.is_file() and speaking_rate == 1.0:
        model = torch.jit.load(str(compact), map_location='cpu').eval()
    else:
        exported=torch.jit.load(str(directory/(language+'_female_vits_30hrs.pt')),map_location='cpu').eval()
        model=FullLengthVits(exported).eval()
        # Change predicted phoneme durations, preserving decoder pitch and
        # the WAV sample rate. Do not slow playback by relabelling samples.
        model.length_scale /= speaking_rate
    CACHE[key]=(model,tokenizer,extra,letters)
    return CACHE[key]

class SpeechRequest(BaseModel):
    text: str = Field(min_length=1,max_length=1500)
    language: str = 'en'
    speaking_rate: float = Field(default=1.0,ge=0.85,le=1.15)

app=FastAPI(title='Offline college VITS')

@app.get('/health')
def health():
    from scripts.vits_decoder import status
    return {'ok':True,'languages':[lang for lang in DIGITS if (DIRECTORY/(lang+'_female')/(lang+'_female_vits_30hrs.pt')).is_file()], 'loaded':[key[0] for key in CACHE], 'backend':BACKEND, 'threads':THREADS,
            'frontend_device':'cpu', 'decoder':status()}

@app.post('/synthesize')
def synthesize(request: SpeechRequest):
    if request.language not in DIGITS:
        raise HTTPException(422,'Unsupported language')
    start=time.perf_counter()
    with LOCK:
        model,tokenizer,extra,letters=load(request.language,request.speaking_rate)
        text=extra.multilingual_cleaners(normalize(request.text,request.language))
        missing=sorted(set(text)-set(letters+"!¡'(),-.:;¿? "))
        if missing:
            raise HTTPException(422,{'error':'Unsupported speech characters; supply native spellings in data/vits-pronunciations.json','characters':missing})
        inputs=torch.tensor(tokenizer.text_to_ids(text),dtype=torch.long).unsqueeze(0)
        if not inputs.numel():
            raise HTTPException(422,'No speakable text')
        with torch.inference_mode():
            frontend_start = time.perf_counter()
            latent = model.latent(inputs) if hasattr(model, 'latent') else model(inputs)
            frontend_seconds = time.perf_counter()-frontend_start
            decode_start = time.perf_counter()
            if BACKEND == 'onnx':
                from scripts.vits_decoder import decode
                audio = decode(latent, DIRECTORY/(request.language+'_female'), THREADS)
            else:
                audio=model.exported.waveform_decoder(latent).squeeze().cpu().numpy()
            decode_seconds = time.perf_counter()-decode_start
    if audio.ndim!=1 or not audio.size or not np.isfinite(audio).all():
        raise HTTPException(500,'Invalid generated audio')
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(22050)
        wav.writeframes((np.clip(audio,-1,1)*32767).astype('<i2').tobytes())
    return Response(buffer.getvalue(),media_type='audio/wav',headers={
        'X-Synthesis-Seconds':str(round(time.perf_counter()-start,3)),
        'X-Frontend-Seconds':str(round(frontend_seconds,3)),
        'X-Decoder-Seconds':str(round(decode_seconds,3)), 'X-Synthesis-Backend':BACKEND})
