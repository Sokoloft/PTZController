import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ptz_controller.app import create_app
from ptz_controller.engine import Engine


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name)/'config.json')

    def tearDown(self):
        self.engine.disconnect()
        self.temp.cleanup()

    def test_small_frames_and_disarmed_input(self):
        self.engine.tick([.5, -.2], {'button:2'})
        frame = self.engine.live_snapshot()
        self.assertEqual(frame['sequence'], 1)
        self.assertEqual(frame['input']['axes'], [.5, -.2])
        self.assertNotIn('logs', frame)
        self.assertNotIn('config', frame)
        self.assertNotIn('presets', frame['model'])
        frame['input']['axes'][0] = 99
        self.assertEqual(self.engine.input['axes'][0], .5)

    def test_simulation_uses_elapsed_time(self):
        e = self.engine
        e.connect();e.armed = True
        for _ in range(60):e.tick([1], set(), 1/60)
        self.assertAlmostEqual(e.model['pan'], .2)
        e.execute('home');self.assertEqual(e.model['pan'], 0)
        for _ in range(20):e.tick([1], set(), 1/20)
        self.assertAlmostEqual(e.model['pan'], .2)
        generation = e.live_snapshot()['generation']
        e.stop()
        self.assertGreater(e.live_snapshot()['generation'], generation)

    def test_stream_yields_fresh_samples_and_does_not_keep_controls_armed(self):
        e = self.engine
        client = create_app(e).test_client()
        with client.get('/api/live', buffered=False) as response:
            self.assertEqual(response.mimetype, 'text/event-stream')
            self.assertIn('no-store', response.headers['Cache-Control'])
            first = json.loads(next(response.response).decode().removeprefix('data: '))
            self.assertEqual(first['sequence'], 0)
            e.tick([.8], set())
            second = json.loads(next(response.response).decode().removeprefix('data: '))
            self.assertGreater(second['sequence'], first['sequence'])
            self.assertEqual(second['input']['axes'], [.8])
            self.assertEqual(e.heartbeat, 0)  # output stream is not a GUI heartbeat

    def test_fast_polling_bounds_motion_commands_but_releases_immediately(self):
        e = self.engine
        e.connect();e.armed = True
        with patch('ptz_controller.engine.time.monotonic', return_value=10) as clock:
            e.tick([.5], set(), 1/60)
            first = len(e.traffic)
            clock.return_value = 10.01
            e.tick([.8], set(), 1/60)
            self.assertEqual(len(e.traffic), first)
            e.tick([0], set(), 1/60)
            self.assertGreater(len(e.traffic), first)
            self.assertEqual(e.motion['pan'], 0)
