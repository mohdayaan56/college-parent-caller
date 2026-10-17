from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.ari import ari_events_loop, originate, originate_zoiper_audio_check, prewarm_greetings, prepare_greeting
from app.config import settings
from app.audio_cache import prepare_call_audio, call_audio_status
from app.conversation import create_session, get_session, close_session
from app.csv_store import CsvError, find_by_student_id
from app.stt import transcribe_file
from app.tts import synthesize


class StartCallBody(BaseModel):
    phone: str | None = None
    student_id: str | None = None


class TextTurnBody(BaseModel):
    text: str = Field(min_length=0)


def _save_upload(data: bytes, suffix: str = ".wav") -> Path:
    path = settings.recordings_dir / f"upload-{uuid4().hex}{suffix}"
    path.write_bytes(data)
    return path


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if settings.admin_api_token:
        if not x_admin_token or not hmac.compare_digest(x_admin_token, settings.admin_api_token):
            raise HTTPException(status_code=401, detail="Invalid admin token")
    elif settings.app_host not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=503, detail="ADMIN_API_TOKEN is required for a network bind")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.app_host not in {"127.0.0.1", "localhost", "::1"} and not settings.admin_api_token:
        raise RuntimeError("Set ADMIN_API_TOKEN before binding the API to the network")
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    if settings.stt_engine == 'meta_omnilingual' and settings.record_max_seconds > 39:
        raise RuntimeError('Meta STT accepts at most 39 seconds per segment; lower RECORD_MAX_SECONDS.')
    if settings.streaming_stt_enabled:
        if settings.stt_engine != 'meta_omnilingual':
            raise RuntimeError('Live STT currently requires STT_ENGINE=meta_omnilingual.')
        from app.rtp_audio import warm_endpoint
        await asyncio.to_thread(warm_endpoint)
    task = None
    if settings.ollama_enabled:
        from app.llm import warmup
        await asyncio.to_thread(warmup)
    if settings.ari_enabled:
        await asyncio.to_thread(prewarm_greetings)
        task = asyncio.create_task(ari_events_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="Parent Marks Voice Assistant",
    description="Local STT/LLM/TTS assistant that reads marks only from CSV.",
    lifespan=lifespan,
)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
def health() -> dict:
    csv_ok = Path(settings.csv_path).is_file()
    return {"ok": True, "csv": csv_ok, "ari": settings.ari_enabled}


@app.post("/sessions", dependencies=[Depends(require_admin)])
def start_session(body: StartCallBody) -> dict:
    phone = body.phone
    if body.student_id and not phone:
        try:
            student = find_by_student_id(body.student_id)
        except CsvError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not student:
            raise HTTPException(status_code=404, detail="Unknown student_id")
        phone = student.parent_phone
    if not phone:
        raise HTTPException(status_code=400, detail="Provide phone or student_id")
    session = create_session(phone)
    turn = session.greeting()
    return {
        "session_id": session.session_id,
        "state": turn.state.value,
        "reply": turn.reply,
        "hangup": turn.hangup,
        "verified": turn.verified,
        "phone": phone,
    }


@app.post("/sessions/{session_id}/turn", dependencies=[Depends(require_admin)])
def text_turn(session_id: str, body: TextTurnBody) -> dict:
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")
    try:
        turn = session.handle_text(body.text)
    except Exception:
        turn = session.handle_text(body.text, use_llm=False)
    return {
        "session_id": session.session_id,
        "state": turn.state.value,
        "reply": turn.reply,
        "hangup": turn.hangup,
        "verified": turn.verified,
    }


@app.post("/sessions/{session_id}/audio", response_model=None, dependencies=[Depends(require_admin)])
async def audio_turn(
    session_id: str,
    audio: UploadFile = File(...),
    speak: bool = Form(True),
):
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")
    suffix = Path(audio.filename or "clip.wav").suffix or ".wav"
    saved = _save_upload(await audio.read(), suffix)
    try:
        transcript = await asyncio.to_thread(transcribe_file, saved, session.transcription_language)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Speech recognition failed. Marks will not be guessed. ({exc})",
        ) from exc
    turn = await asyncio.to_thread(session.handle_text, transcript)
    payload = {
        "session_id": session.session_id,
        "state": turn.state.value,
        "reply": turn.reply,
        "hangup": turn.hangup,
        "verified": turn.verified,
        "transcript": transcript,
    }
    if not speak:
        return payload
    out = settings.recordings_dir / f"{session.session_id}-reply.wav"
    try:
        await asyncio.to_thread(synthesize, turn.reply, out, session.language)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Text-to-speech failed. ({exc})",
        ) from exc
    return FileResponse(out, media_type="audio/wav", filename="reply.wav")


@app.post("/calls/prepare", dependencies=[Depends(require_admin)])
async def prepare_phone_call(body: StartCallBody) -> dict:
    """Generate known three-language speech without placing any call."""
    phone = body.phone
    if body.student_id:
        try:
            student = find_by_student_id(body.student_id)
        except CsvError as exc:
            raise HTTPException(503, str(exc)) from exc
        if student is None:
            raise HTTPException(404, "Unknown student_id")
        phone = student.parent_phone
    if not phone:
        raise HTTPException(400, "Provide phone or student_id")
    from app.conversation import CallSession
    session = CallSession(expected_phone=phone)
    return await asyncio.to_thread(prepare_call_audio, session)


@app.post("/calls/originate", dependencies=[Depends(require_admin)])
async def start_phone_call(body: StartCallBody) -> dict:
    phone = body.phone
    if body.student_id:
        try:
            student = find_by_student_id(body.student_id)
        except CsvError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not student:
            raise HTTPException(status_code=404, detail="Unknown student_id")
        phone = student.parent_phone
    if not phone:
        raise HTTPException(status_code=400, detail="Provide phone or student_id")
    session = create_session(phone)
    session.greeting()
    try:
        if settings.require_prepared_call_audio:
            status = call_audio_status(session)
            if not status["ready"]:
                raise HTTPException(409, {**status, "message": "Prepare speech first using POST /calls/prepare. No call placed."})
        await asyncio.to_thread(prepare_greeting, session)
        channel = await originate(phone, session.session_id)
    except Exception as exc:
        close_session(session.session_id)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=502,
            detail=f"Asterisk originate failed: {exc}",
        ) from exc
    return {
        "session_id": session.session_id,
        "phone": phone,
        "channel": channel,
    }


@app.post("/lab/zoiper-audio-check", dependencies=[Depends(require_admin)])
async def zoiper_audio_check() -> dict:
    """Ring the registered Zoiper extension and verify audio in both directions."""
    try:
        return {"channel": await originate_zoiper_audio_check()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Zoiper check failed: {exc}") from exc


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        # Reload kills the ARI event stream mid-call.
        reload=not settings.ari_enabled,
    )


if __name__ == "__main__":
    run()
