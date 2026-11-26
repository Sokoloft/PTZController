"""Thread-safe mapping engine shared by real and virtual controllers."""
import copy
import json
import math
import os
from pathlib import Path
import threading
import time
from collections import deque
from .visca import ACTIONS, CONTINUOUS, open_transport, command, pantilt, lens
from .controllers import ControllerReader
from .config import default_config_path, preset_path, write_json


def defaults():
    return {'camera': {'mode': 'simulator', 'host': '127.0.0.1', 'port': 52381,
                       'framing': 'sony', 'address': 1},
            'controller': 'virtual', 'speed': .5,
            'axes': [{'index': 0, 'target': 'pan', 'deadzone': .15, 'response': 3, 'invert': False},
                     {'index': 1, 'target': 'tilt', 'deadzone': .15, 'response': 3, 'invert': False},
                     {'index': 3, 'target': 'zoom', 'deadzone': .15, 'response': 3, 'invert': True}],
            'bindings': []}


def deadzone(value, zone):
    return 0 if abs(value) <= zone else math.copysign((abs(value)-zone)/(1-zone), value)


def analog_response(value, zone, response=3):
    """Rescale the deadzone, then soften stick travel without changing its range."""
    value = deadzone(max(-1, min(1, value)), zone)
    return math.copysign(abs(value) ** response, value)


def validate(config):
    c = copy.deepcopy(config)
    cam = c['camera']
    if cam['mode'] not in ('simulator', 'tcp', 'udp') or cam['framing'] not in ('sony', 'raw'):
        raise ValueError('Invalid transport or framing')
    cam['port'], cam['address'] = int(cam['port']), int(cam['address'])
    if not 1 <= cam['port'] <= 65535 or not 1 <= cam['address'] <= 7:
        raise ValueError('Invalid port or camera address')
    if not isinstance(cam['host'], str) or not cam['host'].strip():
        raise ValueError('Camera host is required')
    if cam['framing'] == 'sony' and cam['address'] != 1:
        raise ValueError('Sony VISCA over IP uses address 1')
    c['speed'] = float(c['speed'])
    if not 0 < c['speed'] <= 1:
        raise ValueError('Speed must be greater than zero and at most 1')
    if not isinstance(c['controller'], str):
        raise ValueError('Invalid controller')
    if not isinstance(c['axes'], list) or not isinstance(c['bindings'], list):
        raise ValueError('Axes and button mappings must be lists')
    used = set()
    for axis in c['axes']:
        axis['index'], axis['deadzone'] = int(axis['index']), float(axis['deadzone'])
        axis['response'] = float(axis.get('response', 3))
        if axis['response'] not in (1, 2, 3):
            raise ValueError('Axis response must be 1 (linear), 2 (gentle), or 3 (precision)')
        if axis['target'] not in ('pan', 'tilt', 'zoom', 'focus') or axis['target'] in used:
            raise ValueError('Map each motion target to at most one axis')
        used.add(axis['target'])
        if not 0 <= axis['index'] <= 63 or not 0 <= axis['deadzone'] <= .95 or not isinstance(axis['invert'], bool):
            raise ValueError('Invalid axis index, inversion or deadzone')
    sources = set()
    for binding in c['bindings']:
        source = binding['source']
        if not isinstance(source, str):
            raise ValueError('Invalid button or D-pad source')
        parts = source.split(':')
        valid = len(parts) == 2 and parts[0] == 'button' and parts[1].isdigit()
        valid |= len(parts) == 3 and parts[0] == 'hat' and parts[1].isdigit() and parts[2] in ('up', 'down', 'left', 'right')
        if not valid or int(parts[1]) > 127 or source in sources:
            raise ValueError('Use unique button:N or hat:N:up/down/left/right sources')
        sources.add(source)
        action = binding['action']
        if action not in ACTIONS:
            raise ValueError('Unknown mapped action')
        if action == 'profile_connect':
            target = binding.get('param')
            if not isinstance(target, str) or not target.strip():
                raise ValueError('Select a saved application profile for the mapping')
        elif action not in CONTINUOUS | {'none', 'stop'}:
            command(action, binding.get('param', 0), cam['address'])
    return c


def read_preset(path):
    try:
        return validate(json.loads(path.read_text(encoding='utf-8')))
    except (KeyError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError('Invalid preset file') from exc


class Engine:
    def __init__(self, path=None, *, native_window=False):
        # The desktop window lifetime is owned by cli.main, which stops the
        # engine on exit. Physical input must not depend on Chromium timers
        # that can be suspended when that window is minimized.
        self.native_window = native_window
        self.lock = threading.RLock()
        self.updates = threading.Condition(self.lock)
        self.sample_sequence = 0
        self.sample_time = time.monotonic()
        self.generation = 0
        self.last_command_time = 0.
        self.path = Path(path).expanduser() if path is not None else default_config_path()
        self.config_dir = self.path.parent
        self._selection_path = self.config_dir/'.active-preset'
        self.logs = deque(maxlen=150)
        self.traffic = deque(maxlen=150)
        self.log_sequence = 0
        self.config = defaults()
        self.presets = {}
        self.active_preset = None
        self.profile_generation = 0
        self._restore_presets()
        self.transport = None
        self.devices = []
        self.input = {'axes': [], 'pressed': []}
        self.virtual = {'axes': [0]*8, 'pressed': []}
        self.previous = set()
        self.last_motion = None
        self.armed = False
        self.heartbeat = 0
        self.model = {'pan': 0., 'tilt': 0., 'zoom': 0., 'focus': 0., 'presets': {}}
        self.motion = dict.fromkeys(('pan', 'tilt', 'zoom', 'focus'), 0.)
        self.quitting = threading.Event()
        self.input_error = ''
        self.capture = {'id': 0, 'listening': False, 'source': None}
        self.capture_held = set()

    def log(self, kind, message):
        with self.lock:
            self.log_sequence += 1
            event = {'time': time.strftime('%H:%M:%S'), 'kind': kind, 'message': message,
                     'sequence': self.log_sequence}
            (self.traffic if kind in ('TX', 'RX') else self.logs).append(event)

    def save(self, config):
        """Apply settings for the current session."""
        config = validate(config)
        with self.lock:
            self.disconnect()
            self.config = config
            self.previous.clear()
            self.log('INFO', 'Applied camera settings for this session')

    def _restore_presets(self):
        for path in sorted(self.config_dir.glob('*.json')):
            try:
                preset_path(self.config_dir, path.stem)
                if path.is_file() and not path.is_symlink():
                    self.presets[path.stem] = read_preset(path)
            except (OSError, ValueError):
                continue

        active = None
        try:
            active = json.loads(self._selection_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            active = self._migrate_embedded_presets()
        except (OSError, ValueError):
            self.log('WARN', 'Could not read the previous preset selection')
        if not isinstance(active, str) or active not in self.presets:
            active = next(iter(self.presets), None)
        if active is not None:
            self.active_preset = active
            self.config = copy.deepcopy(self.presets[active])

    def _migrate_embedded_presets(self):
        # Older installations can have an aggregate settings file in this directory.
        if self.path.stem in self.presets or not self.path.is_file() or self.path.is_symlink():
            return None
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict) or 'config' not in saved:
                return None
            embedded = saved.get('presets') or {}
            if not isinstance(embedded, dict):
                return None
            if not embedded and not self.presets:
                embedded = {'Custom setup': saved['config']}
            active = saved.get('active_preset')
            for name, raw_config in embedded.items():
                if name in self.presets:
                    continue
                try:
                    self._store_preset(name, validate(raw_config))
                except (OSError, ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
                    self.log('WARN', f'Could not migrate preset {name!r}: {exc}')
            self._remember_preset(active)
            return active
        except (OSError, ValueError, RecursionError) as exc:
            self.log('WARN', f'Could not migrate older presets: {exc}')
            return None

    def _remember_preset(self, name):
        write_json(self._selection_path, name)

    def _store_preset(self, name, config, *, replace=False):
        path = preset_path(self.config_dir, name)
        existing = None
        if path.is_symlink():
            raise ValueError(f'Cannot use {path.name!r} because it is a symbolic link')
        if path.exists():
            try:
                existing = read_preset(path) if path.is_file() else None
            except (OSError, ValueError):
                existing = None
            if existing is None:
                raise ValueError(f'Choose another preset name. {path.name!r} is already in use.')
        if existing is not None and existing != config and not replace:
            raise ValueError(f'A different preset named {name!r} already exists. Rename the file and try again.')
        write_json(path, config)
        self.presets[name] = copy.deepcopy(config)

    def save_preset(self, name, config):
        if not isinstance(name, str):
            raise ValueError('Enter a preset name (1–80 characters)')
        name, config = name.strip(' '), validate(config)
        with self.lock:
            self._store_preset(name, config, replace=True)
            self._remember_preset(name)
            if config != self.config:
                self.disconnect()
                self.cancel_capture()
            self.config, self.active_preset = config, name
            self.log('INFO', f'Saved application preset: {name}')

    def import_preset(self, name, config):
        try:
            config = validate(config)
        except (KeyError, TypeError, OverflowError, RecursionError) as exc:
            raise ValueError('The file must contain a complete camera preset') from exc
        with self.lock:
            self._store_preset(name, config)
            self.load_preset(name)

    def load_preset(self, name):
        with self.lock:
            if name not in self.presets:
                raise ValueError('Preset does not exist')
            config = copy.deepcopy(self.presets[name])
            self._remember_preset(name)
            self.disconnect()
            self.cancel_capture()
            self.config, self.active_preset = config, name
            self.profile_generation += 1
            self.previous.clear()
            self.input = {'axes': [], 'pressed': []}
            self.virtual = {'axes': [0]*8, 'pressed': []}
            self.log('INFO', f'Loaded application preset: {name}')

    def switch_profile(self, name):
        """Stop the old camera, load a saved setup, and attempt to enable it."""
        with self.lock:
            if name not in self.presets:
                raise ValueError(f'Application profile does not exist: {name}')
            held = set(self.input['pressed'])
            self.load_preset(name)
            # A held switch button must be released before the destination's
            # reciprocal mapping can fire, including mappings on other buttons.
            self.previous = held
            self.log('ACTION', f'Connecting application profile: {name}')
            try:
                self.connect()
                self.arm()
            except Exception as exc:
                self.disconnect()
                raise ValueError(f'Could not connect application profile {name}: {exc}') from exc

    def export_preset(self, name):
        with self.lock:
            if not isinstance(name, str) or name not in self.presets:
                raise ValueError('Select a saved preset to export')
            return {'filename': preset_path(self.config_dir, name).name,
                    'config': copy.deepcopy(self.presets[name])}

    def delete_preset(self, name):
        with self.lock:
            if name not in self.presets:
                raise ValueError('Preset does not exist')
            preset_path(self.config_dir, name).unlink(missing_ok=True)
            del self.presets[name]
            if name == self.active_preset:
                self.active_preset = None
                self._remember_preset(None)
            self.log('INFO', f'Deleted application preset: {name}')

    def arm(self):
        with self.lock:
            if not self.transport or self.transport.error:
                raise ValueError('Connect a working camera or simulator first')
            if self.config['controller'] != 'virtual' and self.config['controller'] not in {d['id'] for d in self.devices}:
                raise ValueError('Saved controller is disconnected; select an available controller in Settings')
            self.cancel_capture()
            self.heartbeat = time.monotonic()
            self.armed = True
            self.log('INFO', 'Controller armed')

    def start_capture(self):
        with self.lock:
            self.stop()
            self.capture = {'id': self.capture['id'] + 1, 'listening': True, 'source': None}
            self.capture_held = set(self.input['pressed'])
            self.log('INFO', 'Listening for a controller button or D-pad direction')
            return self.capture['id']

    def cancel_capture(self):
        with self.lock:
            self.capture['listening'] = False

    def preset_state(self):
        return {'active_preset': self.active_preset,
                'profile_generation': self.profile_generation,
                'preset_modified': bool(self.active_preset and self.presets.get(self.active_preset) != self.config)}

    def test_connection(self, camera):
        from .probe import probe_connection
        # Only connection fields matter; unfinished mapping edits must not block a test.
        trial = defaults()
        trial['camera'] = camera
        settings = validate(trial)['camera']
        self.log('INFO', 'Testing displayed camera connection settings')
        # Deliberately outside self.lock: timeouts must not block input or heartbeats.
        result = probe_connection(settings, self.log)
        self.log('INFO' if result['status'] in ('success', 'simulator') else 'WARN', result['message'])
        return result

    def connect(self):
        with self.lock:
            self.disconnect()
            self.transport = open_transport(self.config['camera'], self.log)
            self.log('INFO', 'Simulator ready' if self.config['camera']['mode'] == 'simulator' else self.transport.backend + '; awaiting camera reply')
            self.stop()

    def disconnect(self):
        with self.lock:
            self.cancel_capture()
            self.stop()
            if self.transport:
                self.transport.close()
                self.transport = None
                self.log('INFO', 'Camera disconnected')

    def send(self, data):
        if not self.transport:
            raise ValueError('Connect the camera or simulator first')
        self.transport.send(data)

    def stop(self):
        with self.lock:
            self.generation += 1
            self.armed = False
            self.motion = dict.fromkeys(self.motion, 0.)
            self.last_motion = None
            if self.transport:
                try:
                    self.transport.stop(self.config['camera']['address'])
                except OSError as exc:
                    self.log('ERROR', f'Stop failed: {exc}')

    def select_controller(self, selected):
        with self.lock:
            if selected != 'virtual' and selected not in {d['id'] for d in self.devices}:
                raise ValueError('Controller is disconnected; choose an available controller')
            self.stop()
            self.config['controller'] = selected
            self.input = {'axes': [], 'pressed': []}
            self.previous.clear()
            self.cancel_capture()
            self.log('INFO', 'Controller selected. Input monitor is live. Use Save Preset to remember it.')

    def execute(self, action, param=0):
        with self.lock:
            if action == 'stop':
                self.stop()
                return
            if action == 'none':
                return
            if action == 'profile_connect':
                self.switch_profile(param)
                return
            data = command(action, param, self.config['camera']['address'])
            self.send(data)
            self.log('ACTION', ACTIONS[action] + (f': {param}' if action.startswith('preset_') else ''))
            if self.config['camera']['mode'] == 'simulator':
                if action in ('home', 'preset_recall'):
                    self.generation += 1
                if action == 'home':
                    self.model['pan'] = self.model['tilt'] = 0.
                if action == 'preset_save':
                    self.model['presets'][str(param)] = {k: self.model[k] for k in self.motion}
                if action == 'preset_recall':
                    preset = self.model['presets'].get(str(param))
                    if preset:
                        self.model.update(preset)
                    else:
                        self.log('INFO', 'Virtual preset is empty; save it first')
                if action == 'preset_reset':
                    self.model['presets'].pop(str(param), None)
                self.log('SIM', ACTIONS[action] + ' ' + str(param))

    def tick(self, axes, pressed, dt=.03):
        with self.lock:
            self.sample_sequence += 1
            self.sample_time = time.monotonic()
            self.updates.notify_all()
            self.input = {'axes': list(axes), 'pressed': sorted(pressed)}
            if self.capture['listening']:
                fresh = sorted(set(pressed) - self.capture_held)
                self.capture_held = set(pressed)
                if fresh:
                    self.capture.update(listening=False, source=fresh[0])
                    self.log('INFO', 'Captured input: ' + fresh[0])
            if not self.armed or not self.transport:
                self.previous = set(pressed)
                return
            motion = dict.fromkeys(self.motion, 0.)
            for axis in self.config['axes']:
                value = axes[axis['index']] if axis['index'] < len(axes) else 0
                motion[axis['target']] = analog_response(value, axis['deadzone'], axis.get('response', 3)) * (-1 if axis['invert'] else 1)
            for binding in self.config['bindings']:
                if binding['source'] not in pressed:
                    continue
                action = binding['action']
                if action == 'stop':
                    self.stop()
                    return
                if action in CONTINUOUS:
                    target, direction = action.split('_')
                    target = 'pan' if target == 'pan' else 'tilt' if target == 'tilt' else target
                    motion[target] = -1 if direction in ('left', 'up', 'out', 'near') else 1
                elif binding['source'] not in self.previous:
                    profile_generation = self.profile_generation
                    self.execute(action, binding.get('param', 0))
                    if self.profile_generation != profile_generation:
                        # Remaining bindings and analog samples belong to the old setup.
                        return
            self.previous = set(pressed)
            # Keep small nonzero inputs: rounding here would enlarge the deadzone
            # after the curve. VISCA packet encoding handles speed quantization.
            motion = {k: v*self.config['speed'] for k, v in motion.items()}
            current = tuple(motion.values())
            old = dict(zip(motion, self.last_motion)) if self.last_motion else dict.fromkeys(motion, 0.)
            releasing = any(old[k] and not motion[k] for k in motion)
            # Poll/render at 60 Hz, but bound network motion updates to 30 Hz.
            # A release bypasses the limit so stop packets are never delayed.
            if current != self.last_motion and (releasing or self.last_motion is None or
                                                self.sample_time-self.last_command_time >= 1/30):
                address = self.config['camera']['address']
                if self.last_motion is None or pantilt(motion['pan'], motion['tilt'], address) != pantilt(old['pan'], old['tilt'], address):
                    self.send(pantilt(motion['pan'], motion['tilt'], address))
                for kind in ('zoom', 'focus'):
                    if self.last_motion is None or lens(kind, motion[kind], address) != lens(kind, old[kind], address):
                        self.send(lens(kind, motion[kind], address))
                self.last_motion = current
                self.last_command_time = self.sample_time
            self.motion = motion
            if self.config['camera']['mode'] == 'simulator':
                for kind, value in motion.items():
                    self.model[kind] = max(-1, min(1, self.model[kind] + value*dt*.4))

    def live_snapshot(self):
        """Small, immutable telemetry frame; no logs/settings/preset deep copy."""
        with self.lock:
            return {'sequence': self.sample_sequence, 'sample_time': self.sample_time,
                    'generation': self.generation, 'input': copy.deepcopy(self.input),
                    'model': {k: self.model[k] for k in self.motion},
                    'motion': self.motion.copy(), 'armed': self.armed,
                    'connected': self.transport is not None,
                    'confirmed': bool(self.transport and self.transport.confirmed),
                    'input_error': self.input_error,
                    'error': self.transport.error if self.transport else '',
                    'capture': self.capture.copy(), **self.preset_state()}

    def wait_for_sample(self, previous):
        with self.updates:
            self.updates.wait_for(lambda: self.sample_sequence != previous or self.quitting.is_set(),
                                  timeout=.5)
            return self.live_snapshot()

    def snapshot(self):
        with self.lock:
            if self.transport and hasattr(self.transport, 'drain_logs'):
                self.transport.drain_logs()
            return copy.deepcopy({'config': self.config, 'devices': self.devices,
                'input': self.input, 'logs': sorted([*self.logs, *self.traffic], key=lambda e: e['sequence']), 'model': self.model,
                'motion': self.motion, 'armed': self.armed, 'connected': self.transport is not None,
                'confirmed': bool(self.transport and self.transport.confirmed),
                'error': self.transport.error if self.transport else '', 'actions': ACTIONS,
                'input_error': self.input_error,
                'backend': self.transport.backend if self.transport else '',
                'presets': [{'name': name, 'camera': config['camera'], 'controller': config['controller']}
                            for name, config in self.presets.items()],
                'capture': self.capture, **self.preset_state()})

    def run(self):
        # All SDL operations stay on this thread. Qt owns the main thread.
        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
        os.environ.setdefault('SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS', '1')
        os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
        pygame = None
        reader = None
        try:
            import pygame
            pygame.display.init()
            pygame.joystick.init()
            try:
                from pygame._sdl2 import controller
                controller.init()
            except (ImportError, pygame.error):
                controller = None
            reader = ControllerReader(pygame, controller)
        except Exception as exc:
            self.input_error = str(exc)
            self.log('ERROR', f'Controller input unavailable: {exc}; virtual input still works')
            pygame = None
        last_scan = 0
        previous_tick = time.monotonic()
        next_tick = previous_tick + 1/60
        try:
            while not self.quitting.wait(max(0, next_tick-time.monotonic())):
                now = time.monotonic()
                dt = min(.1, now-previous_tick)
                previous_tick = now
                next_tick = max(next_tick + 1/60, now)
                try:
                    if pygame:
                        events = pygame.event.get()
                        device_changed = any(event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED)
                                             for event in events)
                        if device_changed or time.monotonic()-last_scan > 1:
                            reader.scan()
                            last_scan = time.monotonic()
                    with self.lock:
                        self.devices = reader.devices if reader else []
                        if self.transport and hasattr(self.transport, 'drain_logs'):
                            self.transport.drain_logs()
                        if self.armed and self.transport and self.transport.error:
                            self.stop()
                        needs_heartbeat = not self.native_window or self.config['controller'] == 'virtual'
                        if self.armed and needs_heartbeat and time.monotonic()-self.heartbeat > 1.5:
                            self.stop()
                            self.log('INFO', 'GUI heartbeat lost; controls disarmed')
                        selected = self.config['controller']
                        reading = reader.read(selected) if reader and selected != 'virtual' else None
                        if selected == 'virtual':
                            axes, pressed = self.virtual['axes'], set(self.virtual['pressed'])
                        elif reading is not None:
                            axes, pressed = reading
                        else:
                            if self.armed:
                                self.stop()
                                self.log('ERROR', 'Selected controller disconnected')
                            axes, pressed = [], set()
                        self.tick(axes, pressed, dt)
                        self.input_error = '' if pygame else self.input_error
                except Exception as exc:
                    with self.lock:
                        self.input_error = str(exc)
                        self.stop()
                        self.log('ERROR', str(exc))
                    last_scan = 0
        finally:
            self.disconnect()
            if pygame:
                pygame.quit()
