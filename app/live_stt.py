"""Growing-buffer ASR and tentative preparation; only final text commits a turn.

The selected Meta export is non-incremental: each request decodes a snapshot.
Coalesce incoming audio while one request runs, rather than queue old snapshots.
"""
import asyncio
from contextlib import suppress
from io import BytesIO
from time import monotonic
import wave

import httpx

from app.config import settings


def pcm_wav(pcm, sample_rate=8000):
    if len(pcm) % 2:
        raise ValueError('PCM16 must contain complete samples')
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class GrowingTranscriber:
    def __init__(self, language, *, on_partial=None, minimum=None, step=None, client=None, constrain_script=None, language_menu=False):
        self.language = language
        self.language_menu = language_menu
        self.constrain_script = language in {'en','hi','kn'} if constrain_script is None else constrain_script
        self.on_partial = on_partial
        self.minimum = settings.streaming_partial_min_seconds if minimum is None else minimum
        self.step = settings.streaming_partial_step_seconds if step is None else step
        self.buffer = bytearray()
        self.changed = asyncio.Event()
        self.closed = False
        self.client = client
        self.owns_client = client is None
        self.task = None
        self.latest = None
        self.partials = []
        self.last_requested = 0
        self.last_error = None
        self.requests_started = 0

    async def start(self):
        if self.client is None:
            self.client = httpx.AsyncClient(trust_env=False, timeout=settings.meta_stt_timeout_seconds)
        self.task = asyncio.create_task(self._partials())

    def feed(self, pcm, *, allow_partial=True):
        if self.closed:
            return
        if len(pcm) % 2 or len(self.buffer)+len(pcm) > settings.record_max_seconds*16000:
            raise ValueError('Live audio exceeds the bounded turn buffer')
        self.buffer.extend(pcm)
        if allow_partial:
            self.changed.set()

    async def _request(self, snapshot, *, final=False):
        started = monotonic()
        deadline = started + settings.meta_stt_timeout_seconds
        while True:
            menu_options = {'language_menu': True} if self.language_menu else {}
            response = await self.client.post(settings.meta_stt_url.rstrip('/')+'/transcribe',
                params={'language':self.language, 'trim_silence':False,
                        'constrain_script':self.constrain_script, **menu_options},
                content=pcm_wav(snapshot), headers={'Content-Type':'audio/wav'})
            # Final audio takes priority locally. Another caller's resident
            # worker may still be busy; retry only its explicit busy response.
            if response.status_code != 429 or not final or monotonic() >= deadline:
                break
            await asyncio.sleep(0.1)
        response.raise_for_status()
        data = response.json()
        if data.get('language') != self.language or not isinstance(data.get('text'), str):
            raise RuntimeError('Invalid live ASR response')
        from app.stt import _reject_wrong_indic_script
        return {'text':_reject_wrong_indic_script(data['text'].strip(), self.language),
                'samples':len(snapshot)//2, 'seconds':monotonic()-started,
                'service_metrics':data.get('timings',{})}

    async def _partials(self):
        while not self.closed:
            await self.changed.wait()
            self.changed.clear()
            if self.closed:
                return
            count = len(self.buffer)
            if self.requests_started >= settings.streaming_partial_max_requests:
                continue
            if count < self.minimum*16000 or count-self.last_requested < self.step*16000:
                continue
            snapshot = bytes(self.buffer)
            self.last_requested = count
            self.requests_started += 1
            try:
                result = await self._request(snapshot)
                self.latest = (snapshot, result)
                self.partials.append(result)
                if self.on_partial and not self.closed:
                    # This callback may prepare a draft but may not play audio
                    # or change the real CallSession, including identity state.
                    await self.on_partial(result['text'])
            except (httpx.HTTPError, RuntimeError, ValueError) as error:
                self.last_error = type(error).__name__

    async def finish(self):
        started = monotonic()
        self.closed = True
        self.changed.set()
        snapshot = bytes(self.buffer)
        # HTTP cancellation cannot cancel an already running ORT inference.
        # Await at most the one in-flight request; never queue stale prefixes.
        if self.task:
            await self.task
        waited = monotonic()-started
        if self.latest and self.latest[0] == snapshot:
            result = self.latest[1]
            reused = True
        else:
            result = await self._request(snapshot, final=True)
            reused = False
        return snapshot, result['text'], {
            'streaming_stt':True, 'partial_count':len(self.partials),
            'final_snapshot_reused':reused, 'final_asr_seconds':monotonic()-started,
            'final_wait_for_partial_seconds':waited,
            'partial_asr_seconds':sum(p['seconds'] for p in self.partials),
            'partial_error':self.last_error,
        }

    async def close(self):
        self.closed = True
        self.changed.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.owns_client and self.client:
            await self.client.aclose()


class TentativeReply:
    """One coalescing draft worker. Revisions never update the real session."""
    def __init__(self, session):
        self.session = session
        self.pending = None
        self.task = None
        self.closed = False
        self.drafts = 0

    async def update(self, text):
        from app.conversation import CallState
        if self.closed or self.session.state != CallState.VERIFIED:
            return
        self.pending = text
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._prepare())

    async def _prepare(self):
        from copy import deepcopy
        from app.intents import classify_intent
        from app.conversation import rewrite_intent_reply
        from app.audio_cache import cache_path, valid_audio
        while self.pending and not self.closed:
            text, self.pending = self.pending, None
            clone = deepcopy(self.session)
            student = next((r for r in clone.wards if r.student_id == clone.active_student_id), None)
            if student is None:
                continue
            intent = classify_intent(text, tuple(student.marks))
            if intent['intent'] not in {'subject','total','percentage','performance'}:
                continue
            generate = settings.streaming_prefetch_llm and settings.ollama_enabled and settings.llm_generate_replies
            reply = await asyncio.to_thread(rewrite_intent_reply, student, intent, clone.language, text,
                                            cached_only=not generate)
            # Only inspect an existing WAV here. Fresh TTS competing with ASR
            # made the previous laptop benchmark slower.
            valid_audio(cache_path(reply, clone.language))
            self.drafts += 1

    async def close(self):
        self.closed = True
        self.pending = None
        if self.task:
            self.task.cancel()
            # A failed optional draft must not discard a valid final question.
            with suppress(asyncio.CancelledError, Exception):
                await self.task
