"""Bounded private btmon capture and extraction of only CVSD host PCM audio."""
import argparse
from pathlib import Path
import os
import struct
import subprocess
from uuid import uuid4
import wave


def extract(path, output):
    streams = {6:bytearray(),7:bytearray()}
    counts = {6:0,7:0}
    with path.open('rb') as source:
        signature,version,link = struct.unpack('>8sII',source.read(16))
        if (signature,version,link) != (b'btsnoop\0',1,2001):
            raise ValueError('Expected BlueZ monitor btsnoop format')
        while header := source.read(24):
            if len(header) != 24:
                break
            _,length,flags,_,_ = struct.unpack('>IIIIQ',header)
            if length > 65535:
                raise ValueError('Invalid packet length')
            packet = source.read(length)
            if len(packet) != length:
                break
            opcode = flags & 65535
            if flags >> 16 or opcode not in streams or len(packet) < 3:
                continue
            handle,size = struct.unpack('<HB',packet[:3])
            if (handle >> 12) & 3 or len(packet) != size+3 or size % 2:
                continue
            streams[opcode].extend(packet[3:])
            counts[opcode] += 1
    for opcode,label in ((7,'rx'),(6,'tx')):
        target = output.with_suffix('.'+label+'.wav')
        if target.exists():
            raise FileExistsError(target)
        with wave.open(str(target),'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(streams[opcode])
        target.chmod(0o600)
        print(label,'packets',counts[opcode],'seconds',round(len(streams[opcode])/16000,3),'file',target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extract',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--seconds',type=int,default=100)
    args = parser.parse_args()
    if args.extract:
        extract(args.extract,args.output or args.extract)
        return
    if not 30 <= args.seconds <= 120:
        raise ValueError('Capture duration must be bounded to 30..120 seconds')
    os.umask(0o077)
    directory = Path('/opt/college-phone-diagnostics')
    directory.mkdir(parents=True,exist_ok=True)
    directory.chmod(0o700)
    path = directory/('keypad-'+uuid4().hex+'.btsnoop')
    print('Starting private bounded capture:',path,flush=True)
    result = subprocess.run(['timeout',str(args.seconds),'btmon','-i','0','-S','-w',str(path)],
                            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    if result.returncode not in {0,124}:
        raise RuntimeError('Bluetooth monitor failed')
    print('Capture finished:',path,flush=True)


if __name__ == '__main__':
    main()
