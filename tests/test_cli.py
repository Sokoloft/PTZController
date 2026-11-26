import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from ptz_controller import __version__


class CLITests(unittest.TestCase):
    def test_version_exits_without_starting_application_or_writing_config(self):
        main = Path(__file__).resolve().parents[1]/'src/main.py'
        with tempfile.TemporaryDirectory() as tmp:
            for option in ('--version', '-V'):
                result = subprocess.run([sys.executable, str(main), option], cwd=tmp,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), f'PTZ Controller {__version__}')
                self.assertEqual(result.stderr, '')
                self.assertFalse(list(Path(tmp).iterdir()))

    def test_config_argument_is_forwarded_and_documented(self):
        main = Path(__file__).resolve().parents[1]/'src/main.py'
        from ptz_controller.cli import main as launch
        with patch.object(sys, 'argv', [str(main), '--config', '/tmp/custom-ptz/config.json']), \
             patch('ptz_controller.engine.Engine', side_effect=RuntimeError('stop before startup')) as factory:
            with self.assertRaisesRegex(RuntimeError, 'stop before startup'):
                launch()
        factory.assert_called_once_with('/tmp/custom-ptz/config.json', native_window=True)
        help_result = subprocess.run([sys.executable, str(main), '--help'],
                                     capture_output=True, text=True, timeout=5)
        self.assertIn('--config', help_result.stdout)

    def test_export_opens_save_dialog_and_writes_preset_at_selected_path(self):
        from ptz_controller.cli import PresetDialogs
        from ptz_controller.engine import Engine, defaults

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = Engine(root/'settings/config.json')
            preset = defaults()
            preset['axes'][0]['deadzone'] = .42
            engine.save_preset('Studio', preset)
            export = root/'export/Studio.json'
            export.parent.mkdir()
            bridge = PresetDialogs(engine, 'open', 'save')
            bridge._window = Mock()
            bridge._window.create_file_dialog.return_value = (str(export),)
            self.assertEqual(bridge.export_preset('Studio'), {'saved': True})
            bridge._window.create_file_dialog.assert_called_once_with(
                'save', directory=str(Path.home()), save_filename='Studio.json',
                file_types=('JSON files (*.json)',))
            self.assertEqual(json.loads(export.read_text()), preset)
            bridge._window.create_file_dialog.return_value = None
            self.assertEqual(bridge.export_preset('Studio'), {'saved': False})
            bridge._window.create_file_dialog.reset_mock()
            bridge._window.create_file_dialog.return_value = (str(export),)
            picked = bridge.choose_preset()
            self.assertEqual(picked['name'], 'Studio.json')
            self.assertEqual(json.loads(picked['content']), preset)
            bridge._window.create_file_dialog.assert_called_once_with(
                'open', directory=str(Path.home()), file_types=('JSON files (*.json)',))
