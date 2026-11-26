import json
import re
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from ptz_controller.app import create_app
from ptz_controller.controllers import ControllerReader
from ptz_controller.engine import Engine, defaults
from ptz_controller.package_camera import PackageTransport
from ptz_controller.visca import command, lens, open_transport, pantilt


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out waiting for worker')
        time.sleep(.01)


class PackageTests(unittest.TestCase):
    def test_worker_stop_cancels_backlog(self):
        started, release = threading.Event(), threading.Event()
        camera = MagicMock(received=False)
        forward = pantilt(1, 0)

        def send(payload):
            if payload == forward:
                started.set()
                release.wait(3)
        camera.send_payload.side_effect = send
        with patch('ptz_controller.package_camera.WireCamera', return_value=camera):
            t = PackageTransport(defaults()['camera'], lambda *_: None)
            try:
                t.send(forward); self.assertTrue(started.wait(2))
                t.send(pantilt(-1, 0)); t.send(lens('zoom', 1))
                t.send(command('preset_recall', 4))
                t.stop(); release.set()
                until(lambda: camera.send_payload.call_count >= 4)
            finally:
                release.set(); t.close()
            sent = [call.args[0] for call in camera.send_payload.call_args_list]
            self.assertEqual(sent[0], forward)
            self.assertNotIn(pantilt(-1, 0), sent)
            self.assertNotIn(lens('zoom', 1), sent)
            self.assertNotIn(command('preset_recall', 4), sent)
            self.assertIn(lens('zoom', 0), sent)
            camera.close_connection.assert_called_once()
            self.assertFalse(t.worker.is_alive())

    def test_queued_udp_loopback(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0)); server.settimeout(.1)
        received, failures, logs = [], [], []
        finished = threading.Event()
        def camera_server():
            while not finished.is_set():
                try:
                    data, peer = server.recvfrom(1024)
                    received.append(data)
                    if data[:2] == b'\x02\x00':
                        reply = b'\x02\x01\x00\x01' + data[4:8] + b'\x01'
                    else:
                        payload = (b'\x90\x50\xff' if data[9] == 9 else b'\x90\x41\xff')
                        reply = b'\x01\x11\x00\x03' + data[4:8] + payload
                    server.sendto(reply, peer)
                except socket.timeout:
                    continue
                except Exception as exc:
                    if not finished.is_set(): failures.append(exc)
                    break
        worker = threading.Thread(target=camera_server); worker.start()
        settings = defaults()['camera']; settings.update(mode='udp', port=server.getsockname()[1])
        t = open_transport(settings, lambda k,m: logs.append((k,m)))
        try:
            self.assertIsInstance(t, PackageTransport)
            for payload in (pantilt(.5, -.5), lens('zoom', 1), lens('focus', -1),
                            command('preset_save', 2), command('preset_recall', 2),
                            command('home'), command('autofocus'),
                            command('custom', '81 09 04 47 FF')):
                t.send(payload)
                until(lambda: any(d[8:] == payload for d in received))
            until(lambda: t.confirmed)
            t.reset_sequence()
            until(lambda: sum(d[:2] == b'\x02\x00' for d in received) >= 2)
            self.assertFalse(t.error, t.error)
            t.drain_logs()
            self.assertTrue(any(k == 'RX' for k, _ in logs))
        finally:
            t.close(); finished.set(); worker.join(2); server.close()
        self.assertFalse(failures)
        self.assertFalse(t.worker.is_alive())
        # Stop is flushed before the package socket is closed.
        self.assertIn(bytes.fromhex('81 01 04 07 00 FF'), [d[8:] for d in received])


class ControllerTests(unittest.TestCase):
    def test_discovery_reading_and_stable_id(self):
        # Limit the mock to actual pygame Joystick methods, not Controller APIs.
        joy = MagicMock(spec=['get_instance_id', 'get_guid', 'get_name', 'get_numaxes',
                              'get_numbuttons', 'get_numhats', 'get_axis', 'get_button', 'get_hat'])
        joy.get_instance_id.return_value = 8
        joy.get_guid.return_value = 'test-guid'
        joy.get_name.return_value = 'Test gamepad'
        joy.get_numaxes.return_value = 2
        joy.get_numbuttons.return_value = 2
        joy.get_numhats.return_value = 1
        joy.get_axis.side_effect = lambda i: [.8, -.4][i]
        joy.get_button.side_effect = lambda i: [0, 1][i]
        joy.get_hat.return_value = (1, 1)
        pygame = SimpleNamespace(joystick=SimpleNamespace(get_count=lambda: 1, Joystick=lambda i: joy))
        reader = ControllerReader(pygame); reader.scan(); reader.scan()
        self.assertEqual(reader.read('test-guid:0'), ([.8, -.4], {'button:1', 'hat:0:right', 'hat:0:up'}))
        self.assertEqual(reader.read('8'), reader.read('test-guid:0'))
        joy.get_instance_id.return_value = 12
        reader.scan()
        self.assertEqual(reader.devices[0]['id'], 'test-guid:0')
        pygame.joystick.get_count = lambda: 0
        reader.scan()
        self.assertIsNone(reader.read('test-guid:0'))

    def test_selection_immediately_updates_disarmed_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Engine(Path(tmp)/'config.json')
            engine.devices = [{'id':'test-guid:0', 'name':'Test', 'axes':2, 'buttons':1, 'hats':0}]
            engine.connect(); engine.armed = True
            app = create_app(engine); client = app.test_client()
            token = json.loads(re.search(r'const TOKEN = ("[^"]+")', client.get('/').get_data(as_text=True)).group(1))
            try:
                response = client.post('/api/controller', json={'controller':'test-guid:0'}, headers={'X-PTZ-Token':token})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(engine.config['controller'], 'test-guid:0')
                self.assertFalse(engine.armed)
                self.assertIsNotNone(engine.transport)
                engine.tick([.8, -.4], {'button:0'})
                state = client.get('/api/state').json
                self.assertEqual(state['input'], {'axes':[.8, -.4], 'pressed':['button:0']})
                self.assertEqual(state['motion']['pan'], 0)
                self.assertFalse(engine.path.exists())  # selection does not need a Save round-trip
            finally:
                engine.disconnect()


if __name__ == '__main__':
    unittest.main()
