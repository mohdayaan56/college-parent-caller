"""Require sustained speech and recognized words before interrupting playback."""
import asyncio
from contextlib import suppress
import re
import unicodedata

from app.config import settings


def spoken_words(text):
    # Non-speech labels are metadata rather than a parent's utterance.
    text = re.sub(r'\[[^\]]*\]|\([^)]*\)|<[^>]*>', '', text or '')
    return any(unicodedata.category(char)[0] in {'L', 'N'} for char in text)


class SpeechInterruptGuard:
    def __init__(self, decode):
        self.decode = decode
        self.audio = bytearray()
        self.candidate = False
        self.task = None
        self.last_requested = 0
        self.probes = 0
        self.total_probes = 0
        self.rejected = 0
        self.error = None

    def feed(self, pcm, endpoint, ended):
        self.audio.extend(pcm[:max(0, settings.record_max_seconds * 16000 - len(self.audio))])
        if not self.candidate:
            del self.audio[:-6400]  # Preserve 400 ms, including the word onset.
            self.candidate = endpoint.interrupt_run >= int(settings.barge_in_min_speech_seconds * 8000)
        if self.task is not None and self.task.done():
            try:
                result = self.task.result()
            except (Exception, asyncio.CancelledError) as error:
                self.error = type(error).__name__
                result = {}
            self.task = None
            if spoken_words(result.get('text')):
                return True
            if ended:
                self.rejected += 1
                self.reset_candidate()
        if not self.candidate:
            return False
        # At most one request in flight and four snapshots per utterance.
        # Receiving audio continues while local ASR checks the candidate.
        if (self.task is None and self.probes < 4
                and (self.probes == 0 or len(self.audio) - self.last_requested >= 8000)):
            self.last_requested = len(self.audio)
            self.probes += 1
            self.total_probes += 1
            self.task = asyncio.create_task(self.decode(bytes(self.audio)))
        if len(self.audio) >= settings.record_max_seconds * 16000 or (ended and self.task is None):
            self.rejected += 1
            self.reset_candidate()
        return False

    def reset_candidate(self):
        if self.task:
            self.task.cancel()
            self.task = None
        del self.audio[:-6400]
        self.candidate = False
        self.probes = self.last_requested = 0

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.task
