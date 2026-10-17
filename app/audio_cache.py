"""Prepare speech before dialing; reuse only an exact text/language match."""
from copy import deepcopy
import csv
import asyncio
from contextlib import suppress
from functools import lru_cache
import hashlib
import json
import re
import threading
from time import monotonic
import wave
from pathlib import Path
from uuid import uuid4

from app.config import ROOT, settings
from app.conversation import CallSession, CallState, reply_for_intent, rewrite_intent_reply, with_followup
from app.language import phrase, menu_segments
from app.tts import synthesize, write_ulaw_or_pcm8k

_lock = threading.Lock()


@lru_cache(maxsize=16)
def _pronunciation_fingerprint(language, versions):
    native = []
    for name, _mtime, _size in versions:
        path = Path(name)
        if not path.is_file():
            continue
        if path.suffix in {".pt", ".onnx"}:
            native.append((name, _mtime, _size))
        elif path.suffix == ".csv":
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    for field in ("parent_name", "student_name"):
                        native.append((row.get(field, ""), row.get(f"{field}_{language}", "")))
        else:
            native.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(native, ensure_ascii=False).encode()).hexdigest()


def _speech_version(language):
    paths = [ROOT / "data/vits-pronunciations.json", ROOT / "scripts/vits_service.py",
             ROOT / "scripts/number_speech.py",
             ROOT / "scripts/vits_dynamic.py", ROOT / "scripts/vits_decoder.py", ROOT / "app/tts.py"]
    voice = ROOT / "models/voiceapi-vits" / f"{language}_female"
    paths.extend((voice / f"{language}_female_vits_30hrs.pt", voice / "chars.txt", voice / "extra.py"))
    paths.extend((voice / 'frontend.pt', voice / 'decoder.onnx'))
    if language in {"hi", "kn"}:
        paths.append(settings.csv_path)
    versions = []
    for path in paths:
        try:
            stat = path.stat()
            versions.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            versions.append((str(path), 0, 0))
    return _pronunciation_fingerprint(language, tuple(versions))


def cache_path(text: str, language: str) -> Path:
    version = ([_speech_version(lang) for lang in ('en','hi','kn')]
               if menu_segments(text,settings.college_name) else _speech_version(language))
    identity = ["full-vits-v3", settings.tts_engine, settings.vits_url,
                str(settings.piper_model), str(settings.piper_hindi_model),
                str(settings.mms_kannada_model), settings.tts_speaking_rate,
                version, language, text]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return settings.recordings_dir / "speech-cache" / f"{digest}.wav"


def valid_audio(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as wav:
            return (wav.getnchannels() == 1 and wav.getsampwidth() == 2
                    and wav.getframerate() == 8000 and wav.getnframes() > 0
                    and path.stat().st_size >= 44 + wav.getnframes() * 2)
    except (OSError, EOFError, wave.Error):
        return False


def cached_speech(text: str, language: str, timing: dict | None = None) -> Path:
    path = cache_path(text, language)
    started = monotonic()
    if valid_audio(path):
        if timing is not None:
            timing.update(tts_cache_hit=True, tts_seconds=0.0, audio_conversion_seconds=0.0,
                          audio_cache_lookup_seconds=round(monotonic() - started, 4))
        return path
    segments = menu_segments(text,settings.college_name)
    if segments:
        # Use each instruction's own voice before joining one phone WAV.
        parts = [cached_speech(part,lang) for part,lang in segments]
        with _lock:
            _assemble_chunks(text,language,parts)
        if timing is not None:
            timing.update(tts_cache_hit=False,tts_seconds=round(monotonic()-started,3),
                          audio_conversion_seconds=0.0)
        return path
    with _lock:
        if valid_audio(path):
            if timing is not None:
                timing.update(tts_cache_hit=True, tts_seconds=0.0, audio_conversion_seconds=0.0,
                              audio_cache_lookup_seconds=round(monotonic() - started, 4))
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.parent / f"tmp-{uuid4().hex}-native.wav"
        playable = path.parent / f"tmp-{uuid4().hex}-8k.wav"
        try:
            stage = monotonic()
            synthesize(text, original, language)
            tts_seconds = monotonic() - stage
            stage = monotonic()
            write_ulaw_or_pcm8k(original, playable)
            if not valid_audio(playable):
                raise ValueError("Speech preparation produced invalid phone audio")
            playable.chmod(0o666)
            playable.replace(path)
            if timing is not None:
                timing.update(tts_cache_hit=False, tts_seconds=round(tts_seconds, 3),
                              audio_conversion_seconds=round(monotonic() - stage, 3))
        finally:
            original.unlink(missing_ok=True)
            playable.unlink(missing_ok=True)
    return path


def call_audio_plan(session: CallSession, languages=None, include_queries=False, prepare_llm=False):
    """Simulate known turns on copies; preserve the real session and privacy gate."""
    opening = deepcopy(session)
    greeting = opening.greeting()
    plan = [(greeting.reply, opening.language)]
    if greeting.hangup or not opening.wards:
        return plan
    languages = tuple(languages or ("en", "hi", "kn")) if settings.ask_language_before_identity else (opening.language,)
    for language in languages:
        if language not in {"en", "hi", "kn"}:
            raise ValueError("Unsupported preparation language")
        clone = deepcopy(opening)
        clone.language = language
        confirmation = clone._confirmation_prompt()
        plan.append((confirmation.reply, language))
        plan.append((phrase(language, "confirm_retry", parent=clone._parent_label()), language))
        affirmative = {"en": "yes", "hi": "हाँ", "kn": "ಹೌದು"}[language]
        if not clone.confirmation_prompted:
            affirmative = clone._parent_label()
        verified = clone.handle_text(affirmative, use_llm=False)
        if verified.verified:
            plan.append((verified.reply, language))
            for student in clone.wards:
                plan.append((phrase(language,'ask_question',student=student.student_name),language))
        for key in ("language_retry", "deny", "data_unavailable", "clarify", "stt_fail", "opt_out", "understanding_fail",
                    "other_student", "out_of_scope"):
            text = phrase(language, key)
            if key in {'data_unavailable', 'other_student', 'out_of_scope'}:
                text = with_followup(text, language)
            plan.append((text, language))
        plan.append((phrase(language, 'bye', parent=clone.wards[0].parent_name), language))
        if include_queries and clone.state == CallState.VERIFIED:
            for student in clone.wards:
                plan.append((reply_for_intent(student, {'intent':'subject', 'subject':None}, language), language))
                for kind in ("total", "percentage", "performance", "repeat", "all_subjects", "lowest_subject", "class_info", "contact"):
                    plan.append((rewrite_intent_reply(student, {"intent": kind}, language,
                                                     cached_only=not prepare_llm), language))
                for subject in student.marks:
                    plan.append((rewrite_intent_reply(student, {"intent": "subject", "subject": subject}, language,
                                                     cached_only=not prepare_llm), language))
    return list(dict.fromkeys(plan))


def prepare_call_audio(session: CallSession, languages=None, include_queries=None, progress=None) -> dict:
    if include_queries is None:
        include_queries = settings.prepare_query_audio
    plan = call_audio_plan(session, languages, include_queries, prepare_llm=True)
    started = monotonic()
    hits = sum(valid_audio(cache_path(text, lang)) for text, lang in plan)
    for index, (text, language) in enumerate(plan, 1):
        if progress:
            progress(index, len(plan), language)
        cached_speech(text, language)
    return {"prepared": len(plan), "already_cached": hits,
            "languages": sorted({lang for _, lang in plan}),
            "seconds": round(monotonic() - started, 3)}


def call_audio_status(session: CallSession) -> dict:
    plan = call_audio_plan(session, include_queries=settings.prepare_query_audio)
    missing = sum(not valid_audio(cache_path(text, lang)) for text, lang in plan)
    return {"ready": missing == 0, "missing": missing, "required": len(plan)}


def speech_chunks(text: str, max_chars: int = 140) -> list[str]:
    """Split at sentence/clause/word boundaries without dropping any text."""
    sentences = re.split(r"(?<=[.!?।])\s+", text.strip())
    parts = []
    for sentence in sentences:
        clauses = re.split(r"(?<=,)\s+", sentence) if len(sentence) > max_chars else [sentence]
        for clause in clauses:
            chunk = ""
            for word in clause.split():
                if chunk and len(chunk) + len(word) + 1 > max_chars:
                    parts.append(chunk)
                    chunk = ""
                chunk = (chunk + " " + word).strip()
            if chunk:
                parts.append(chunk)
    return parts


def _assemble_chunks(text: str, language: str, parts: list[Path]) -> None:
    destination = cache_path(text, language)
    if valid_audio(destination):
        return
    temporary = destination.parent / f"tmp-{uuid4().hex}-joined.wav"
    try:
        with wave.open(str(temporary), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            for index, part in enumerate(parts):
                with wave.open(str(part), "rb") as source:
                    out.writeframes(source.readframes(source.getnframes()))
                if index < len(parts) - 1:
                    out.writeframes(b"\0\0" * 1200)  # 150 ms between clauses.
        temporary.chmod(0o666)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


async def reply_audio(text: str, language: str):
    """Yield first audio promptly; prefetch the next chunk while the PBX plays."""
    full = cache_path(text, language)
    if valid_audio(full) or menu_segments(text,settings.college_name):
        timing = {}
        path = await asyncio.to_thread(cached_speech, text, language, timing)
        yield path, {**timing, "audio_chunks": 1, "tts_streaming": False}
        return
    chunks = speech_chunks(text)
    if not chunks:
        raise ValueError("No speakable reply text")
    produced = []
    pending = None
    def start(chunk):
        metrics = {}
        task = asyncio.create_task(asyncio.to_thread(cached_speech, chunk, language, metrics))
        return task, metrics
    try:
        pending, metrics = start(chunks[0])
        for index in range(len(chunks)):
            path = await pending
            produced.append(path)
            current_metrics = metrics
            pending = None
            if index + 1 < len(chunks):
                pending, metrics = start(chunks[index + 1])
            yield path, {**current_metrics, "audio_chunks": len(chunks),
                         "tts_streaming": len(chunks) > 1,
                         "tts_measurement_scope": "current audio chunk"}
        if len(produced) > 1:
            await asyncio.to_thread(_assemble_chunks, text, language, produced)
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
            # An HTTP request already running in a worker thread may finish
            # caching its chunk; no further chunk or call is started.
