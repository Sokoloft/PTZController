import json
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from ptz_controller.app import create_app
from ptz_controller.engine import Engine, deadzone, defaults, validate
from ptz_controller.visca import command, frame, lens, pantilt, Transport


class PacketTests(unittest.TestCase):
    def test_standard_packets(self):
        self.assertEqual(pantilt(1, -1).hex(), '8101060118140201ff')
        self.assertEqual(pantilt(0, 0).hex(), '8101060101010303ff')
        self.assertEqual(lens('zoom', 1).hex(), '8101040727ff')
        self.assertEqual(lens('focus', -1).hex(), '8101040837ff')
        self.assertEqual(command('preset_recall', 3).hex(), '8101043f0203ff')
        self.assertEqual(command('preset_save', 0).hex(), '8101043f0100ff')
        self.assertEqual(frame(command('home'), 7).hex(), '010000050000000781010604ff')
        self.assertEqual(frame(command('custom','81 09 04 00 FF'), 0, True)[:2], b'\x01\x10')

    def test_deadzone_and_validation(self):
        self.assertEqual(deadzone(.1,.15), 0)
        self.assertAlmostEqual(deadzone(.6,.2), .5)
        self.assertEqual(deadzone(-1,.2), -1)
        for invalid in ['81 01 04 00', '82 01 04 FF', '81 FF 01 FF']:
            with self.assertRaises(ValueError):
                command('custom', invalid)
        with self.assertRaises(ValueError):
            command('preset_recall', 128)
        c = defaults(); c['axes'][0]['deadzone'] = 1
        with self.assertRaises(ValueError):
            validate(c)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name)/'config.json')
        self.engine.connect()
        self.engine.armed = True

    def tearDown(self):
        self.engine.disconnect()
        self.temp.cleanup()

    def test_movement_release_and_no_repeated_packets(self):
        e = self.engine
        e.tick([1,0,0,0], set())
        self.assertGreater(e.model['pan'], 0)
        count = len(e.traffic)
        e.tick([1,0,0,0], set())
        self.assertEqual(len(e.traffic), count)
        e.tick([0,0,0,0], set())
        self.assertEqual(e.motion['pan'], 0)
        self.assertIn('03 03 ff', e.traffic[-1]['message'])

    def test_buttons_edges_and_presets(self):
        e = self.engine
        e.config['bindings'] = [{'source':'button:0','action':'preset_save','param':2},
                                {'source':'button:1','action':'zoom_in'}]
        e.model['pan'] = .5
        e.tick([], {'button:0'})
        e.model['pan'] = -.5
        e.tick([], {'button:0'})
        self.assertEqual(e.model['presets']['2']['pan'], .5)
        e.execute('preset_recall', 2)
        self.assertEqual(e.model['pan'], .5)
        e.tick([], {'button:1'})
        self.assertGreater(e.motion['zoom'], 0)
        e.tick([], set())
        self.assertEqual(e.motion['zoom'], 0)
        e.stop()
        e.tick([1], set())
        self.assertEqual(e.motion['pan'], 0)
        self.assertFalse(e.armed)

    def test_save_reload(self):
        c = defaults(); c['bindings'] = [{'source':'hat:0:up','action':'home'}]
        self.engine.save_preset('Desk', c)
        self.assertIsNone(self.engine.transport)
        loaded = Engine(self.engine.path)
        self.assertEqual(loaded.config, c)

    def test_api(self):
        client = create_app(self.engine).test_client()
        page = client.get('/').get_data(as_text=True)
        token = json.loads(re.search(r'const TOKEN = ("[^"]+")', page).group(1))
        headers = {'X-PTZ-Token': token}
        self.assertEqual(client.post('/api/stop', json={}).status_code, 403)
        self.assertEqual(client.post('/api/arm', json={}, headers=headers).status_code, 200)
        self.assertEqual(client.post('/api/virtual', json={'axes':[float('nan')], 'pressed':[]}, headers=headers).status_code, 400)
        self.assertEqual(client.post('/api/command', json={'action':'custom','param':'bad'}, headers=headers).status_code, 400)
        self.assertEqual(client.post('/api/stop', json={}, headers=headers).status_code, 200)
        self.assertFalse(client.get('/api/state').json['armed'])
        with client.get('/static/app.js') as response:
            self.assertEqual(response.status_code, 200)

    def test_heartbeat_timeout(self):
        e = self.engine
        e.heartbeat = time.monotonic()-5
        e.tick([1], set())
        worker = threading.Thread(target=e.run)
        worker.start()
        deadline = time.monotonic()+2
        while e.armed and time.monotonic() < deadline:
            time.sleep(.02)
        e.quitting.set(); worker.join(3)
        self.assertFalse(e.armed)
        self.assertEqual(e.motion['pan'], 0)


class NetworkTests(unittest.TestCase):
    def test_mock_camera_all_transports(self):
        for mode in ('tcp', 'udp'):
            for framing in ('raw', 'sony'):
                with self.subTest(mode=mode, framing=framing):
                    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM if mode == 'tcp' else socket.SOCK_DGRAM)
                    server.bind(('127.0.0.1', 0)); server.settimeout(2)
                    if mode == 'tcp': server.listen(1)
                    received, failures, logs = [], [], []
                    payload = command('preset_recall', 4)
                    reply = bytes.fromhex('90 41 FF')
                    wire_reply = frame(reply, 0) if framing == 'sony' else reply
                    def camera():
                        try:
                            if mode == 'tcp':
                                conn, _ = server.accept()
                                with conn:
                                    conn.settimeout(2)
                                    expected = len(frame(payload,0) if framing == 'sony' else payload)
                                    data = b''
                                    while len(data) < expected:
                                        part = conn.recv(1024)
                                        if not part: break
                                        data += part
                                    received.append(data)
                                    # Split response to exercise TCP reassembly.
                                    conn.sendall(wire_reply[:2]); time.sleep(.02); conn.sendall(wire_reply[2:])
                                    time.sleep(.1)
                            else:
                                data, peer = server.recvfrom(1024); received.append(data)
                                server.sendto(wire_reply, peer)
                        except Exception as exc:
                            failures.append(exc)
                    worker = threading.Thread(target=camera); worker.start()
                    settings = defaults()['camera']; settings.update(mode=mode, framing=framing, port=server.getsockname()[1])
                    transport = Transport(settings, lambda k,m: logs.append((k,m)))
                    try:
                        transport.send(payload)
                        deadline = time.monotonic()+2
                        while not transport.confirmed and time.monotonic() < deadline:
                            time.sleep(.01)
                        self.assertTrue(transport.confirmed, logs)
                        self.assertIn(('RX', '90 41 ff'), logs)
                    finally:
                        transport.close(); worker.join(3); server.close()
                    self.assertFalse(failures)
                    self.assertEqual(received, [frame(payload,0) if framing == 'sony' else payload])


if __name__ == '__main__':
    unittest.main()
