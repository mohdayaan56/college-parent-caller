"""No-call smoke test: real VITS, PCMU, VAD and ASR; simulated bot playback.

Run in the existing caller container with python -m scripts.check_barge_in.
Add --pbx to verify the real Asterisk stop operation on a temporary UDP channel.
Neither mode rings a handset.
"""
import asyncio
import argparse
import json
import struct
from time import monotonic
import wave
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import suppress
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode
from uuid import uuid4

import numpy as np
import websockets

from app import ari
from app.audio_cache import cached_speech
from app.conversation import CallSession, CallState
from app.csv_store import find_by_student_id
from app.intents import classify_intent
from app.rtp_audio import StreamingTap, ULAW
from app.config import settings


async def check_pbx_stop(path):
    """Play to a temporary UDP media channel, never to the softphone."""
    app_name = 'barge-smoke-' + uuid4().hex
    channel_id = app_name + '-media'
    received = asyncio.Event()
    class Receiver(asyncio.DatagramProtocol):
        def datagram_received(self, data, address):
            if len(data) > 12:
                received.set()
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        Receiver, local_addr=(settings.streaming_audio_bind_host,
                             settings.streaming_audio_port+settings.streaming_audio_ports-1))
    port = transport.get_extra_info('sockname')[1]
    url = urlsplit(ari._events_url())
    query = parse_qs(url.query)
    query['app'] = [app_name]
    url = urlunsplit(url._replace(query=urlencode(query, doseq=True)))
    stopped = asyncio.Event()
    async def consume(socket):
        async for message in socket:
            event = json.loads(message)
            if event.get('type') == 'PlaybackFinished':
                ari._notify('PlaybackFinished:' + event['playback']['id'], event)
                stopped.set()
    playback = consumer = None
    try:
        async with websockets.connect(url, open_timeout=10) as socket:
            consumer = asyncio.create_task(consume(socket))
            response = await ari._ari('POST', '/channels/externalMedia', params={
                'app':app_name, 'channelId':channel_id,
                'external_host':f'{settings.streaming_audio_advertise_host}:{port}',
                'format':'ulaw', 'transport':'udp', 'encapsulation':'rtp', 'direction':'both'})
            response.raise_for_status()
            playback = asyncio.create_task(ari._play(channel_id, path))
            await asyncio.wait_for(received.wait(), 10)
            if playback.done():
                raise AssertionError('Playback ended before stop test')
            started = monotonic()
            playback.cancel()
            with suppress(asyncio.CancelledError):
                await playback
            await asyncio.wait_for(stopped.wait(), 5)
            return {'scope':'Real Asterisk playback to temporary UDP media channel; no handset',
                    'audio_received':True, 'playback_finished_after_cancel':True,
                    'cancel_to_finished_event_seconds':round(monotonic()-started,3)}
    finally:
        for task in (playback, consumer):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        with suppress(Exception):
            await ari._ari('DELETE', '/channels/' + channel_id)
        transport.close()


async def main(pbx=False):
    student = find_by_student_id('demo-001')
    if student is None:
        raise RuntimeError('Fictional demo-001 record required')
    session = CallSession(expected_phone=student.parent_phone, records=[student])
    session.greeting()
    if session.state == CallState.AWAITING_LANGUAGE:
        session.handle_text('1', use_llm=False)
    intro = session.handle_text(student.parent_name, use_llm=False)
    if session.state != CallState.VERIFIED or session.language != 'en':
        raise RuntimeError('Test requires the verified English fictional record')
    prompt = 'What is the total?'
    path = await asyncio.to_thread(cached_speech, prompt, 'en')
    with wave.open(str(path), 'rb') as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (8000, 1, 2):
            raise RuntimeError('Expected telephone PCM16 WAV')
        audio = wav.readframes(wav.getnframes())
    audio = b'\0\0'*4000 + audio + b'\0\0'*16000
    stopped = asyncio.Event()
    times = {}
    async def simulated_playback(*args):
        try:
            await asyncio.Future()
        finally:
            times['playback_stopped_at'] = monotonic()
            stopped.set()
    tap = StreamingTap('offline-barge-demo', None)
    async def feed():
        while tap.receiver.queue is None:
            await asyncio.sleep(0)
        for position in range(0, len(audio), 320):
            block = audio[position:position+320].ljust(320, b'\0')
            samples = np.frombuffer(block, dtype='<i2').astype(np.int32)
            ulaw = np.abs(samples[:,None]-ULAW.astype(np.int32)).argmin(axis=1).astype(np.uint8)
            packet = struct.pack('!BBHII',128,0,(position//320)%65536,position//2,1) + ulaw.tobytes()
            tap.receiver.datagram_received(packet, ('127.0.0.1', 9000))
            if position == 8000:
                times['question_audio_started_at'] = monotonic()
            await asyncio.sleep(.02)
    with patch.object(ari, '_play_reply', simulated_playback):
        feeder = asyncio.create_task(feed())
        try:
            capture = await ari._play_reply_listening(tap.channel_id, session, tap,
                                                     SimpleNamespace(reply='long marks summary'), {})
            live = await capture
            if not stopped.is_set():
                raise AssertionError('Bot playback was not interrupted')
            intent = classify_intent(live.transcript, list(student.marks))
            if intent.get('intent') != 'total':
                raise AssertionError(f'Question was not recognized: {live.transcript!r}')
            answer = session.handle_text(live.transcript, use_llm=False)
            if '342' not in answer.reply or answer.hangup:
                raise AssertionError('Wrong answer to interrupted question')
            if not session.handle_text('No', use_llm=False).hangup:
                raise AssertionError('Follow-up did not end the conversation')
            report = {'scope':'Synthetic VITS voice over simulated PCMU ingress; no handset or live PBX playback',
                      'transcript':live.transcript, 'answer':answer.reply,
                      'playback_interrupted':True, 'no_ends_call':True,
                      'question_audio_start_to_interrupt_seconds':round(times['playback_stopped_at']-times['question_audio_started_at'],3),
                      'final_asr_seconds':live.metrics['final_asr_seconds']}
            if pbx:
                playback_path = await asyncio.to_thread(cached_speech, intro.reply, 'en')
                report['pbx'] = await check_pbx_stop(playback_path)
            from app.config import ROOT
            output = ROOT / 'runtime/deployment/barge-in-check.json'
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False, indent=2))
        finally:
            feeder.cancel()
            with suppress(asyncio.CancelledError):
                await feeder


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbx', action='store_true')
    asyncio.run(main(parser.parse_args().pbx))
