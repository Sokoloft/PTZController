import json
from pathlib import Path
import re
import tempfile
import unittest
from html.parser import HTMLParser
from ptz_controller.app import create_app
from ptz_controller.engine import Engine, defaults


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'config.json'
        self.e = Engine(self.path)
        self.client = create_app(self.e).test_client()
        self.page = self.client.get('/').get_data(as_text=True)
        token = json.loads(re.search(r'const TOKEN = ("[^"]+")', self.page).group(1))
        self.headers = {'X-PTZ-Token': token}

    def tearDown(self):
        self.e.disconnect()
        self.tmp.cleanup()

    def post(self, operation, data=None):
        return self.client.post('/api/'+operation, json=data or {}, headers=self.headers)

    def test_full_presets_persist_switch_and_update(self):
        first = defaults()
        first['controller'] = 'saved-guid:0'
        first['camera']['host'] = 'camera-a.local'
        first['axes'][0]['deadzone'] = .3
        first['bindings'] = [{'source':'button:2','action':'preset_recall','param':3}]
        self.e.save_preset('Studio', first)
        second = defaults();second['speed'] = .25;second['camera']['host'] = 'camera-b.local'
        self.e.save_preset('Stage', second)
        studio = self.path.with_name('Studio.json')
        stage = self.path.with_name('Stage.json')
        self.assertEqual(json.loads(studio.read_text()), first)
        self.assertEqual(json.loads(stage.read_text()), second)
        self.assertFalse(self.path.exists())
        self.e.connect();self.e.armed = True
        self.e.load_preset('Studio')
        self.assertIsNone(self.e.transport)
        self.assertFalse(self.e.armed)
        self.assertEqual(self.e.config, first)
        self.assertEqual(self.e.active_preset, 'Studio')
        loaded = Engine(self.path)
        self.assertEqual(loaded.config, first)
        self.assertEqual(loaded.presets, {'Studio':first, 'Stage':second})
        self.assertEqual(loaded.active_preset, 'Studio')
        first['speed'] = .75
        self.e.save_preset('Studio', first)
        self.assertEqual(len(self.e.presets), 2)
        self.assertEqual(Engine(self.path).presets['Studio']['speed'], .75)
        self.assertEqual(json.loads(stage.read_text()), second)
        self.e.config['speed'] = .1
        self.assertTrue(self.e.snapshot()['preset_modified'])
        self.e.delete_preset('Studio')
        self.assertIsNone(self.e.active_preset)
        self.assertFalse(studio.exists())
        self.assertEqual(list(Engine(self.path).presets), ['Stage'])

    def test_legacy_config_migration_and_invalid_save(self):
        self.path.write_text(json.dumps(defaults()))
        e = Engine(self.path)
        self.assertEqual(e.config, defaults())
        self.assertEqual(e.presets, {'config': defaults()})
        e.save_preset('Migrated', e.config)
        self.assertEqual(json.loads(self.path.read_text()), defaults())
        original = self.path.read_text()
        for name in ('', '  ', 'x'*81, '../Other', 'Stage/Left'):
            with self.assertRaises(ValueError):e.save_preset(name, defaults())
        invalid = defaults();invalid['axes'][0]['deadzone'] = 2
        with self.assertRaises(ValueError):e.save_preset('Invalid', invalid)
        self.assertEqual(self.path.read_text(), original)

    def test_import_stores_and_activates_named_json(self):
        config = defaults()
        config['speed'] = .75
        config['axes'][0]['deadzone'] = .44
        response = self.post('preset-import', {'name': 'Studio camera', 'config': config})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['state']['active_preset'], 'Studio camera')
        preset = self.path.with_name('Studio camera.json')
        self.assertEqual(json.loads(preset.read_text()), config)
        restarted = Engine(self.path)
        self.assertEqual(restarted.config, config)
        self.assertEqual(restarted.active_preset, 'Studio camera')
        self.assertEqual(restarted.presets['Studio camera'], config)
        self.e.save(defaults())
        exported = self.post('preset-export', {'name': 'Studio camera'})
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json['filename'], 'Studio camera.json')
        self.assertEqual(exported.json['config'], config)

        self.assertEqual(self.post('preset-import', {'name': 'Studio camera', 'config': defaults()}).status_code, 400)
        self.assertEqual(self.post('preset-import', {'name': 'Invalid', 'config': {}}).status_code, 400)
        self.assertEqual(json.loads(preset.read_text()), config)
        self.assertFalse(self.path.with_name('Invalid.json').exists())

    def test_discovers_standalone_presets_and_ignores_unrelated_json(self):
        self.path.with_name('Desk.json').write_text(json.dumps(defaults()))
        unrelated = self.path.with_name('Other.json')
        unrelated.write_text('{"other":true}')
        engine = Engine(self.path)
        self.assertEqual(engine.presets, {'Desk': defaults()})
        with self.assertRaises(ValueError):
            engine.save_preset('Other', defaults())
        self.assertEqual(json.loads(unrelated.read_text()), {'other': True})
        engine.load_preset('Desk')
        self.assertEqual(Engine(self.path).active_preset, 'Desk')

    def test_one_click_connect_and_preset_api(self):
        self.assertEqual(self.post('preset-save', {'name':'Desk','config':defaults()}).status_code, 200)
        self.assertEqual(self.post('connect').status_code, 200)
        self.assertTrue(self.e.armed)
        self.assertIsNotNone(self.e.transport)
        self.assertGreater(self.e.heartbeat, 0)
        self.assertEqual(self.post('preset-load', {'name':'Desk'}).status_code, 200)
        self.assertFalse(self.e.armed)
        self.assertIsNone(self.e.transport)
        self.assertEqual(self.post('connect').status_code, 200)
        self.assertTrue(self.e.armed)
        self.assertEqual(self.post('preset-delete', {'name':'Desk'}).status_code, 200)
        self.assertEqual(self.e.presets, {})

    def test_connect_missing_saved_controller_fails_cleanly_and_logs_error(self):
        config = defaults();config['controller'] = 'missing:0'
        response = self.post('connect', {'config':config})
        self.assertEqual(response.status_code, 400)
        self.assertIn('disconnected', response.json['error'])
        self.assertFalse(self.e.armed)
        self.assertIsNone(self.e.transport)
        self.assertTrue(any(l['kind']=='ERROR' for l in self.e.logs))

    def test_button_capture_ignores_held_buttons_and_keeps_short_press_result(self):
        self.e.connect();self.e.armed = True
        self.e.tick([], {'button:1'})
        result = self.post('capture-start').json
        self.assertFalse(self.e.armed)
        self.e.tick([], {'button:1'})
        self.assertIsNone(self.e.capture['source'])
        self.e.tick([], set());self.e.tick([], {'button:4'});self.e.tick([], set())
        frame = self.e.live_snapshot()
        self.assertEqual(frame['capture']['id'], result['capture_id'])
        self.assertEqual(frame['capture']['source'], 'button:4')
        self.assertFalse(frame['capture']['listening'])
        self.post('capture-start');self.e.tick([], {'hat:0:up'})
        self.assertEqual(self.e.capture['source'], 'hat:0:up')
        current = self.post('capture-start').json['capture_id']
        self.post('capture-cancel', {'capture_id':current-1})
        self.assertTrue(self.e.capture['listening'])
        self.post('capture-cancel', {'capture_id':current})
        self.assertFalse(self.e.capture['listening'])

    def test_background_requests_not_logged_and_traffic_cannot_evict_errors(self):
        before = list(self.e.logs)
        self.post('heartbeat');self.client.get('/api/state')
        self.assertEqual(list(self.e.logs), before)
        self.e.log('ERROR', 'Keep this error')
        for i in range(200):self.e.log('TX', str(i))
        self.assertTrue(any(l['message']=='Keep this error' for l in self.e.snapshot()['logs']))
        self.assertEqual(len(self.e.traffic), 150)

    def test_configuration_sections_are_inside_collapsible_settings(self):
        class Sections(HTMLParser):
            def __init__(self):
                super().__init__();self.inside=False;self.heading=False;self.found={}
            def handle_starttag(self, tag, attrs):
                if tag=='details' and dict(attrs).get('id')=='settings':self.inside=True
                if tag=='h2':self.heading=True
            def handle_endtag(self, tag):
                if tag=='details':self.inside=False
                if tag=='h2':self.heading=False
            def handle_data(self, data):
                if self.heading:self.found[data]=self.inside
        parser=Sections();parser.feed(self.page)
        for title in ('Controller & Motion','Button & D-Pad Mappings','Camera Commands','Camera-free Test Bench'):
            self.assertTrue(parser.found[title])
        for title in ('Camera Connection','Camera Presets','Activity Log'):
            self.assertFalse(parser.found[title])
