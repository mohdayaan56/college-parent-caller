from __future__ import annotations

import subprocess
import threading
import wave
from pathlib import Path

from app.config import settings


def _silent_wav(path: Path, seconds: float = 0.4, rate: int = 16000) -> Path:
    frames = int(seconds * rate)
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * frames)
    return path


def synthesize(text: str, outfile: str | Path, language: str = "en") -> Path:
    path = Path(outfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = (settings.tts_engine or "none").strip().lower()
    if engine == "voiceapi_vits":
        import httpx
        response = httpx.post(settings.vits_url.rstrip('/') + '/synthesize',
                             json={'text': text, 'language': language,
                                   'speaking_rate':settings.tts_speaking_rate},
                             timeout=settings.vits_timeout_seconds, trust_env=False)
        response.raise_for_status()
        with wave.open(__import__('io').BytesIO(response.content), 'rb') as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or not wav.getnframes():
                raise ValueError('VITS returned invalid PCM audio')
        path.write_bytes(response.content)
        return path
    if engine == "local":
        if language == "kn":
            return _mms_kannada(text, path)
        if language in {"en", "hi"}:
            model = settings.piper_model if language == "en" else settings.piper_hindi_model
            return _piper(text, path, model)
        raise ValueError(f"Unsupported speech language: {language}")
    if engine == "piper":
        if language != "en":
            raise RuntimeError("The bundled Piper voice is English only; configure indic_parler for Hindi/Kannada")
        return _piper(text, path)
    if engine == "indic_parler":
        return _indic_parler(text, path, language)
    if engine == "kokoro":
        return _kokoro(text, path)
    _silent_wav(path)
    return path


_parler = None
_parler_tokenizer = None
_parler_description_tokenizer = None
def _indic_parler(text: str, path: Path, language: str) -> Path:
    global _parler, _parler_tokenizer, _parler_description_tokenizer
    model_path = settings.indic_parler_model
    if not model_path.is_dir():
        raise FileNotFoundError(f"Local Indic Parler TTS model not found: {model_path}")
    import torch
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer

    if _parler is None:
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        _parler = ParlerTTSForConditionalGeneration.from_pretrained(
            str(model_path), local_files_only=True
        ).to(device).eval()
        _parler_tokenizer = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        _parler_description_tokenizer = AutoTokenizer.from_pretrained(
            str(settings.indic_parler_description_tokenizer), local_files_only=True
        )
    speaker = {"en": "Mary", "hi": "Divya", "kn": "Anu"}.get(language, "Mary")
    description = f"{speaker}'s voice is clear, calm and moderate in speed. The recording has very clear audio."
    device = next(_parler.parameters()).device
    desc = _parler_description_tokenizer(description, return_tensors="pt").to(device)
    prompt = _parler_tokenizer(text, return_tensors="pt").to(device)
    with torch.no_grad():
        generation = _parler.generate(
            input_ids=desc.input_ids, attention_mask=desc.attention_mask,
            prompt_input_ids=prompt.input_ids, prompt_attention_mask=prompt.attention_mask,
        )
    _write_pcm16(path, generation.cpu().numpy().squeeze(), _parler.config.sampling_rate)
    return path


def _piper(text: str, path: Path, model: Path | None = None) -> Path:
    model = Path(model or settings.piper_model)
    if not model.is_file():
        raise FileNotFoundError(
            f"Piper voice model not found: {model}. "
            "Download a Piper ONNX voice into models/ or set TTS_ENGINE=none."
        )
    cmd = [
        settings.piper_bin,
        "--model",
        str(model),
        "--output_file",
        str(path),
    ]
    result = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(
            "Piper TTS failed: "
            + (result.stderr.decode("utf-8", errors="replace") or "unknown error")
        )
    return path


_mms_model = None
_mms_tokenizer = None
_mms_lock = threading.Lock()


def _mms_kannada(text: str, path: Path) -> Path:
    """Run the public MMS Kannada VITS model without a network dependency."""
    global _mms_model, _mms_tokenizer
    model_dir = settings.mms_kannada_model
    if not (model_dir / "model.safetensors").is_file():
        raise FileNotFoundError(f"Local Kannada TTS model not found: {model_dir}")
    import torch
    from transformers import AutoTokenizer, VitsModel

    with _mms_lock:
        if _mms_model is None:
            torch.set_num_threads(2)
            _mms_tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
            _mms_model = VitsModel.from_pretrained(str(model_dir), local_files_only=True).eval()
        encoded = _mms_tokenizer(text, return_tensors="pt")
        with torch.inference_mode():
            output = _mms_model(**encoded)
        _write_pcm16(path, output.waveform.squeeze().cpu().numpy(), _mms_model.config.sampling_rate)
    return path


def _kokoro(text: str, path: Path) -> Path:
    try:
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise RuntimeError(
            "kokoro-onnx is not installed. pip install kokoro-onnx "
            "or set TTS_ENGINE=piper."
        ) from exc

    model = Path("models/kokoro-v1.0.onnx")
    voices = Path("models/voices-v1.0.bin")
    if not model.is_file() or not voices.is_file():
        raise FileNotFoundError(
            "Kokoro model files missing under models/. "
            "See README for download links."
        )
    tts = Kokoro(str(model), str(voices))
    samples, sample_rate = tts.create(text, voice=settings.kokoro_voice)
    _write_pcm16(path, samples, sample_rate)
    return path


def _write_pcm16(path: Path, samples, sample_rate: int) -> None:
    import numpy as np

    audio = np.asarray(samples)
    if audio.dtype != np.int16:
        audio = np.clip(audio, -1.0, 1.0)
        audio = (audio * 32767).astype("<i2")
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(audio.tobytes())


def write_ulaw_or_pcm8k(src: Path, dest: Path, rate: int = 8000) -> Path:
    """Downsample to 8 kHz 16-bit mono for Asterisk Playback/STREAM FILE."""
    import numpy as np

    with wave.open(str(src), "rb") as wav:
        if wav.getcomptype() != "NONE" or wav.getsampwidth() != 2:
            raise ValueError("TTS output must be uncompressed 16-bit PCM WAV")
        src_rate = wav.getframerate()
        channels = wav.getnchannels()
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    data = data.reshape(-1, channels).astype("float32").mean(axis=1) / 32768.0
    if src_rate != rate and len(data) > 0:
        from math import gcd
        from scipy.signal import resample_poly
        divisor = gcd(src_rate, rate)
        data = resample_poly(data, rate // divisor, src_rate // divisor).astype("float32")
    pcm = (np.clip(data, -1.0, 1.0) * 32767).astype("<i2")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dest), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())
    return dest


def write_text_placeholder_wav(path: Path) -> Path:
    return _silent_wav(path)
