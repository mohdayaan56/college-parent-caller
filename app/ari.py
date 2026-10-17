from __future__ import annotations

import asyncio
import json
import os
from time import monotonic
import wave
from contextlib import aclosing, suppress
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import websockets

from app.config import settings
from app.audio_cache import cached_speech, reply_audio
from app.conversation import CallSession, CallState, close_session, create_session, get_session
from app.language import phrase
from app.phone import digits_only, normalize_phone
from app.stt import transcribe_file
from app.tts import synthesize, write_ulaw_or_pcm8k
from app.rtp_audio import StreamingTap, MediaError, is_auxiliary, AUXILIARY_PARENTS

_SPOOL_DIRS = (
    Path("/var/spool/asterisk/recording"),
    Path("/var/spool/asterisk/recordings"),
)
_LAB_ARG = "zoiper-audio-check"

# Playback/recording waiters keyed by "PlaybackFinished:<id>" etc.
_waiters: dict[str, asyncio.Future] = {}
_channel_waiters: dict[str, list[asyncio.Future]] = {}
_dead_channels: set[str] = set()
_greetings: dict[str, tuple[str, Path]] = {}
_play_timings: dict[str, dict] = {}
_language_digits: dict[str, asyncio.Queue] = {}


def _receive_language_digit(channel_id: str, digit: str, source="ari") -> None:
    queue = _language_digits.get(channel_id)
    if queue is not None and digit in {"1", "2", "3"} and queue.empty():
        print(f"[ARI DTMF] channel={channel_id} digit={digit} source={source}")
        queue.put_nowait(digit)


def _receive_ari_digit(channel_id, digit):
    parent = AUXILIARY_PARENTS.get(channel_id)
    _receive_language_digit(parent or channel_id,digit,'ari-tap' if parent else 'ari')


async def _capture_or_language_digit(channel_id: str, capture):
    """Keep listening to speech while allowing a keypad choice to win."""
    speech = asyncio.create_task(capture)
    digit = asyncio.create_task(_language_digits[channel_id].get())
    try:
        await asyncio.wait((speech, digit), return_when=asyncio.FIRST_COMPLETED)
        if digit.done():
            return None, digit.result()
        return await speech, None
    finally:
        for task in (speech, digit):
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


def prepare_greeting(session: CallSession) -> Path:
    """Prepare the exact introduction before dialing and reuse it on answer."""
    greeting = session.greeting()
    return cached_speech(greeting.reply, session.language)


def _save_timing_report(report: dict) -> None:
    print("[CALL LATENCY] " + json.dumps(report, ensure_ascii=False), flush=True)
    try:
        with (settings.recordings_dir / "latency.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(report, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[ARI] Could not save latency measurement: {exc}")


def _playback_started(playback_id: str) -> None:
    timing = _play_timings.get(playback_id)
    if timing is None or timing.get("playback_started"):
        return
    timing["playback_started"] = True
    elapsed = monotonic() - timing["recording_finished_at"]
    report = {key: value for key, value in timing.items() if not key.endswith("_at")}
    report["phase"] = "playback_started"
    report["recording_finished_to_playback_seconds"] = round(elapsed, 3)
    # Silence endpointing is configurable. This is an estimate,
    # not a timestamp observed on the remote handset. Duration-limit turns
    # cannot use this estimate because they may have ended during speech.
    max_seconds = timing.get("record_max_seconds", settings.record_max_seconds)
    silence = timing.get("record_silence_seconds", settings.record_silence_seconds)
    if timing.get('input_method') == 'dtmf':
        report['measurement_boundary'] = 'Keypad choice handled to ARI playback event; handset delay excluded'
    elif timing.get('streaming_stt'):
        if timing.get('last_voice_at') is not None:
            report['speech_end_to_playback_estimate_seconds'] = round(monotonic()-timing['last_voice_at'],3)
        report['measurement_boundary'] = 'Estimated incoming RTP speech end to ARI playback event; handset delay excluded'
    elif timing.get("recorded_audio_seconds", max_seconds) < max_seconds - 0.1:
        report["speech_end_to_playback_estimate_seconds"] = round(elapsed + silence, 3)
        report["endpoint_silence_assumption_seconds"] = silence
    report.setdefault("measurement_boundary", "ARI events received by caller; remote handset delay excluded")
    _save_timing_report(report)


def prewarm_greetings() -> None:
    """Load local TTS before calls and cache the fixed 8 kHz introductions."""
    if settings.tts_engine != "local":
        return
    settings.recordings_dir.mkdir(parents=True, exist_ok=True)
    for language in ("en", "hi", "kn"):
        greeting = phrase(language, "greeting", college=settings.college_name)
        original = settings.recordings_dir / f"greeting-{language}.wav"
        playable = settings.recordings_dir / f"greeting-{language}-8k.wav"
        try:
            synthesize(greeting, original, language)
            write_ulaw_or_pcm8k(original, playable)
            _make_readable(playable)
            _greetings[language] = (greeting, playable)
        except Exception as exc:
            # An English-only audio check can run before other voices arrive.
            print(f"[ARI] Could not prewarm {language} greeting: {exc}")


def _auth() -> tuple[str, str]:
    return (settings.ari_user, settings.ari_password)


def sip_endpoint_for(phone: str) -> str:
    # If using softphone endpoint directly (e.g. zoiper1 or extension number)
    if settings.outbound_channel_driver not in {"pjsip", "mobile"}:
        raise ValueError("Unsupported outbound channel driver")
    if settings.outbound_channel_driver == "pjsip" and settings.sip_trunk.lower() in ("zoiper1", "zoiper", "1001", "1002"):
        return f"{settings.sip_endpoint}/{settings.sip_trunk}"
    number = digits_only(normalize_phone(phone))
    if not 10 <= len(number) <= 15:
        raise ValueError("A valid parent phone number is required for SIP origination")
    if settings.sip_dial_format == "national":
        if not (number.startswith("91") and len(number) == 12):
            raise ValueError("National dialing requires an Indian +91 number")
        number = number[2:]
    elif settings.sip_dial_format == "e164_plus":
        number = "+" + number
    elif settings.sip_dial_format != "e164_digits":
        raise ValueError(f"Unsupported SIP_DIAL_FORMAT: {settings.sip_dial_format}")
    if settings.outbound_channel_driver == "mobile":
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]+", settings.mobile_device):
            raise ValueError("Invalid mobile device name")
        return f"Mobile/{settings.mobile_device}/{number}"
    return f"{settings.sip_endpoint}/{number}@{settings.sip_trunk}"


def _make_readable(path: Path) -> None:
    try:
        os.chmod(path, 0o666)
    except OSError:
        pass


def _playable_wav(wav_path: Path) -> Path:
    """Put the 8 kHz WAV where Asterisk can read it, return the file to play."""
    _make_readable(wav_path)
    try:
        sounds_dir = settings.asterisk_sounds_dir
        sounds_dir.mkdir(parents=True, exist_ok=True)
        dest = sounds_dir / wav_path.name
        dest.write_bytes(wav_path.read_bytes())
        _make_readable(dest)
        return dest
    except OSError as exc:
        raise RuntimeError(f"Asterisk cannot read the generated audio in {settings.asterisk_sounds_dir}: {exc}") from exc


def _media_uri(wav_path: Path) -> str:
    playable = _playable_wav(wav_path)
    return f"sound:parent-marks/{playable.stem}"


def _find_recording(name: str) -> Path | None:
    candidates = [settings.recordings_dir / f"{name}.wav"]
    for spool in _SPOOL_DIRS:
        candidates.append(spool / f"{name}.wav")
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 44:
                return path
        except OSError:
            continue
    return None


def _notify(key: str, payload: dict) -> None:
    fut = _waiters.get(key)
    if fut and not fut.done():
        fut.set_result({**payload, "_received_monotonic": monotonic()})


def _fail_channel_waiters(channel_id: str) -> None:
    _dead_channels.add(channel_id)
    for fut in _channel_waiters.pop(channel_id, []):
        if not fut.done():
            fut.set_exception(RuntimeError(f"channel {channel_id} hung up"))


def _register_waiter(key: str, channel_id: str) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _waiters[key] = fut
    _channel_waiters.setdefault(channel_id, []).append(fut)
    return fut


async def _wait_event(key: str, fut: asyncio.Future, timeout: float, channel_id: str) -> dict:
    try:
        return await asyncio.wait_for(fut, timeout)
    finally:
        _waiters.pop(key, None)
        channel_waiters = _channel_waiters.get(channel_id, [])
        if fut in channel_waiters:
            channel_waiters.remove(fut)
        if not channel_waiters:
            _channel_waiters.pop(channel_id, None)


async def originate(phone: str, session_id: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.ari_url.rstrip('/')}/channels",
            params={
                "endpoint": sip_endpoint_for(phone),
                "app": settings.ari_app,
                "appArgs": f"{session_id},{phone}",
                "callerId": settings.outbound_caller_id,
                "timeout": 45,
                "formats": "ulaw,alaw",
            },
            auth=_auth(),
            timeout=20.0,
        )
        response.raise_for_status()
        return response.json() if response.content else {}


async def _ari(method: str, path: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(trust_env=False) as client:
        return await client.request(
            method,
            f"{settings.ari_url.rstrip('/')}{path}",
            auth=_auth(),
            timeout=20.0,
            **kwargs,
        )


async def _answer(channel_id: str) -> None:
    res = await _ari("POST", f"/channels/{channel_id}/answer")
    res.raise_for_status()
    print(f"[ARI ANSWER] status={res.status_code} body={res.text}")


async def _play(channel_id: str, wav_path: Path, timing: dict | None = None) -> None:
    if channel_id in _dead_channels:
        return
    media = _media_uri(wav_path)
    # Keep slashes in the path. httpx params= would turn them into %2F and
    # Asterisk then cannot find the sound file (silent call, no RTP).
    encoded = quote(media, safe=":/")
    print(f"[ARI PLAY] media='{media}' on channel '{channel_id}'")
    playback_id = uuid4().hex
    if timing is not None:
        _play_timings[playback_id] = timing
    key = f"PlaybackFinished:{playback_id}"
    fut = _register_waiter(key, channel_id)
    request = asyncio.create_task(_ari("POST", f"/channels/{channel_id}/play/{playback_id}?media={encoded}"))
    try:
        res = await asyncio.shield(request)
        res.raise_for_status()
        await _wait_event(key, fut, timeout=60, channel_id=channel_id)
    except (asyncio.TimeoutError, RuntimeError) as exc:
        raise RuntimeError(f"Asterisk did not finish playback on {channel_id}: {exc}") from exc
    except asyncio.CancelledError:
        # Cancelling a Python waiter alone leaves Asterisk speaking. Stop the
        # actual playback, including when cancellation races its POST response.
        with suppress(Exception):
            await request
        stopped = await _ari("DELETE", f"/playbacks/{playback_id}")
        if stopped.status_code != 404:
            stopped.raise_for_status()
        raise
    finally:
        if timing is not None and not timing.get("playback_started"):
            report = {key: value for key, value in timing.items() if not key.endswith("_at")}
            report["phase"] = "playback_not_observed"
            _save_timing_report(report)
        _play_timings.pop(playback_id, None)
        if _waiters.get(key) is fut:
            _waiters.pop(key, None)
        if fut in _channel_waiters.get(channel_id, []):
            _channel_waiters[channel_id].remove(fut)


async def _record(channel_id: str, name: str, timing: dict | None = None) -> Path | None:
    if channel_id in _dead_channels:
        return None
    print(f"[ARI RECORD] name='{name}' on channel '{channel_id}'")
    key = f"RecordingFinished:{name}"
    fut = _register_waiter(key, channel_id)
    try:
        res = await _ari(
            "POST",
            f"/channels/{channel_id}/record",
            params={
                "name": name,
                "format": "wav",
                "maxDurationSeconds": settings.record_max_seconds,
                "maxSilenceSeconds": settings.record_silence_seconds,
                "beep": "true",
                "ifExists": "overwrite",
            },
        )
        res.raise_for_status()
        event = await _wait_event(key, fut, timeout=20, channel_id=channel_id)
        if timing is not None:
            timing["recording_finished_at"] = event.get("_received_monotonic", monotonic())
        if event.get("type") == "RecordingFailed":
            return None
    except (asyncio.TimeoutError, RuntimeError) as exc:
        print(f"[ARI RECORD] wait ended: {exc}")
        if channel_id not in _dead_channels:
            await _ari("DELETE", f"/recordings/live/{name}")
    finally:
        if _waiters.get(key) is fut:
            _waiters.pop(key, None)
        if fut in _channel_waiters.get(channel_id, []):
            _channel_waiters[channel_id].remove(fut)
    # ARI serves stored audio even when the application and Asterisk have
    # separate filesystems (for example a Windows app and Linux PBX).
    for attempt in range(3):
        try:
            res = await _ari("GET", f"/recordings/stored/{name}/file")
            res.raise_for_status()
            dest = settings.recordings_dir / f"{name}.wav"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(res.content)
            with wave.open(str(dest), "rb") as wav:
                if wav.getnframes() == 0:
                    raise ValueError("empty recording")
                if timing is not None:
                    timing["recorded_audio_seconds"] = round(wav.getnframes() / wav.getframerate(), 3)
            try:
                await _ari("DELETE", f"/recordings/stored/{name}")
            except Exception as exc:
                print(f"[ARI RECORD] could not remove stored copy: {exc}")
            if timing is not None:
                timing["recording_fetch_seconds"] = round(monotonic() - timing["recording_finished_at"], 3)
            return dest
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if attempt == 2:
                print(f"[ARI RECORD] could not fetch {name}: {exc}")
            else:
                await asyncio.sleep(0.2)
    return _find_recording(name)


async def originate_zoiper_audio_check() -> dict:
    if not settings.ari_enabled or settings.sip_trunk.lower() not in {"zoiper1", "zoiper", "1001", "1002"}:
        raise RuntimeError("Set ARI_ENABLED=true and SIP_TRUNK=zoiper1 for the Zoiper check")
    return await originate("lab", _LAB_ARG)


async def _handle_zoiper_audio_check(channel_id: str) -> None:
    """Play a spoken prompt, capture microphone audio, and echo it to Zoiper."""
    _dead_channels.discard(channel_id)
    prefix = f"zoiper-check-{uuid4().hex}"
    try:
        await _answer(channel_id)
        original = settings.recordings_dir / f"{prefix}-prompt.wav"
        playable = settings.recordings_dir / f"{prefix}-prompt-8k.wav"
        await asyncio.to_thread(
            synthesize,
            "Hello. This is the college audio check. After the beep, say a short sentence. I will play it back.",
            original,
            "en",
        )
        await asyncio.to_thread(write_ulaw_or_pcm8k, original, playable)
        await _play(channel_id, playable)
        recording = await _record(channel_id, f"{prefix}-reply")
        if recording and channel_id not in _dead_channels:
            await _play(channel_id, recording)
        else:
            print("[ARI LAB] No caller audio was received")
    except Exception as exc:
        print(f"[ARI LAB] Audio check failed: {exc}")
    finally:
        if channel_id not in _dead_channels:
            try:
                await _hangup(channel_id)
            except Exception:
                pass
        _dead_channels.discard(channel_id)


async def _hangup(channel_id: str) -> None:
    await _ari("DELETE", f"/channels/{channel_id}")


async def _play_reply(channel_id: str, text: str, language: str, timing: dict) -> None:
    first = True
    async with aclosing(reply_audio(text, language)) as chunks:
        async for path, metrics in chunks:
            if channel_id in _dead_channels:
                break
            if first:
                timing.update(metrics)
                timing["playback_requested_at"] = monotonic()
                report = {key: value for key, value in timing.items() if not key.endswith("_at")}
                report["phase"] = "reply_ready"
                report["recording_finished_to_reply_ready_seconds"] = round(monotonic() - timing["recording_finished_at"], 3)
                _save_timing_report(report)
            await _play(channel_id, path, timing if first else None)
            first = False


async def handle_stasis(channel_id: str, session: CallSession) -> None:
    tap = None
    _language_digits[channel_id] = asyncio.Queue(maxsize=1)
    try:
        if settings.streaming_stt_enabled:
            tap = StreamingTap(channel_id, _ari, alive=lambda:channel_id not in _dead_channels,
                on_digit=lambda digit:_receive_language_digit(channel_id, digit, 'inband'))
            try:
                await tap.open()
            except Exception as exc:
                print(f'[ARI STREAM] Setup failed; using recording fallback: {type(exc).__name__}')
                tap = None
        await _handle_parent_turns(channel_id, session, tap)
    finally:
        _language_digits.pop(channel_id, None)
        if tap:
            await tap.close()


async def _play_reply_listening(channel_id, session, tap, turn, timing):
    """Return the same capture task after playback; never discard an onset."""
    speech_started, playback_finished = asyncio.Event(), asyncio.Event()
    capture = asyncio.create_task(tap.collect(session, speech_started=speech_started,
                                             playback_finished=playback_finished))
    speech = asyncio.create_task(speech_started.wait())
    playback = asyncio.create_task(_play_reply(channel_id, turn.reply, session.language, timing))
    handed_off = False
    try:
        await asyncio.wait((speech, playback), return_when=asyncio.FIRST_COMPLETED)
        if speech_started.is_set() and not playback.done():
            interrupt_at = monotonic()
            playback.cancel()
            with suppress(asyncio.CancelledError):
                await playback
            _save_timing_report({'session_id':session.session_id, 'phase':'barge_in',
                                'speech_detected_to_stop_seconds':round(monotonic()-interrupt_at, 3)})
        else:
            await playback
        handed_off = True
        return capture
    finally:
        playback_finished.set()
        for task in (speech, playback) + (() if handed_off else (capture,)):
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


async def _handle_parent_turns(channel_id: str, session: CallSession, tap=None) -> None:
    captures = set()
    try:
        await _parent_turn_loop(channel_id, session, tap, captures)
    finally:
        for capture in captures:
            if not capture.done():
                capture.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await capture


async def _play_language_menu_listening(channel_id, session, tap, play):
    """Keep the same speech capture across a menu prompt, including early digits."""
    if settings.language_menu_keypad_only:
        if tap:
            tap.receiver.begin_menu_diagnostics()
        return await _play_keypad_menu(channel_id,play)
    speech_started, playback_finished = asyncio.Event(), asyncio.Event()
    capture = asyncio.create_task(_capture_or_language_digit(channel_id,
        tap.collect(session, speech_started=speech_started, playback_finished=playback_finished)))
    speech = asyncio.create_task(speech_started.wait())
    playback = asyncio.create_task(play)
    handed_off = False
    try:
        await asyncio.wait((speech, capture, playback), return_when=asyncio.FIRST_COMPLETED)
        if (speech_started.is_set() or capture.done()) and not playback.done():
            playback.cancel()
            with suppress(asyncio.CancelledError):
                await playback
        else:
            await playback
        handed_off = True
        return capture
    finally:
        playback_finished.set()
        for task in (speech, playback) + (() if handed_off else (capture,)):
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


async def _wait_keypad_choice(channel_id, playback_finished=None):
    digit = asyncio.create_task(_language_digits[channel_id].get())
    finished = None
    try:
        if playback_finished is not None:
            finished = asyncio.create_task(playback_finished.wait())
            await asyncio.wait((digit,finished),return_when=asyncio.FIRST_COMPLETED)
        try:
            value = await asyncio.wait_for(asyncio.shield(digit),settings.record_max_seconds)
        except asyncio.TimeoutError:
            value = ''  # Ordinary invalid menu turn; no speech inference.
        return None,value
    finally:
        for task in (digit,finished):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def _play_keypad_menu(channel_id,play):
    finished = asyncio.Event()
    capture = asyncio.create_task(_wait_keypad_choice(channel_id,finished))
    playback = asyncio.create_task(play)
    handed_off = False
    try:
        await asyncio.wait((capture,playback),return_when=asyncio.FIRST_COMPLETED)
        if capture.done() and not playback.done():
            playback.cancel()
            with suppress(asyncio.CancelledError):
                await playback
        else:
            await playback
        handed_off = True
        return capture
    finally:
        finished.set()
        if not handed_off:
            capture.cancel()
        if not playback.done():
            playback.cancel()
        for task in ((playback,) if handed_off else (playback,capture)):
            with suppress(asyncio.CancelledError,Exception):
                await task


async def _parent_turn_loop(channel_id, session, tap, captures) -> None:
    rec_dir = settings.recordings_dir
    rec_dir.mkdir(parents=True, exist_ok=True)
    _dead_channels.discard(channel_id)

    await _answer(channel_id)

    greeting = session.greeting()
    try:
        ast_hi = await asyncio.to_thread(prepare_greeting, session)
    except Exception as exc:
        print(f"[ARI ERROR] Greeting synthesis failed: {exc}")
        import traceback

        traceback.print_exc()
        await _hangup(channel_id)
        return

    pending_menu = None
    if (tap or settings.language_menu_keypad_only) and session.state == CallState.AWAITING_LANGUAGE:
        pending_menu = await _play_language_menu_listening(channel_id, session, tap,
                                                          _play(channel_id, ast_hi))
        captures.add(pending_menu)
    else:
        await _play(channel_id, ast_hi)
    if greeting.hangup or channel_id in _dead_channels:
        await _hangup(channel_id)
        return

    empty_turns = 0
    pending_capture = None
    while channel_id not in _dead_channels:
        rec_name = f"parentmarks-{session.session_id}-{uuid4().hex[:6]}"
        timing = {"session_id": session.session_id, "recording": rec_name,
                  "record_silence_seconds": settings.record_silence_seconds,
                  "record_max_seconds": settings.record_max_seconds}
        stage_started = monotonic()
        language_menu = session.state == CallState.AWAITING_LANGUAGE
        digit = None
        if language_menu and settings.language_menu_keypad_only:
            if pending_menu is not None:
                capture,pending_menu = pending_menu,None
                try:
                    live,digit = await capture
                finally:
                    captures.discard(capture)
            else:
                live,digit = await _wait_keypad_choice(channel_id)
            if tap:
                report = tap.receiver.finish_menu_diagnostics(rec_name)
                print('[PHONE MENU] '+json.dumps({'session_id':session.session_id,'digit':digit,**report}))
            audio = True
        elif tap:
            try:
                if language_menu:
                    if pending_menu is not None:
                        capture, pending_menu = pending_menu, None
                        try:
                            live, digit = await capture
                        finally:
                            captures.discard(capture)
                    else:
                        live, digit = await _capture_or_language_digit(channel_id, tap.collect(session))
                elif pending_capture is not None:
                    capture, pending_capture = pending_capture, None
                    try:
                        live = await capture
                    finally:
                        captures.discard(capture)
                else:
                    live = await tap.collect(session)
                if digit is None:
                    timing.update(live.metrics)
                    transcript = live.transcript
                    timing['stt_seconds'] = live.metrics['final_asr_seconds']
                audio = True  # live PCM stays in memory; no extra call recording
            except Exception as exc:
                if channel_id in _dead_channels:
                    break
                empty_turns += 1
                print(f'[ARI STREAM] Capture failed: {type(exc).__name__}; asking to repeat')
                if empty_turns >= 3:
                    await _hangup(channel_id)
                    break
                # Do not feed damaged/partial audio into identity verification.
                timing['recording_finished_at'] = monotonic()
                await _play_reply(channel_id, phrase(session.language,'stt_fail'),session.language,timing)
                await tap.close()
                tap = None
                continue
        else:
            if language_menu:
                audio, digit = await _capture_or_language_digit(channel_id, _record(channel_id, rec_name, timing))
                if digit is not None:
                    with suppress(Exception):
                        await _ari("DELETE", f"/recordings/live/{rec_name}")
                    audio = True
            else:
                audio = await _record(channel_id, rec_name, timing)
        if channel_id in _dead_channels:
            break
        if audio is None:
            empty_turns += 1
            print(f"[ARI] No recording file for {rec_name} (attempt {empty_turns})")
            if empty_turns >= 3:
                await _hangup(channel_id)
                break
            continue
        empty_turns = 0
        if digit is not None:
            transcript = digit
            timing.update(input_method="dtmf" if digit else "keypad_timeout",
                          stt_seconds=0.0, recording_finished_at=monotonic())
        elif not tap:
            stage_started = monotonic()
            try:
                transcript = await asyncio.to_thread(transcribe_file, audio, session.transcription_language,
                    language_menu=language_menu,
                    constrain_script=language_menu or session.transcription_language in {'en','hi','kn'})
            except Exception as exc:
                print(f"[ARI] STT failed: {exc}")
                transcript = ""
            timing["stt_seconds"] = round(monotonic() - stage_started, 3)
        print(f"[ARI] transcript={transcript!r}")
        stage_started = monotonic()
        turn = await asyncio.to_thread(session.handle_text, transcript or "")
        if session.state != CallState.AWAITING_LANGUAGE:
            _language_digits.pop(channel_id, None)
        timing["reply_preparation_seconds"] = round(monotonic() - stage_started, 3)
        timing['llm_used'] = session.last_llm_used
        timing['llm_seconds'] = session.last_llm_seconds
        timing['requested_kinds'] = [request['kind'] for request in session.last_requests]
        timing["language"] = session.language
        try:
            if (tap or settings.language_menu_keypad_only) and session.state == CallState.AWAITING_LANGUAGE and not turn.hangup:
                pending_menu = await _play_language_menu_listening(channel_id, session, tap,
                    _play_reply(channel_id, turn.reply, session.language, timing))
                captures.add(pending_menu)
            elif tap and session.state == CallState.VERIFIED and not turn.hangup:
                pending_capture = await _play_reply_listening(channel_id, session, tap, turn, timing)
                captures.add(pending_capture)
            else:
                await _play_reply(channel_id, turn.reply, session.language, timing)
        except Exception as exc:
            print(f"[ARI ERROR] Reply generation/playback failed: {exc}")
            await _hangup(channel_id)
            break
        if turn.hangup:
            await _hangup(channel_id)
            break


async def _handle_campaign_stasis(channel_id: str, session: CallSession, call_id: str) -> None:
    from app.campaign import finish_call

    try:
        await handle_stasis(channel_id, session)
        status = (
            "opted_out" if session.opted_out else
            "completed" if session.was_verified else
            "denied" if session.state.value == "denied" else "failed"
        )
        finish_call(call_id, status)
    except Exception as exc:
        print(f"[ARI CALL ERROR] {exc}")
        finish_call(call_id, "failed", str(exc))
        if channel_id not in _dead_channels:
            try:
                await _hangup(channel_id)
            except Exception:
                pass
    finally:
        close_session(session.session_id)
        _dead_channels.discard(channel_id)


async def _handle_regular_stasis(channel_id: str, session: CallSession) -> None:
    try:
        await handle_stasis(channel_id, session)
    except Exception as exc:
        print(f"[ARI CALL ERROR] {exc}")
        if channel_id not in _dead_channels:
            try:
                await _hangup(channel_id)
            except Exception:
                pass
    finally:
        close_session(session.session_id)
        _dead_channels.discard(channel_id)


def _events_url() -> str:
    base = urlsplit(settings.ari_url.rstrip("/") + "/events")
    scheme = {"http": "ws", "https": "wss"}.get(base.scheme)
    if scheme is None:
        raise ValueError("ARI_URL must start with http:// or https://")
    query = urlencode({"app": settings.ari_app, "api_key": f"{settings.ari_user}:{settings.ari_password}"})
    return urlunsplit((scheme, base.netloc, base.path, query, ""))


async def ari_events_loop() -> None:
    url = _events_url()
    print("[ARI LOOP] Starting WebSocket event listener...")
    delay = 1
    while True:
        try:
            async with websockets.connect(url, open_timeout=10) as socket:
                print("[ARI LOOP] Connected to ARI event stream.")
                delay = 1
                async for line in socket:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "ChannelDtmfReceived":
                        _receive_ari_digit((event.get('channel') or {}).get('id'),event.get('digit'))
                        continue
                    if is_auxiliary((event.get('channel') or {}).get('id')):
                        continue
                    print(f"[ARI EVENT] {etype} - {event.get('application')}")
                    if etype == "StasisStart":
                        channel = event.get("channel") or {}
                        channel_id = channel.get("id")
                        args = event.get("args") or []
                        print(f"[ARI STASIS START] channel={channel_id}, args={args}")
                        if args and args[0] == _LAB_ARG:
                            asyncio.create_task(_handle_zoiper_audio_check(channel_id))
                            continue
                        session = None
                        campaign_call_id = None
                        phone = ""
                        if args:
                            if args[0].startswith("campaign:"):
                                from app.campaign import records_for_call

                                campaign_call_id = args[0].partition(":")[2]
                                snapshot = records_for_call(campaign_call_id)
                                if snapshot is not None:
                                    phone, records = snapshot
                                    session = create_session(phone, records=records)
                            else:
                                session = get_session(args[0])
                            if len(args) > 1 and not campaign_call_id:
                                phone = args[1]
                        if session is None:
                            phone = phone or (channel.get("caller") or {}).get("number") or ""
                            session = create_session(phone)
                        if campaign_call_id:
                            from app.campaign import mark_connected

                            mark_connected(campaign_call_id)
                            asyncio.create_task(_handle_campaign_stasis(channel_id, session, campaign_call_id))
                        else:
                            asyncio.create_task(_handle_regular_stasis(channel_id, session))
                    elif etype == "PlaybackStarted":
                        _playback_started((event.get("playback") or {}).get("id", ""))
                    elif etype == "PlaybackFinished":
                        playback = event.get("playback") or {}
                        _notify(f"PlaybackFinished:{playback.get('id')}", event)
                    elif etype in {"RecordingFinished", "RecordingFailed"}:
                        recording = event.get("recording") or {}
                        _notify(f"RecordingFinished:{recording.get('name')}", event)
                    elif etype in {"StasisEnd", "ChannelDestroyed", "ChannelHangupRequest"}:
                        channel = event.get("channel") or {}
                        cid = channel.get("id")
                        if cid:
                            _fail_channel_waiters(cid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[ARI LOOP ERROR] Event stream disconnected: {exc}. Retrying in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
