import copy
import json
from pathlib import Path
import re
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from ptz_controller.app import create_app
from ptz_controller.engine import Engine, defaults
from ptz_controller.probe import probe_connection


class ProbeTests(unittest.TestCase):
    def exchange(self, mode='udp', framing='sony', reply=b'\x90\x50\x02\xff',
                 address=1, sequence=0, prefix=b'', split=False):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM if mode == 'tcp' else socket.SOCK_DGRAM)
        server.bind(('127.0.0.1', 0));server.settimeout(2)
        if mode == 'tcp':server.listen(1)
        camera = defaults()['camera'];camera.update(mode=mode, framing=framing,
            port=server.getsockname()[1], address=address)
        received, failures = [], []
        wire = prefix + (struct.pack('!HHI', 0x0111, len(reply), sequence)+reply if framing=='sony' else reply)
        def endpoint():
            try:
                if mode=='tcp':
                    conn, _ = server.accept()
                    with conn:
                        conn.settimeout(2)
                        expected = 13 if framing=='sony' else 5
                        data = b''
                        while len(data)<expected:
                            part=conn.recv(64)
                            if not part:break
                            data+=part
                        received.append(data)
                        if split:
                            for part in (wire[:2],wire[2:7],wire[7:]):
                                if part:conn.sendall(part);time.sleep(.005)
                        else:conn.sendall(wire)
                else:
                    data, peer=server.recvfrom(64);received.append(data);server.sendto(wire,peer)
            except Exception as exc:failures.append(exc)
        worker=threading.Thread(target=endpoint);worker.start()
        try:
            result=probe_connection(camera, timeout=.2)
        finally:
            worker.join(3);server.close()
        self.assertFalse(failures, failures)
        expected=bytes([0x80+address,9,4,0,255])
        if framing=='sony':expected=bytes.fromhex('01 10 00 05 00 00 00 00')+expected
        self.assertEqual(received,[expected])  # no reset, clear, power or motion commands
        return result

    def test_valid_reply_all_transports_and_fragmented_tcp(self):
        for mode in ('tcp','udp'):
            for framing in ('raw','sony'):
                with self.subTest(mode=mode, framing=framing):
                    result=self.exchange(mode, framing, split=mode=='tcp')
                    self.assertEqual(result['status'],'success')
                    self.assertTrue(result['responded'])
                    self.assertIsNotNone(result['latency_ms'])
        self.assertEqual(self.exchange('tcp','raw',b'\xa0\x50\x03\xff',address=2)['status'],'success')

    def test_errors_ack_only_wrong_sequence_and_unrelated_data_do_not_pass(self):
        for reply, sequence, expected in ((b'\x90\x60\x02\xff',0,'rejected'),
                                          (b'\x90\x41\xff',0,'incomplete'),
                                          (b'\x90\x50\x02\xff',123,'invalid'),
                                          (b'\xa0\x50\x02\xff',0,'invalid'),
                                          (b'junk',0,'invalid')):
            with self.subTest(reply=reply, sequence=sequence):
                self.assertEqual(self.exchange(reply=reply,sequence=sequence)['status'],expected)
        # An ACK followed by a valid inquiry response is handled in the same read.
        ack=bytes.fromhex('01 11 00 03 00 00 00 00 90 41 ff')
        self.assertEqual(self.exchange('tcp',prefix=ack)['status'],'success')

    def test_simulator_does_not_open_a_socket(self):
        with patch('ptz_controller.probe.socket.socket') as factory:
            result=probe_connection(defaults()['camera'])
        factory.assert_not_called()
        self.assertEqual(result['status'],'simulator')
        self.assertFalse(result['responded'])

    def test_silent_udp_endpoint_times_out(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as silent:
            silent.bind(('127.0.0.1',0))
            camera=defaults()['camera'];camera.update(mode='udp',port=silent.getsockname()[1])
            result=probe_connection(camera,timeout=.04)
            self.assertEqual(result['status'],'timeout')
            self.assertFalse(result['responded'])


class ProbeAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'config.json'
        self.e=Engine(self.path);self.client=create_app(self.e).test_client()
        page=self.client.get('/').get_data(as_text=True)
        self.token=json.loads(re.search(r'const TOKEN = ("[^"]+")',page).group(1))

    def tearDown(self):
        self.e.disconnect();self.tmp.cleanup()

    def post(self, camera):
        return self.client.post('/api/test-connection',json={'camera':camera},headers={'X-PTZ-Token':self.token})

    def test_testing_does_not_save_connect_or_arm_and_validates_camera_only(self):
        original=copy.deepcopy(self.e.config)
        self.assertEqual(self.post(defaults()['camera']).status_code,200)
        self.assertIsNone(self.e.transport);self.assertFalse(self.e.armed)
        self.assertEqual(self.e.config,original);self.assertFalse(self.path.exists())
        self.e.connect();self.e.arm();transport=self.e.transport
        camera=defaults()['camera'];camera['host']='displayed-but-unsaved.local'
        response=self.post(camera)
        self.assertEqual(response.status_code,200)
        self.assertIs(self.e.transport,transport);self.assertTrue(self.e.armed)
        self.assertEqual(self.e.config,original)
        self.assertTrue(any('No physical camera' in item['message'] for item in self.e.logs))
        camera['port']=0
        self.assertEqual(self.post(camera).status_code,400)

    def test_probe_wait_does_not_hold_input_lock(self):
        entered, release=threading.Event(),threading.Event()
        result={'status':'timeout','responded':False,'message':'Test timeout','latency_ms':None}
        def blocked(*args):
            entered.set();release.wait(2);return result
        with patch('ptz_controller.probe.probe_connection',side_effect=blocked):
            worker=threading.Thread(target=self.e.test_connection,args=(defaults()['camera'],))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                acquired=self.e.lock.acquire(timeout=.1)
                if acquired:self.e.lock.release()
                self.assertTrue(acquired,'Probe must not block controller input or heartbeats')
            finally:release.set();worker.join(2)
