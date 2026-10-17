"""WinMM loopback capture for checking prerecorded audio routed to Stereo Mix."""
import ctypes
from ctypes import wintypes
import math
import struct
import time
import wave
import winsound
from contextlib import contextmanager


@contextmanager
def stereo_mix_input():
    from pycaw.pycaw import AudioUtilities
    devices = [device for device in AudioUtilities.GetAllDevices(device_state=1)
               if 'stereo mix' in device.FriendlyName.lower()]
    if len(devices) != 1:
        raise RuntimeError('Expected one active Stereo Mix input')
    endpoint = devices[0].EndpointVolume
    original_mute = endpoint.GetMute()
    original_level = endpoint.GetMasterVolumeLevelScalar()
    try:
        endpoint.SetMute(0, None)
        # Realtek Stereo Mix at 100% adds enough gain to clip these voices.
        endpoint.SetMasterVolumeLevelScalar(0.5, None)
        yield endpoint
    finally:
        endpoint.SetMasterVolumeLevelScalar(original_level, None)
        endpoint.SetMute(original_mute, None)


class Caps(ctypes.Structure):
    _fields_ = [('mid', wintypes.WORD), ('pid', wintypes.WORD),
                ('version', wintypes.DWORD), ('name', wintypes.WCHAR * 32),
                ('formats', wintypes.DWORD), ('channels', wintypes.WORD),
                ('reserved', wintypes.WORD)]


class Format(ctypes.Structure):
    _fields_ = [('tag', wintypes.WORD), ('channels', wintypes.WORD),
                ('rate', wintypes.DWORD), ('bytes_per_second', wintypes.DWORD),
                ('align', wintypes.WORD), ('bits', wintypes.WORD), ('size', wintypes.WORD)]


class Header(ctypes.Structure):
    _fields_ = [('data', ctypes.c_void_p), ('length', wintypes.DWORD),
                ('recorded', wintypes.DWORD), ('user', ctypes.c_size_t),
                ('flags', wintypes.DWORD), ('loops', wintypes.DWORD),
                ('next', ctypes.c_void_p), ('reserved', ctypes.c_size_t)]


def capture_playback(source, destination):
    mm = ctypes.WinDLL('winmm')
    devices = []
    for index in range(mm.waveInGetNumDevs()):
        caps = Caps()
        mm.waveInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps))
        if 'stereo mix' in caps.name.lower():
            devices.append(index)
    if not devices:
        raise RuntimeError('Stereo Mix capture device is unavailable')
    with wave.open(str(source), 'rb') as wav:
        seconds = wav.getnframes() / wav.getframerate()
    buffer = ctypes.create_string_buffer(int((seconds + 1.2) * 32000))
    header = Header(ctypes.addressof(buffer), len(buffer), 0, 0, 0, 0, None, 0)
    fmt = Format(1, 1, 16000, 32000, 2, 16, 0)
    handle = ctypes.c_void_p()
    mm.waveInOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT,
                             ctypes.POINTER(Format), ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    for name in ('waveInPrepareHeader', 'waveInAddBuffer', 'waveInUnprepareHeader'):
        getattr(mm, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(Header), wintypes.UINT]
    for name in ('waveInStart', 'waveInStop', 'waveInReset', 'waveInClose'):
        getattr(mm, name).argtypes = [ctypes.c_void_p]
    def check(code):
        if code:
            raise RuntimeError(f'Windows audio capture failed with WinMM code {code}')
    check(mm.waveInOpen(ctypes.byref(handle), devices[0], ctypes.byref(fmt), 0, 0, 0))
    prepared = False
    try:
        check(mm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header)))
        prepared = True
        check(mm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header)))
        check(mm.waveInStart(handle))
        time.sleep(.2)
        winsound.PlaySound(str(source), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        time.sleep(.4)
        check(mm.waveInStop(handle))
        check(mm.waveInReset(handle))
        pcm = buffer.raw[:header.recorded]
        with wave.open(str(destination), 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm)
        samples = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
        rms = math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples)))
        return {'device': 'Stereo Mix', 'seconds': len(pcm) / 32000,
                'rms': round(rms, 2), 'peak': max((abs(sample) for sample in samples), default=0),
                'clipped_fraction': sum(abs(sample) >= 32760 for sample in samples) / max(1, len(samples))}
    finally:
        mm.waveInReset(handle)
        if prepared:
            mm.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
        mm.waveInClose(handle)
