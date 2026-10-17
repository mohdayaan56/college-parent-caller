"""Receive the inbound-only Asterisk snoop as PCMU RTP on a private bridge."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
import struct
import wave
from time import monotonic
from uuid import uuid4

import numpy as np

from app.config import settings
from app.live_stt import GrowingTranscriber, TentativeReply

AUXILIARY_CHANNELS = set()
AUXILIARY_PARENTS = {}


def is_auxiliary(channel_id):
    # End events can arrive after cleanup removes the registration.
    return channel_id in AUXILIARY_CHANNELS or str(channel_id).startswith(('live-snoop-', 'live-rtp-'))


class MediaError(RuntimeError):
    pass


def _ulaw_table():
    code = np.bitwise_not(np.arange(256, dtype=np.uint8)).astype(np.int32)
    magnitude = (((code & 15) << 3) + 132) << ((code >> 4) & 7)
    return np.where(code & 128, 132-magnitude, magnitude-132).astype('<i2')


ULAW = _ulaw_table()


def parse_rtp(packet):
    if len(packet) < 12 or packet[0] >> 6 != 2 or packet[1] & 127 != 0:
        raise ValueError('Expected PCMU RTP version 2')
    offset = 12+4*(packet[0] & 15)
    if len(packet) < offset:
        raise ValueError('Truncated RTP CSRC list')
    if packet[0] & 16:
        if len(packet) < offset+4:
            raise ValueError('Truncated RTP extension')
        offset += 4+4*struct.unpack_from('!H',packet,offset+2)[0]
    end = len(packet)
    if packet[0] & 32:
        padding = packet[-1]
        if not padding or padding > end-offset:
            raise ValueError('Invalid RTP padding')
        end -= padding
    if offset >= end or end-offset > 1600:
        raise ValueError('Invalid RTP payload length')
    sequence, timestamp, ssrc = struct.unpack_from('!HII',packet,2)
    return sequence, timestamp, ssrc, ULAW[np.frombuffer(packet[offset:end],dtype=np.uint8)].tobytes()


class InboundRtp(asyncio.DatagramProtocol):
    def __init__(self, on_digit=None):
        from app.dtmf import ToneDigits
        self.tones = ToneDigits(on_digit) if on_digit else None
        self.transport = None
        self.peer = None
        self.ssrc = None
        self.timestamp = None
        self.queue = None
        self.packets = 0
        self.lost_samples = 0
        self.error = None
        self.payload_types = {}
        self.menu_audio = None
        self.menu_packet_start = 0
        self.menu_payload_start = {}

    def begin_menu_diagnostics(self):
        self.menu_packet_start = self.packets
        self.menu_payload_start = dict(self.payload_types)
        self.menu_audio = bytearray() if settings.phone_menu_diagnostics else None

    def finish_menu_diagnostics(self, name):
        report = {'rtp_packets':self.packets-self.menu_packet_start,
                  'rtp_payload_types':{str(key):count-self.menu_payload_start.get(key,0)
                                       for key,count in self.payload_types.items()}}
        audio,self.menu_audio = self.menu_audio,None
        if audio:
            directory = settings.recordings_dir/'phone-diagnostics'
            directory.mkdir(parents=True,exist_ok=True)
            path = directory/(name+'.wav')
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(audio)
            path.chmod(0o600)
            report['menu_audio'] = str(path)
            report['captured_seconds'] = len(audio)/16000
        return report

    def connection_made(self, transport):
        self.transport = transport

    def arm(self):
        self.queue = asyncio.Queue(maxsize=750)  # <=15 s of typical 20 ms packets
        self.lost_samples = 0
        self.error = None
        self.timestamp = None  # silence outside a turn is not lost question audio
        return self.queue

    def disarm(self):
        self.queue = None

    def error_received(self, error):
        self.error = 'Incoming RTP socket failed'

    def datagram_received(self, data, address):
        if self.peer == address and len(data) >= 12 and data[0] >> 6 == 2:
            payload_type = data[1] & 127
            self.payload_types[payload_type] = self.payload_types.get(payload_type,0)+1
        try:
            _sequence, timestamp, ssrc, pcm = parse_rtp(data)
        except ValueError:
            return
        if self.peer is None:
            self.peer, self.ssrc = address, ssrc
        if address != self.peer or ssrc != self.ssrc:
            return
        gap = 0 if self.timestamp is None else (timestamp-self.timestamp+2**31)%2**32-2**31
        if gap < 0:  # duplicated or late packet; do not append it twice
            return
        self.timestamp = (timestamp+len(pcm)//2)%2**32
        self.packets += 1
        if self.menu_audio is not None:
            remaining = 35*16000-len(self.menu_audio)
            self.menu_audio.extend(pcm[:max(0,remaining)])
        if self.tones:
            if gap:
                self.tones.reset()
            self.tones.feed(pcm)
        if self.queue is None:
            return
        if gap:
            self.lost_samples += gap
            self.error = 'RTP gap; repeat the question'
        try:
            self.queue.put_nowait(pcm)
        except asyncio.QueueFull:
            self.error = 'Live audio receiver could not keep up'


class VoiceEndpoint:
    def __init__(self, silence_seconds):
        from silero_vad import load_silero_vad
        self.model = load_silero_vad(onnx=True)
        self.pending = bytearray()
        self.samples = 0
        self.last_voice = 0
        self.voiced = 0
        self.voice_run = 0
        self.interrupt_run = 0
        self.silence_samples = int(silence_seconds*8000)

    def reset_activity(self):
        self.voiced = self.voice_run = self.interrupt_run = 0
        self.last_voice = self.samples

    def feed(self, pcm):
        import torch
        self.pending.extend(pcm)
        while len(self.pending) >= 512:  # 256 samples / 32 ms, Silero 8 kHz input
            block = bytes(self.pending[:512])
            del self.pending[:512]
            audio = np.frombuffer(block,dtype='<i2').astype(np.float32)/32768
            probability = float(self.model(torch.from_numpy(audio),8000).item())
            self.samples += 256
            self.interrupt_run = (self.interrupt_run + 256
                                  if probability >= settings.barge_in_vad_threshold else 0)
            if probability >= 0.35:
                self.last_voice = self.samples
                self.voiced += 256
                self.voice_run += 256
            else:
                self.voice_run = 0
        return self.voiced >= 512 and self.samples-self.last_voice >= self.silence_samples


def warm_endpoint():
    # Import Torch and validate the bundled VAD before accepting a call.
    VoiceEndpoint(settings.streaming_endpoint_seconds)


@dataclass
class LiveTurn:
    pcm: bytes
    transcript: str
    metrics: dict


class StreamingTap:
    def __init__(self, channel_id, ari_request, *, alive=lambda:True, on_digit=None):
        self.channel_id = channel_id
        self.ari = ari_request
        self.alive = alive
        suffix = uuid4().hex
        self.snoop = 'live-snoop-'+suffix
        self.external = 'live-rtp-'+suffix
        self.bridge = 'live-bridge-'+suffix
        self.transport = None
        self.receiver = InboundRtp(on_digit=on_digit)
        self.port = None

    async def open(self):
        loop = asyncio.get_running_loop()
        for port in range(settings.streaming_audio_port, settings.streaming_audio_port+settings.streaming_audio_ports):
            try:
                self.transport, _ = await loop.create_datagram_endpoint(lambda:self.receiver,
                    local_addr=(settings.streaming_audio_bind_host,port))
                self.port = port
                break
            except OSError:
                continue
        if self.transport is None:
            raise MediaError('No private streaming media port available')
        AUXILIARY_CHANNELS.update((self.snoop,self.external))
        AUXILIARY_PARENTS.update({self.snoop:self.channel_id,self.external:self.channel_id})
        try:
            for path, params in (
                (f'/bridges/{self.bridge}', {'type':'mixing,proxy_media'}),
                (f'/channels/{self.channel_id}/snoop/{self.snoop}',
                 {'app':settings.ari_app,'spy':'in','whisper':'none','appArgs':'live-audio-internal'}),
                ('/channels/externalMedia', {'app':settings.ari_app,'channelId':self.external,
                 'external_host':f'{settings.streaming_audio_advertise_host}:{self.port}',
                 'format':'ulaw','transport':'udp','encapsulation':'rtp','direction':'both'}),
                (f'/bridges/{self.bridge}/addChannel', {'channel':f'{self.snoop},{self.external}'}),
            ):
                response = await self.ari('POST',path,params=params)
                response.raise_for_status()
        except BaseException:
            await self.close()
            raise

    async def collect(self, session, *, speech_started=None, playback_finished=None):
        from app.conversation import CallState
        drafts = TentativeReply(session)
        recognizer = GrowingTranscriber(session.transcription_language,on_partial=drafts.update,
            language_menu=(getattr(session,'state',None) == CallState.AWAITING_LANGUAGE),
            constrain_script=(getattr(session,'state',None) == CallState.AWAITING_LANGUAGE
                              or session.transcription_language in {'en','hi','kn'}))
        from app.barge_in import SpeechInterruptGuard
        guard = SpeechInterruptGuard(recognizer._request) if speech_started is not None else None
        queue = self.receiver.arm()
        started = monotonic()
        last_packet_at = started
        speech_end = None
        endpoint_reason = 'duration_limit'
        speech_at = None
        listening_at = None
        try:
            # Arm first: retain audio arriving during the small VAD model load.
            silence_seconds = (.45 if getattr(session,'state',None) == CallState.AWAITING_LANGUAGE
                               else .65 if getattr(session,'state',None) == CallState.AWAITING_NAME
                               else settings.streaming_endpoint_seconds)
            endpoint = await asyncio.to_thread(VoiceEndpoint,silence_seconds)
            await recognizer.start()
            while len(recognizer.buffer) < settings.record_max_seconds*16000:
                if not self.alive():
                    raise MediaError('Parent channel hung up')
                now = monotonic()
                if playback_finished is None or playback_finished.is_set():
                    listening_at = listening_at or now
                # A long reply must not consume the parent's answer timeout.
                deadline_start = speech_at or listening_at
                remaining = (settings.record_max_seconds-(now-deadline_start)
                             if deadline_start is not None else 0.1)
                if remaining <= 0:
                    break
                try:
                    pcm = await asyncio.wait_for(queue.get(),min(0.1,remaining))
                except asyncio.TimeoutError:
                    if monotonic()-last_packet_at > 2.0:
                        raise MediaError('Incoming RTP stalled')
                    continue
                last_packet_at = monotonic()
                if self.receiver.error:
                    raise MediaError(self.receiver.error)
                limit = settings.record_max_seconds*16000-len(recognizer.buffer)
                pcm = pcm[:limit]
                ended = endpoint.feed(pcm)
                if speech_started is not None and not speech_started.is_set():
                    playing = playback_finished is not None and not playback_finished.is_set()
                    if playing:
                        rejected = guard.rejected
                        if not guard.feed(pcm, endpoint, ended):
                            if guard.rejected != rejected:
                                endpoint.reset_activity()
                            continue
                        recognizer.feed(bytes(guard.audio), allow_partial=False)
                        pcm = b''  # The current packet is already in the guarded prefix.
                    else:
                        if not guard.candidate and not endpoint.voice_run:
                            # Breath/noise during playback is not a new answer
                            # once playback ends. Keep the pre-roll for a word
                            # arriving at the boundary, but clear stale VAD evidence.
                            endpoint.reset_activity()
                            ended = False
                        recognizer.feed(bytes(guard.audio), allow_partial=False)
                    speech_at = monotonic() if endpoint.voiced else None
                    speech_started.set()
                    await guard.close()
                # Silence and the initial wait are not useful partial speech.
                # Never launch a late prefix job immediately before final ASR.
                active = endpoint.samples-endpoint.last_voice <= 256
                allow_partial = (active and not ended and
                    endpoint.voiced >= settings.streaming_partial_min_seconds*8000)
                pcm = pcm[:max(0, settings.record_max_seconds*16000-len(recognizer.buffer))]
                recognizer.feed(pcm,allow_partial=allow_partial)
                if ended:
                    speech_end = started+endpoint.last_voice/8000
                    endpoint_reason = 'voice_silence'
                    break
            self.receiver.disarm()
            if not recognizer.buffer or not endpoint.voiced:
                raise MediaError('No speech detected; ask the parent to repeat')
            sealed_at = monotonic()
            pcm, transcript, metrics = await recognizer.finish()
            metrics.update(endpoint_reason=endpoint_reason,
                recorded_audio_seconds=len(pcm)/16000,
                streaming_endpoint_seconds=silence_seconds,
                recording_finished_at=sealed_at, last_voice_at=speech_end,
                stream_capture_seconds=sealed_at-started,
                speech_end_to_final_text_seconds=(monotonic()-speech_end if speech_end else None),
                partial_drafts=drafts.drafts, rtp_lost_samples=self.receiver.lost_samples)
            if guard:
                metrics.update(barge_in_asr_probes=guard.total_probes,
                               barge_in_rejected_candidates=guard.rejected,
                               barge_in_probe_error=guard.error)
            return LiveTurn(pcm,transcript,metrics)
        finally:
            self.receiver.disarm()
            await recognizer.close()
            if guard:
                await guard.close()
            await drafts.close()

    async def close(self):
        self.receiver.disarm()
        self.receiver.menu_audio = None
        AUXILIARY_PARENTS.pop(self.snoop,None)
        AUXILIARY_PARENTS.pop(self.external,None)
        if self.transport:
            self.transport.close()
            self.transport = None
        for resource in (f'/channels/{self.snoop}',f'/channels/{self.external}',f'/bridges/{self.bridge}'):
            with suppress(Exception):
                await self.ari('DELETE',resource)
        AUXILIARY_CHANNELS.difference_update((self.snoop,self.external))
