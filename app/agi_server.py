from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import settings
from app.conversation import CallSession, create_session
from app.stt import transcribe_file
from app.tts import synthesize, write_ulaw_or_pcm8k


async def start_agi_server() -> None:
    server = await asyncio.start_server(
        _handle_agi,
        settings.agi_host,
        settings.agi_port,
    )
    async with server:
        await server.serve_forever()


async def _handle_agi(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    env: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if not line or line in {b"\n", b"\r\n"}:
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            env[key.strip()] = value.strip()

    phone = (
        env.get("agi_arg_1")
        or env.get("agi_callerid")
        or env.get("agi_dnid")
        or ""
    )
    session = create_session(phone)
    try:
        await _conversation_loop(reader, writer, session)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _send(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    command: str,
) -> str:
    writer.write((command.strip() + "\n").encode("utf-8"))
    await writer.drain()
    reply = await reader.readline()
    return reply.decode("utf-8", errors="replace").strip()


async def _stream_file(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    wav_8k: Path,
) -> None:
    # STREAM FILE wants path without extension.
    stem = str(wav_8k.with_suffix("")).replace("\\", "/")
    await _send(reader, writer, f'STREAM FILE "{stem}" ""')


async def _record(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    wav_path: Path,
) -> None:
    stem = str(wav_path.with_suffix("")).replace("\\", "/")
    # 8s max, silence 2s, beep
    await _send(reader, writer, f'RECORD FILE "{stem}" wav "#" 8000 0 s=2')


async def _conversation_loop(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    session: CallSession,
) -> None:
    rec_dir = settings.recordings_dir
    turn = session.greeting()
    spoken = rec_dir / f"{session.session_id}_out.wav"
    phone_wav = rec_dir / f"{session.session_id}_out8k.wav"
    try:
        synthesize(turn.reply, spoken, session.language)
        write_ulaw_or_pcm8k(spoken, phone_wav)
        try:
            spoken.chmod(0o666)
            phone_wav.chmod(0o666)
        except OSError:
            pass
        await _stream_file(reader, writer, phone_wav)
    except Exception:
        await _send(reader, writer, "HANGUP")
        return

    if turn.hangup:
        await _send(reader, writer, "HANGUP")
        return

    while True:
        incoming = rec_dir / f"{session.session_id}_in.wav"
        await _record(reader, writer, incoming)
        if not incoming.is_file():
            await _send(reader, writer, "HANGUP")
            return
        try:
            transcript = transcribe_file(incoming, session.transcription_language)
        except Exception:
            transcript = ""
        result = await asyncio.to_thread(session.handle_text, transcript)
        spoken = rec_dir / f"{session.session_id}_next.wav"
        phone_wav = rec_dir / f"{session.session_id}_next8k.wav"
        try:
            synthesize(result.reply, spoken, session.language)
            write_ulaw_or_pcm8k(spoken, phone_wav)
            try:
                spoken.chmod(0o666)
                phone_wav.chmod(0o666)
            except OSError:
                pass
            await _stream_file(reader, writer, phone_wav)
        except Exception:
            await _send(reader, writer, "HANGUP")
            return
        if result.hangup:
            await _send(reader, writer, "HANGUP")
            return
