import asyncio
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.live_stt import GrowingTranscriber, TentativeReply
from app.rtp_audio import InboundRtp, parse_rtp, is_auxiliary, StreamingTap, MediaError
from app.config import settings
from app.conversation import CallState
from app import ari as caller
from tests.support import CsvFixtureMixin, DEMO_PHONE, scratch_dir


def packet(timestamp=0, payload=b'\xff'*160, ssrc=1):
    return struct.pack('!BBHII',128,0,1,timestamp,ssrc)+payload


class RtpTests(unittest.TestCase):
    def test_pcmu_values_and_extension_padding(self):
        p = struct.pack('!BBHII',176,128,2,123,7)+struct.pack('!HH',1,1)+b'1234'+b'\xff\x7f\x00\x80'+b'\0\x02'
        sequence,timestamp,ssrc,pcm = parse_rtp(p)
        self.assertEqual((sequence,timestamp,ssrc),(2,123,7))
        self.assertEqual(struct.unpack('<hhhh',pcm),(0,0,-32124,32124))
        for invalid in (b'',packet()[:11],packet()[0:1]+b'\x08'+packet()[2:],p[:15]):
            with self.assertRaises(ValueError): parse_rtp(invalid)

    def test_duplicate_peer_wraparound_and_gap(self):
        receiver = InboundRtp()
        queue = receiver.arm()
        peer = ('127.0.0.1',9000)
        receiver.datagram_received(packet(2**32-160),peer)
        receiver.datagram_received(packet(0),peer)
        receiver.datagram_received(packet(0),peer)
        receiver.datagram_received(packet(160),('127.0.0.2',9000))
        receiver.datagram_received(packet(160,ssrc=2),peer)
        self.assertEqual(queue.qsize(),2)
        self.assertIsNone(receiver.error)
        receiver.datagram_received(packet(320),peer)
        self.assertEqual(receiver.lost_samples,160)
        self.assertIsNotNone(receiver.error)
        self.assertTrue(is_auxiliary('live-rtp-cleaned-up'))
        self.assertFalse(is_auxiliary('parent-real'))


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def test_breath_during_reply_does_not_become_an_empty_answer_after_reply(self):
        class Endpoint:
            def __init__(self, silence):
                self.samples = 0
                self.reset_activity()
            def reset_activity(self):
                self.voiced = self.voice_run = self.interrupt_run = 0
                self.last_voice = self.samples
            def feed(self, pcm):
                count = len(pcm)//2
                self.samples += count
                if pcm.startswith(b'\x01\0'):
                    self.voiced += count
                    self.voice_run += count
                    self.last_voice = self.samples
                else:
                    self.voice_run = 0
                return self.voiced >= 512 and self.samples-self.last_voice >= 2048
        decode = AsyncMock(return_value={'text': 'What is the total?', 'samples': 100,
                                        'seconds': .01, 'service_metrics': {}})
        tap = StreamingTap('breath-boundary', AsyncMock())
        onset, finished = asyncio.Event(), asyncio.Event()
        session = SimpleNamespace(state=CallState.VERIFIED, transcription_language='en')
        with patch('app.rtp_audio.VoiceEndpoint', Endpoint), \
                patch.object(GrowingTranscriber, '_request', decode):
            task = asyncio.create_task(tap.collect(session, speech_started=onset, playback_finished=finished))
            try:
                while tap.receiver.queue is None:
                    await asyncio.sleep(0)
                queue = tap.receiver.queue
                for block in [b'\x01\0'*256]*12 + [bytes(512)]*12:
                    await queue.put(block)
                    await asyncio.sleep(.001)
                self.assertFalse(onset.is_set())
                finished.set()
                for _ in range(16):
                    await queue.put(bytes(512))
                    await asyncio.sleep(.001)
                self.assertFalse(task.done())
                decode.assert_not_called()
                for block in [b'\x01\0'*256]*12 + [bytes(512)]*12:
                    await queue.put(block)
                    await asyncio.sleep(.001)
                self.assertEqual((await asyncio.wait_for(task, 1)).transcript, 'What is the total?')
            finally:
                task.cancel()
                from contextlib import suppress
                with suppress(asyncio.CancelledError):
                    await task

    async def test_barge_capture_bounds_silence_and_preserves_question_onset(self):
        class Endpoint:
            def __init__(self, silence):
                self.samples = self.last_voice = self.voiced = self.voice_run = self.interrupt_run = 0
            def feed(self, pcm):
                count = len(pcm)//2
                self.samples += count
                if pcm.startswith(b'\x01\0'):
                    self.last_voice = self.samples
                    self.voiced += count
                    self.voice_run += count
                    self.interrupt_run += count
                else:
                    self.voice_run = 0
                    self.interrupt_run = 0
                return self.voiced >= 512 and self.samples-self.last_voice >= 8000
        decoded = []
        async def request(recognizer, snapshot, **kwargs):
            decoded.append(snapshot)
            return {'text':'What are the science marks?', 'samples':len(snapshot)//2,
                    'seconds':0.01, 'service_metrics':{}}
        tap = StreamingTap('test', AsyncMock())
        onset, playback_finished = asyncio.Event(), asyncio.Event()
        session = SimpleNamespace(state=CallState.VERIFIED, transcription_language='en')
        with patch('app.rtp_audio.VoiceEndpoint', Endpoint), \
             patch.object(GrowingTranscriber, '_request', request), \
             patch.object(settings, 'record_max_seconds', 2):
            capture = asyncio.create_task(tap.collect(session, speech_started=onset,
                                                     playback_finished=playback_finished))
            try:
                while tap.receiver.queue is None:
                    await asyncio.sleep(0)
                # Far longer than the bounded turn; silence during playback
                # must neither fill the ASR buffer nor exhaust the timeout.
                for _ in range(300):
                    await tap.receiver.queue.put(b'\0\0'*256)
                    await asyncio.sleep(0)
                self.assertFalse(capture.done())
                self.assertFalse(onset.is_set())
                for _ in range(14):
                    await tap.receiver.queue.put(b'\x01\0'*256)
                    await asyncio.sleep(0)
                # The queued burst and VAD worker must drain on a busy laptop;
                # this checks interruption semantics, not a one-second SLA.
                await asyncio.wait_for(onset.wait(), 3)
                playback_finished.set()
                for _ in range(32):
                    await tap.receiver.queue.put(b'\0\0'*256)
                    await asyncio.sleep(0)
                live = await asyncio.wait_for(capture, 1)
                self.assertEqual(live.transcript, 'What are the science marks?')
                self.assertIn(b'\x01\0'*1536, live.pcm)
                self.assertLessEqual(len(live.pcm), 32000)
                self.assertIsNone(tap.receiver.queue)
            finally:
                capture.cancel()
                from contextlib import suppress
                with suppress(asyncio.CancelledError):
                    await capture

    async def test_language_menu_restricts_latin_without_changing_language(self):
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'language':'en','text':'Hindi'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            recognizer = GrowingTranscriber('en',client=client,constrain_script=True)
            await recognizer._request(b'\0\0'*800)
        self.assertEqual(requests[0].url.params['language'],'en')
        self.assertEqual(requests[0].url.params['constrain_script'],'true')
    async def test_selected_indic_language_constrains_live_and_final_decoding(self):
        for language in ('en', 'hi', 'kn'):
            requests = []
            def respond(request):
                requests.append(request)
                return httpx.Response(200, json={'language':language, 'text':'yes'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                recognizer = GrowingTranscriber(language, client=client)
                await recognizer._request(b'\0\0' * 800, final=False)
                await recognizer._request(b'\0\0' * 1600, final=True)
            self.assertEqual(len(requests), 2)
            for request in requests:
                self.assertEqual(request.url.params['constrain_script'],
                                 'true')

    async def test_finishing_idle_worker_does_not_launch_a_partial(self):
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'language':'en','text':'final'})))
        async with client:
            recognizer=GrowingTranscriber('en',minimum=.02,step=.02,client=client)
            await recognizer.start()
            recognizer.feed(b'\x01\0'*320,allow_partial=False)
            _,_,metrics=await recognizer.finish()
            self.assertEqual(metrics['partial_count'],0)
            self.assertFalse(metrics['final_snapshot_reused'])
            await recognizer.close()

    async def test_coalesces_and_final_changed_question_is_decoded(self):
        entered, release = asyncio.Event(),asyncio.Event()
        requests=[]
        async def respond(request):
            requests.append(request.content)
            if len(requests)==1:
                entered.set()
                await release.wait()
            return httpx.Response(200,json={'language':'en','text':'partial' if len(requests)==1 else 'final'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            recognizer=GrowingTranscriber('en',minimum=.02,step=.02,client=client)
            await recognizer.start()
            recognizer.feed(b'\x01\0'*160)
            await entered.wait()
            for _ in range(5):recognizer.feed(b'\x02\0'*160)
            final=asyncio.create_task(recognizer.finish())
            await asyncio.sleep(.02)
            self.assertFalse(final.done())
            release.set()
            pcm,text,metrics=await final
            self.assertEqual(text,'final')
            self.assertEqual(len(requests),2)
            self.assertEqual(requests[-1][44:],pcm)
            self.assertFalse(metrics['final_snapshot_reused'])
            self.assertGreater(metrics['final_wait_for_partial_seconds'],.01)
            await recognizer.close()

    async def test_exact_snapshot_reused_without_second_request(self):
        partial=asyncio.Event()
        async def callback(text): partial.set()
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'language':'hi','text':'हाँ'})))
        async with client:
            recognizer=GrowingTranscriber('hi',minimum=.02,step=.02,client=client,on_partial=callback)
            await recognizer.start()
            recognizer.feed(b'\x01\0'*160)
            await partial.wait()
            _,text,metrics=await recognizer.finish()
            self.assertEqual(text,'हाँ')
            self.assertTrue(metrics['final_snapshot_reused'])
            await recognizer.close()

    async def test_bad_partial_does_not_prevent_final_and_busy_retry(self):
        count=0
        async def respond(request):
            nonlocal count
            count+=1
            if count==1:return httpx.Response(200,content=b'bad json')
            if count==2:return httpx.Response(429)
            return httpx.Response(200,json={'language':'kn','text':'ಹೌದು'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            recognizer=GrowingTranscriber('kn',minimum=.02,step=.02,client=client)
            await recognizer.start()
            recognizer.feed(b'\x01\0'*160)
            while recognizer.last_error is None: await asyncio.sleep(.001)
            _,text,metrics=await recognizer.finish()
            self.assertEqual(text,'ಹೌದು')
            self.assertEqual(count,3)
            await recognizer.close()

    async def test_unverified_partial_cannot_prepare_or_commit_identity(self):
        session=SimpleNamespace(state=CallState.AWAITING_NAME,verify_attempts=0)
        draft=TentativeReply(session)
        await draft.update('yes I am the parent')
        self.assertIsNone(draft.task)
        self.assertEqual(session.verify_attempts,0)
        self.assertEqual(session.state,CallState.AWAITING_NAME)
        await draft.close()

    async def test_hangup_disarms_buffer_and_setup_failure_cleans_resources(self):
        ari=AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda:None))
        tap=StreamingTap('parent',ari,alive=lambda:False)
        fake_endpoint=SimpleNamespace(voiced=0)
        with patch('app.rtp_audio.VoiceEndpoint',return_value=fake_endpoint):
            with self.assertRaises(MediaError):
                await tap.collect(SimpleNamespace(transcription_language='en'))
        self.assertIsNone(tap.receiver.queue)
        ari.side_effect=RuntimeError('setup failed')
        with patch.object(settings,'streaming_audio_bind_host','127.0.0.1'):
            with self.assertRaises(RuntimeError):await tap.open()
        self.assertIsNone(tap.transport)
        self.assertGreaterEqual(ari.await_count,4)

    async def test_capture_error_asks_repeat_without_committing_identity(self):
        channel='test-live-privacy'
        session=SimpleNamespace(session_id='test',language='en',state=CallState.AWAITING_NAME,
            greeting=lambda:SimpleNamespace(hangup=False),handle_text=AsyncMock())
        tap=SimpleNamespace(collect=AsyncMock(side_effect=MediaError('gap')),close=AsyncMock())
        async def reply(*args): caller._dead_channels.add(channel)
        with scratch_dir() as root, patch.object(settings,'recordings_dir',root), \
             patch.object(caller,'_answer',new=AsyncMock()), \
             patch.object(caller,'prepare_greeting',return_value=root/'fake.wav'), \
             patch.object(caller,'_play',new=AsyncMock()), \
             patch.object(caller,'_play_reply',new=reply):
            try:await caller._handle_parent_turns(channel,session,tap)
            finally:caller._dead_channels.discard(channel)
        session.handle_text.assert_not_called()
        tap.close.assert_awaited_once()


class DraftTests(CsvFixtureMixin,unittest.IsolatedAsyncioTestCase):
    async def test_revised_partial_changes_only_draft_and_keeps_real_session(self):
        from app.conversation import create_session
        from copy import deepcopy
        session=create_session(DEMO_PHONE)
        session.greeting()
        session.handle_text('yes',use_llm=False)
        self.assertEqual(session.state,CallState.VERIFIED)
        before=deepcopy(session)
        drafts=TentativeReply(session)
        with patch('app.conversation.rewrite_intent_reply',return_value='draft') as prepare, \
             patch('app.audio_cache.valid_audio',return_value=False):
            await drafts.update('total marks')
            await drafts.task
            await drafts.update('marks in science instead')
            await drafts.task
            self.assertEqual([c.args[1]['intent'] for c in prepare.call_args_list],['total','subject'])
            self.assertTrue(all(c.kwargs['cached_only'] for c in prepare.call_args_list))
        self.assertEqual(session,before)
        await drafts.close()


if __name__=='__main__': unittest.main()
