"""Physical desktop input survives GUI timer suspension; watchdogs remain active."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ptz_controller.engine import Engine


class BackgroundControllerTests(unittest.TestCase):
    def run_one_tick(self, native_window, controller, attached=True):
        with tempfile.TemporaryDirectory() as folder:
            engine = Engine(Path(folder) / 'config.json', native_window=native_window)
            engine.config['controller'] = controller
            engine.transport = Mock(error='')
            transport = engine.transport
            engine.armed = True
            engine.heartbeat = 0  # GUI timers have stopped running.
            engine.virtual['axes'][0] = .8
            reader = Mock()
            reader.devices = [{'id': 'pad'}] if attached else []
            reader.read.return_value = ([.8, 0, 0, 0], set()) if attached else None
            pygame = types.ModuleType('pygame')
            pygame.display = Mock()
            pygame.joystick = Mock()
            pygame.event = Mock()
            pygame.event.get.return_value = []
            pygame.error = RuntimeError
            pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED = 1, 2
            pygame.quit = Mock()
            states = []
            tick = engine.tick

            def record(*args):
                tick(*args)
                states.append(engine.armed)

            with patch.dict('sys.modules', {'pygame': pygame, 'pygame._sdl2': None}), \
                    patch('ptz_controller.engine.ControllerReader', return_value=reader), \
                    patch('ptz_controller.engine.time.monotonic', return_value=20), \
                    patch.object(engine.quitting, 'wait', side_effect=[False, True]), \
                    patch.object(engine, 'tick', side_effect=record):
                engine.run()
            # run() also stops/disconnects in its finally block on application exit.
            transport.stop.assert_called()
            transport.close.assert_called_once()
            return states, list(engine.logs), transport

    def test_desktop_physical_input_continues_without_gui_heartbeat(self):
        states, logs, transport = self.run_one_tick(True, 'pad')
        self.assertEqual(states, [True])
        transport.send.assert_called()
        self.assertFalse(any('heartbeat lost' in event['message'] for event in logs))

    def test_virtual_and_browser_input_keep_heartbeat_watchdog(self):
        for native, controller in ((True, 'virtual'), (False, 'pad'), (False, 'virtual')):
            with self.subTest(native=native, controller=controller):
                states, logs, transport = self.run_one_tick(native, controller)
                self.assertEqual(states, [False])
                transport.send.assert_not_called()
                self.assertTrue(any('heartbeat lost' in event['message'] for event in logs))

    def test_desktop_still_stops_on_controller_loss(self):
        states, logs, transport = self.run_one_tick(True, 'pad', attached=False)
        self.assertEqual(states, [False])
        transport.send.assert_not_called()
        self.assertTrue(any('controller disconnected' in event['message'] for event in logs))


if __name__ == '__main__':
    unittest.main()
