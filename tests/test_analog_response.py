import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ptz_controller.engine import Engine, defaults, validate
from ptz_controller.visca import pantilt


class AnalogResponseTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.engine = Engine(Path(self.folder.name) / 'settings.json')
        self.engine.transport = Mock()
        self.engine.armed = True
        self.engine.config['axes'] = [dict(index=0, target='pan', deadzone=.15,
                                           invert=False, response=3)]
        self.clock = 10.

    def move(self, value, pressed=()):
        self.clock += .1
        self.engine.transport.send.reset_mock()
        with patch('ptz_controller.engine.time.monotonic', return_value=self.clock):
            self.engine.tick([value], set(pressed))
        return [call.args[0] for call in self.engine.transport.send.call_args_list
                if call.args[0][1:4] == b'\x01\x06\x01']

    def test_precision_uses_slowest_speed_for_light_movement(self):
        packets = self.move(.5)
        # Half stick at the default maximum speed used to request pan speed 5.
        self.assertEqual(packets, [bytes.fromhex('81 01 06 01 01 01 02 03 ff')])
        packets = self.move(.75)
        self.assertEqual(packets[0][4], 4)
        packets = self.move(1.)
        self.assertEqual(packets[0][4], 12)

    def test_deadzone_release_and_reverse(self):
        self.assertEqual(self.move(.15), [pantilt(0, 0)])
        # Input just beyond the deadzone must not be rounded into a stop.
        self.assertEqual(self.move(.151)[0][6], 2)
        self.assertEqual(self.move(-.151)[0][6], 1)
        self.assertEqual(self.move(0), [pantilt(0, 0)])

    def test_inversion_and_button_override(self):
        self.engine.config['axes'][0]['invert'] = True
        self.assertEqual(self.move(.5)[0][6], 1)
        self.engine.config['bindings'] = [dict(source='button:0', action='pan_right')]
        self.assertEqual(self.move(.5, ['button:0']), [pantilt(.5, 0)])

    def test_response_choices_preserve_full_speed(self):
        for response, half_speed in ((1, 5), (2, 2), (3, 1)):
            with self.subTest(response=response):
                self.engine.config['axes'][0]['response'] = response
                self.move(0)
                self.assertEqual(self.move(.5)[0][4], half_speed)
                self.assertEqual(self.move(1)[0][4], 12)

    def test_saved_axis_without_response_defaults_to_precision(self):
        config = defaults()
        for axis in config['axes']:
            del axis['response']
        self.assertTrue(all(axis['response'] == 3 for axis in validate(config)['axes']))
        config['axes'][0]['response'] = 0
        with self.assertRaises(ValueError):
            validate(config)

    def test_response_survives_save_and_reload(self):
        config = defaults()
        config['axes'][0]['response'] = 2
        engine = Engine(Path(self.folder.name) / 'saved.json')
        engine.save_preset('Desk', config)
        self.assertEqual(Engine(engine.path).config['axes'][0]['response'], 2)


if __name__ == '__main__':
    unittest.main()
