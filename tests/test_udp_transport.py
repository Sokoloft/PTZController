import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

from ptz_controller.visca import open_transport, pantilt, lens, command
from ptz_controller.engine import Engine


class CameraEmulator:
    def __init__(self, sony):
        self.sony = sony
        self.packets = []
        self.closed = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(.05)
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self):
        while not self.closed.is_set():
            try:
                data, peer = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            self.packets.append((time.monotonic(), data))
            if self.sony:
                kind, size, seq = struct.unpack('!HHI', data[:8])
                payload = data[8:]
                if kind == 0x0200:
                    response = struct.pack('!HHI', 0x0201, 1, seq) + b'\x01'
                    self.sock.sendto(response, peer)
                    continue
            else:
                payload = data
            # Simulate an unsupported focus stop, then accept subsequent commands.
            reply = b'\x90\x60\x02\xff' if payload == lens('focus', 0) else b'\x90\x41\xff'
            if self.sony:
                reply = struct.pack('!HHI', 0x0111, len(reply), seq) + reply
            self.sock.sendto(reply, peer)

    def close(self):
        self.closed.set()
        self.thread.join(1)
        self.sock.close()


class UDPTests(unittest.TestCase):
    def test_raw_and_sony_rejection_does_not_disable_control(self):
        for sony in (False, True):
            with self.subTest(sony=sony):
                camera = CameraEmulator(sony)
                logs = []
                transport = open_transport(dict(mode='udp', framing='sony' if sony else 'raw',
                                                host='127.0.0.1', port=camera.sock.getsockname()[1],
                                                address=1), lambda *event: logs.append(event))
                try:
                    transport.stop()
                    transport.send(command('preset_recall', 1))
                    deadline = time.monotonic() + 2
                    while len(camera.packets) < (5 if sony else 4) and time.monotonic() < deadline:
                        time.sleep(.01)
                    transport.close()
                    transport.drain_logs()
                    wire = [data[8:] if sony else data for _, data in camera.packets]
                    self.assertIn(pantilt(0, 0), wire)
                    self.assertIn(command('preset_recall', 1), wire)
                    self.assertTrue(transport.confirmed)
                    self.assertEqual(transport.error, '')
                    self.assertTrue(any('syntax error' in message and '81 01 04 08 00 ff' in message
                                        for kind, message in logs if kind == 'ERROR'))
                    times = [stamp for stamp, _ in camera.packets]
                    self.assertTrue(all(b-a >= .045 for a, b in zip(times, times[1:])))
                    if sony:
                        self.assertEqual(camera.packets[0][1], bytes.fromhex('02 00 00 01 00 00 00 00 01'))
                        self.assertEqual(int.from_bytes(camera.packets[1][1][4:8], 'big'), 0)
                finally:
                    if transport.worker.is_alive():
                        transport.close()
                    camera.close()

    def test_wire_speed_quantization_suppresses_duplicate_packets(self):
        engine = Engine('/tmp/ptz-test-nonexistent-config.json')
        engine.armed = True
        engine.transport = type('Recorder', (), {'send': lambda self, data: sent.append(data)})()
        sent = []
        with patch('ptz_controller.engine.time.monotonic', side_effect=[1., 2., 3.]):
            engine.tick([.5, 0, 0, 0], set())
            sent.clear()
            engine.tick([.501, 0, 0, 0], set())
            self.assertEqual(sent, [])
            engine.tick([0, 0, 0, 0], set())
            self.assertEqual(sent, [pantilt(0, 0)])


if __name__ == '__main__':
    unittest.main()
