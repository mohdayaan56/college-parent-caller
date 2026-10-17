import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from app.rtp_audio import InboundRtp
from scripts.capture_phone_keypad import extract


class PhoneDiagnosticTests(unittest.TestCase):
    def test_bluetooth_capture_extracts_only_valid_rx_and_tx_pcm(self):
        def packet(opcode, payload, index=0, status=0):
            body = struct.pack('<HB', 1 | status << 12, len(payload)) + payload
            return struct.pack('>IIIIQ', len(body), len(body), index << 16 | opcode, 0, 0) + body

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'capture.btsnoop'
            path.write_bytes(struct.pack('>8sII', b'btsnoop\0', 1, 2001)
                             + packet(7, b'\x01\x00' * 24)
                             + packet(6, b'\x02\x00' * 24)
                             + packet(7, b'\x03\x00' * 24, index=1)
                             + packet(7, b'\x04\x00' * 24, status=1)
                             + packet(4, b'\x05\x00' * 24))
            output = Path(directory) / 'audio'
            extract(path, output)
            for label, value in [('rx', 1), ('tx', 2)]:
                with wave.open(str(output.with_suffix('.' + label + '.wav'))) as audio:
                    self.assertEqual((audio.getframerate(), audio.getsampwidth(), audio.getnchannels()),
                                     (8000, 2, 1))
                    self.assertEqual(audio.readframes(24), struct.pack('<h', value) * 24)
            with self.assertRaises(FileExistsError):
                extract(path, output)

    def test_menu_capture_is_opt_in_bounded_and_stops_before_identity(self):
        from app.config import settings
        receiver = InboundRtp()
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, 'recordings_dir', Path(directory)):
            with patch.object(settings, 'phone_menu_diagnostics', False):
                receiver.begin_menu_diagnostics()
                self.assertIsNone(receiver.menu_audio)
            with patch.object(settings, 'phone_menu_diagnostics', True):
                receiver.begin_menu_diagnostics()
                receiver.menu_audio.extend(bytes(35 * 16000 - 160))
                datagram = struct.pack('!BBHII', 128, 0, 1, 0, 99) + bytes([255] * 160)
                receiver.datagram_received(datagram, ('127.0.0.1', 1234))
                self.assertEqual(len(receiver.menu_audio), 35 * 16000)
                report = receiver.finish_menu_diagnostics('menu')
                self.assertEqual(report['captured_seconds'], 35)
                self.assertIsNone(receiver.menu_audio)
                receiver.datagram_received(struct.pack('!BBHII', 128, 0, 2, 160, 99) + bytes([255] * 160),
                                          ('127.0.0.1', 1234))
                self.assertIsNone(receiver.menu_audio)
                with wave.open(report['menu_audio']) as audio:
                    self.assertEqual(audio.getnframes(), 35 * 8000)
