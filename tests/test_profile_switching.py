import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ptz_controller.engine import Engine, defaults, validate
from ptz_controller.visca import pantilt


class ProfileSwitchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.engine = Engine(Path(self.folder.name) / 'profiles.json', native_window=True)
        self.engine.devices = [{'id': 'pad'}]
        self.events = []
        self.transports = []

        def open_transport(settings, log):
            name = settings['host']
            self.events.append(('open', name))
            transport = Mock(error='', confirmed=True, backend='test')
            transport.stop.side_effect = lambda address: self.events.append(('stop', name))
            transport.close.side_effect = lambda: self.events.append(('close', name))
            self.transports.append(transport)
            return transport

        self.opener = patch('ptz_controller.engine.open_transport', side_effect=open_transport)
        self.open_mock = self.opener.start()
        self.addCleanup(self.opener.stop)
        for name, target in [('A', 'B'), ('B', 'A')]:
            config = defaults()
            config['controller'] = 'pad'
            config['camera'].update(mode='udp', framing='raw', host=name)
            config['bindings'] = [dict(source='button:0', action='profile_connect', param=target)]
            self.engine.save_preset(name, config)
        self.engine.load_preset('A')
        self.engine.connect()
        self.engine.arm()
        self.events.clear()

    def test_reciprocal_button_switches_once_per_press(self):
        original = self.transports[-1]
        self.engine.tick([1, 0, 0, 0], {'button:0'})
        self.assertEqual(self.engine.active_preset, 'B')
        self.assertTrue(self.engine.armed)
        self.assertEqual(self.events[:3], [('stop', 'A'), ('close', 'A'), ('open', 'B')])
        original.close.assert_called_once()
        # Switching tick must not send the previous profile's full-right sample.
        self.transports[-1].send.assert_not_called()
        for _ in range(4):
            self.engine.tick([0, 0, 0, 0], {'button:0'})
        self.assertEqual(self.engine.active_preset, 'B')
        self.assertEqual(self.open_mock.call_count, 2)
        self.engine.tick([0, 0, 0, 0], set())
        self.engine.tick([0, 0, 0, 0], {'button:0'})
        self.assertEqual(self.engine.active_preset, 'A')
        self.assertTrue(self.engine.armed)
        self.assertEqual(self.open_mock.call_count, 3)
        restored = Engine(self.engine.path)
        self.assertEqual(restored.active_preset, 'A')
        self.assertEqual(restored.config['bindings'][0]['param'], 'B')

    def test_remaining_old_bindings_do_not_execute_on_destination(self):
        self.engine.config['bindings'].append(dict(source='button:1', action='home'))
        self.engine.tick([1, 0, 0, 0], {'button:0', 'button:1'})
        self.transports[-1].send.assert_not_called()
        self.assertEqual(self.engine.previous, {'button:0', 'button:1'})

    def test_unreachable_camera_leaves_selected_profile_disarmed(self):
        original = self.transports[-1]
        self.open_mock.side_effect = OSError('Connection refused')
        with self.assertRaisesRegex(ValueError, 'Could not connect application profile B'):
            self.engine.tick([0, 0, 0, 0], {'button:0'})
        self.assertEqual(self.engine.active_preset, 'B')
        self.assertFalse(self.engine.armed)
        self.assertIsNone(self.engine.transport)
        original.close.assert_called_once()

    def test_missing_target_keeps_existing_connection(self):
        original = self.engine.transport
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            self.engine.execute('profile_connect', 'Deleted')
        self.assertEqual(self.engine.active_preset, 'A')
        self.assertIs(self.engine.transport, original)
        original.close.assert_not_called()

    def test_missing_controller_disarms_destination(self):
        self.engine.presets['B']['controller'] = 'unplugged'
        with self.assertRaisesRegex(ValueError, 'controller is disconnected'):
            self.engine.execute('profile_connect', 'B')
        self.assertFalse(self.engine.armed)
        self.assertIsNone(self.engine.transport)
        self.transports[-1].close.assert_called_once()

    def test_new_profile_mappings_and_motion_settings_take_effect(self):
        self.engine.presets['B']['speed'] = .25
        self.engine.presets['B']['axes'][0]['invert'] = True
        self.engine.tick([0, 0, 0, 0], {'button:0'})
        self.engine.tick([1, 0, 0, 0], set())
        self.assertIn(pantilt(-.25, 0), [call.args[0] for call in self.transports[-1].send.call_args_list])
        state = self.engine.snapshot()
        self.assertEqual(state['config']['camera']['host'], 'B')
        self.assertEqual(state['config']['bindings'][0]['param'], 'A')
        self.assertEqual(state['profile_generation'], 2)
        self.assertEqual(self.engine.live_snapshot()['profile_generation'], 2)

    def test_mapping_validation_allows_forward_reference_but_requires_name(self):
        config = copy.deepcopy(self.engine.config)
        config['bindings'][0]['param'] = 'Profile saved later'
        self.assertEqual(validate(config)['bindings'][0]['param'], 'Profile saved later')
        for invalid in ('', '  ', 0, None):
            config['bindings'][0]['param'] = invalid
            with self.assertRaises(ValueError):
                validate(config)


if __name__ == '__main__':
    unittest.main()
