from __future__ import annotations

from pathlib import Path
import subprocess
from uuid import uuid4

import numpy as np

from app.config import settings


_models = {}
GEMMA_NO_THINK_TEMPLATE = (
    "{% for message in messages %}<|turn>{{ message.role }}\n"
    "{{ message.content }}<turn|>\n{% endfor %}"
    "{% if add_generation_prompt %}<|turn>model\n{% endif %}"
)
LANGUAGE_NAMES = {"en": "English", "hi": "Hindi", "kn": "Kannada"}


def _reject_wrong_indic_script(transcript: str, language: str) -> str:
    """Reject a clear Hindi/Kannada script swap, while allowing code mixing."""
    if language not in {"hi", "kn"}:
        return transcript
    devanagari = sum("\u0900" <= char <= "\u097f" for char in transcript)
    kannada = sum("\u0c80" <= char <= "\u0cff" for char in transcript)
    if (language == "hi" and kannada and not devanagari) or (
        language == "kn" and devanagari and not kannada
    ):
        return ""
    return transcript


def _gemma_audio(path: str | Path, language: str) -> str:
    """Use a single local Gemma 4 audio checkpoint for all three languages."""
    from scipy.io import wavfile
    from scipy.signal import resample_poly

    for asset in (settings.gemma_runner, settings.gemma_model, settings.gemma_mmproj):
        if not asset.is_file():
            raise FileNotFoundError(f"Gemma audio asset not found: {asset}")
    sample_rate, samples = wavfile.read(str(path))
    if np.issubdtype(samples.dtype, np.integer):
        samples = samples.astype(np.float32) / np.iinfo(samples.dtype).max
    else:
        samples = samples.astype(np.float32)
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    if sample_rate != 16000:
        from math import gcd

        divisor = gcd(sample_rate, 16000)
        mono = resample_poly(mono, 16000 // divisor, sample_rate // divisor)
    prompt = (f"Transcribe the audio in {LANGUAGE_NAMES[language]}. "
              f"Return only the {LANGUAGE_NAMES[language]} transcript.")
    # The multimodal CLI currently reads audio from a WAV path.
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    audio16 = settings.recordings_dir / f"gemma-{uuid4().hex}.wav"
    try:
        pcm = (np.clip(mono, -1, 1) * 32767).astype("<i2")
        wavfile.write(str(audio16), 16000, pcm)
        command = [str(settings.gemma_runner), "-m", str(settings.gemma_model),
                   "--mmproj", str(settings.gemma_mmproj), "--audio", str(audio16),
                   "-p", prompt, "--jinja", "--chat-template", GEMMA_NO_THINK_TEMPLATE,
                   "--no-warmup", "-n", "256", "-c", "1024", "-t", "4", "--temp", "0"]
        completed = subprocess.run(command, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=settings.gemma_timeout_seconds)
    finally:
        audio16.unlink(missing_ok=True)
    if completed.returncode:
        raise RuntimeError(f"Gemma audio runner exited {completed.returncode}: "
                           f"{completed.stderr[-500:]}")
    transcript = completed.stdout.strip()
    if not transcript or "<|channel>" in transcript:
        raise RuntimeError("Gemma audio runner returned no usable transcript")
    return transcript


def _load_whisper(model_name: str):
    if model_name not in _models:
        from faster_whisper import WhisperModel

        _models[model_name] = WhisperModel(
            model_name,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return _models[model_name]


def _meta_omnilingual(path: str | Path, language: str, constrain_script=None, language_menu=False) -> str:
    """Send a recorded turn to the resident local Linux ASR service."""
    import httpx

    # Avoid sending parent audio through an HTTP proxy configured on the host.
    with httpx.Client(trust_env=False, timeout=settings.meta_stt_timeout_seconds) as client:
        menu_options = {'language_menu': True} if language_menu else {}
        response = client.post(settings.meta_stt_url.rstrip('/') + '/transcribe',
            params={'language': language, 'constrain_script': (language in {'en','hi','kn'}
                    if constrain_script is None else constrain_script), **menu_options},
            content=Path(path).read_bytes(),
            headers={'Content-Type': 'audio/wav'})
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or result.get('language') != language or not isinstance(result.get('text'), str):
        raise RuntimeError('Meta ASR returned an invalid response')
    return result['text'].strip()


def _indic_conformer(path: str | Path, language: str) -> str:
    if language not in {"hi", "kn"}:
        raise ValueError("IndicConformer is configured only for Hindi and Kannada calls")
    model_path = settings.indic_conformer_model
    if not model_path.is_dir():
        raise FileNotFoundError(f"Local IndicConformer model not found: {model_path}")
    import torch
    import torchaudio
    from transformers import AutoModel

    if "indic_conformer" not in _models:
        _models["indic_conformer"] = AutoModel.from_pretrained(
            str(model_path), trust_remote_code=True, local_files_only=True
        ).eval()
    audio, sample_rate = torchaudio.load(str(path))
    audio = audio.mean(dim=0, keepdim=True)
    if sample_rate != 16000:
        audio = torchaudio.functional.resample(audio, sample_rate, 16000)
    with torch.no_grad():
        return str(_models["indic_conformer"](audio, language, "ctc")).strip()


def transcribe_file(path: str | Path, language: str = "en", *, constrain_script=None, language_menu=False) -> str:
    if language not in {"en", "hi", "kn"}:
        raise ValueError(f"Unsupported speech language: {language}")
    if settings.stt_engine == "gemma_audio":
        return _reject_wrong_indic_script(_gemma_audio(path, language), language)
    if settings.stt_engine == "meta_omnilingual":
        return _reject_wrong_indic_script(_meta_omnilingual(path, language, constrain_script, language_menu), language)
    if language in {"hi", "kn"} and settings.stt_engine == "indic_conformer":
        return _reject_wrong_indic_script(_indic_conformer(path, language), language)
    model_name = settings.whisper_model if settings.stt_engine == "whisper_unified" else (
        settings.whisper_model if language == "en" else
        (settings.whisper_kannada_model or settings.whisper_multilingual_model) if language == "kn" else
        settings.whisper_multilingual_model
    )
    if not model_name or (language != "en" and model_name.endswith(".en")):
        raise RuntimeError(f"No multilingual STT model configured for {language}")
    model = _load_whisper(model_name)
    segments, _info = model.transcribe(
        str(path), beam_size=1, vad_filter=True,
        max_new_tokens=256, condition_on_previous_text=False,
        language=language if language in {"en", "hi", "kn"} else None,
    )
    transcript = " ".join(segment.text.strip() for segment in segments).strip()
    return _reject_wrong_indic_script(transcript, language)
