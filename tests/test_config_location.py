import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ptz_controller.config import default_config_path
from ptz_controller.engine import Engine, defaults


class ConfigLocationTests(unittest.TestCase):
    def test_default_respects_user_config_location(self):
        with patch.dict(os.environ, {'HOME': '/home/camera', 'XDG_CONFIG_HOME': ''}):
            self.assertEqual(default_config_path(), Path('/home/camera/.config/ptz-controller/config.json'))
        with patch.dict(os.environ, {'XDG_CONFIG_HOME': '/settings'}):
            self.assertEqual(default_config_path(), Path('/settings/ptz-controller/config.json'))

    def test_appimage_ignores_adjacent_config_and_discovers_user_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_config = root/'config.json'
            app_config.write_text(json.dumps({'config': defaults(), 'presets': {'Adjacent': defaults()}}))
            user_dir = root/'settings/ptz-controller'
            user_dir.mkdir(parents=True)
            preset = defaults()
            preset['axes'][0]['deadzone'] = .37
            (user_dir/'Desk.json').write_text(json.dumps(preset))
            with patch.dict(os.environ, {'XDG_CONFIG_HOME': str(user_dir.parent),
                                         'APPIMAGE': str(root/'PTZ_Controller.AppImage')}):
                engine = Engine()
                self.assertEqual(engine.active_preset, 'Desk')
                self.assertEqual(engine.config, preset)
                self.assertEqual(engine.presets, {'Desk': preset})
                self.assertFalse((user_dir/'config.json').exists())
                self.assertIn('Adjacent', app_config.read_text())

    def test_embedded_presets_migrate_only_once_in_user_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'config.json'
            config = defaults()
            config['axes'][0]['deadzone'] = .4
            original = {'version': 1, 'config': defaults(), 'active_preset': 'Desk',
                        'presets': {'Desk': config, 'Stage': defaults(), 'Stage/Left': defaults()}}
            path.write_text(json.dumps(original))
            engine = Engine(path)
            self.assertEqual(json.loads(path.with_name('Desk.json').read_text()), config)
            self.assertEqual(engine.active_preset, 'Desk')
            self.assertEqual(engine.config, config)
            self.assertEqual(set(engine.presets), {'Desk', 'Stage'})
            engine.delete_preset('Stage')
            self.assertEqual(Engine(path).presets, {'Desk': config})
            self.assertEqual(json.loads(path.read_text()), original)

    def test_session_edits_only_persist_when_saved_as_a_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'config.json'
            engine = Engine(path)
            config = defaults()
            config['axes'][0]['deadzone'] = .65
            engine.save(config)
            self.assertEqual(engine.config, config)
            self.assertFalse(list(path.parent.iterdir()))
            engine.save_preset('Desk', config)
            engine.save(defaults())
            self.assertEqual(Engine(path).config, config)
            self.assertFalse(path.exists())
