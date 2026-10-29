"""visca-over-ip 0.5.1 adapter.

Only this module depends on the package's private hooks, for logging and custom
commands. Public Camera methods perform movement and supported named actions.
All blocking Camera operations run on one worker; input polling never waits for UDP.
"""
from collections import deque
from queue import Empty, SimpleQueue
import threading
from visca_over_ip import Camera
from .visca import pantilt, lens


class LoggedCamera(Camera):
    def __init__(self, host, port, emit):
        self.emit = emit
        self.received = False
        try:
            super().__init__(host, port)
            # Avoid repeating non-idempotent commands when a camera returns errors.
            self.num_retries = 1
        except Exception:
            if hasattr(self, '_sock'):
                self.close_connection()
            raise

    def _send_command(self, command_hex, query=False):
        self.emit('TX', ('81 09 ' if query else '81 01 ') +
                  bytes.fromhex(command_hex).hex(' ') + ' ff [visca_over_ip]')
        return super()._send_command(command_hex, query=query)

    def _receive_response(self):
        try:
            result = super()._receive_response()
        except Exception as exc:
            self.emit('ERROR', str(exc))
            raise
        if result is not None:
            self.received = True
            self.emit('RX', result.hex(' '))
        else:
            self.emit('WARN', 'No camera reply (visca_over_ip timeout)')
        return result

    def reset_sequence_number(self):
        self.emit('INFO', 'visca_over_ip: resetting camera sequence')
        return super().reset_sequence_number()


def dispatch(camera, payload):
    """Translate validated app payloads to the package's public Camera API."""
    body = payload[2:-1]
    if payload[1] == 1 and len(body) == 6 and body[:2] == b'\x06\x01':
        pan = body[2] * (-1 if body[4] == 1 else 1) if body[4] != 3 else 0
        tilt = body[3] * (-1 if body[5] == 1 else 1) if body[5] != 3 else 0
        return camera.pantilt(pan, tilt)
    if payload[1] == 1 and len(body) == 3 and body[:2] in (b'\x04\x07', b'\x04\x08'):
        code = body[2]
        speed = (code & 15) * (1 if code & 0xf0 == 0x20 else -1) if code else 0
        if body[1] == 7:
            return camera.zoom(speed)
        # In this library positive manual_focus is near (0x3p).
        return camera.manual_focus(-speed)
    if payload[1] == 1 and len(body) == 4 and body[:2] == b'\x04\x3f' and body[2] in (1, 2):
        method = camera.save_preset if body[2] == 1 else camera.recall_preset
        return method(body[3])
    named = {
        b'\x06\x04': ('pantilt_home', ()),
        b'\x04\x38\x02': ('set_focus_mode', ('auto',)),
        b'\x04\x38\x03': ('set_focus_mode', ('manual',)),
        b'\x04\x00\x02': ('set_power', (True,)),
        b'\x04\x00\x03': ('set_power', (False,)),
        b'\x04\x39\x00': ('autoexposure_mode', ('auto',)),
        b'\x04\x39\x03': ('autoexposure_mode', ('manual',)),
        b'\x04\x35\x00': ('white_balance_mode', ('auto',)),
        b'\x04\x35\x03': ('white_balance_mode', ('one push',)),
        b'\x04\x33\x02': ('backlight', (True,)),
        b'\x04\x33\x03': ('backlight', (False,)),
    }
    for code, name in ((0x0a, 'shutter'), (0x0b, 'iris'), (0x0c, 'gain')):
        named[bytes([4, code, 2])] = ('increase_' + name, ())
        named[bytes([4, code, 3])] = ('decrease_' + name, ())
    if payload[1] == 1 and body in named:
        method, args = named[body]
        return getattr(camera, method)(*args)
    # The package has no public generic command API (including preset clear).
    return camera._send_command(body.hex(' '), query=payload[1] == 9)


def motion_group(payload):
    if payload[1:4] == b'\x01\x06\x01':
        return 'pantilt'
    if payload[1:4] in (b'\x01\x04\x07', b'\x01\x04\x08'):
        return str(payload[3])
    return None


class PackageTransport:
    backend = 'visca_over_ip 0.5.1 · Sony UDP'

    def __init__(self, settings, log):
        self.settings, self.log = settings, log
        self.error = ''
        self.events = SimpleQueue()
        self.condition = threading.Condition()
        self.pending = deque()
        self.closing = False
        self.camera = None
        self.worker = threading.Thread(target=self._run, daemon=True, name='visca-over-ip')
        self.worker.start()

    @property
    def confirmed(self):
        return bool(self.camera and self.camera.received)

    def drain_logs(self):
        while True:
            try:
                kind, message = self.events.get_nowait()
            except Empty:
                return
            self.log(kind, message)

    def _enqueue(self, job):
        with self.condition:
            if self.closing:
                raise OSError('Camera connection is closing')
            self.pending.append(job)
            self.condition.notify()

    def send(self, payload):
        with self.condition:
            if self.error:
                raise OSError(self.error + '; reconnect the camera')
            group = motion_group(payload)
            if group:
                self.pending = deque(job for job in self.pending
                                     if job[0] != 'send' or motion_group(job[1]) != group)
            if len(self.pending) >= 64:
                raise ValueError('Camera command queue is full')
            self._enqueue(('send', payload))

    def stop(self, address=1):
        with self.condition:
            # Cancel queued movement and actions; only an in-flight operation may finish.
            self.pending.clear()
            self._enqueue(('stop', None))

    def reset_sequence(self):
        with self.condition:
            if self.error:
                raise OSError(self.error + '; reconnect the camera')
            self.pending.clear()
            self._enqueue(('stop', None))
            self._enqueue(('reset', None))

    def _run(self):
        def emit(kind, message):
            self.events.put((kind, message))
        try:
            self.camera = LoggedCamera(self.settings['host'], self.settings['port'], emit)
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.pending or self.closing)
                    if not self.pending:
                        break
                    kind, payload = self.pending.popleft()
                try:
                    if kind == 'reset':
                        self.camera.reset_sequence_number()
                    elif kind == 'stop':
                        # Try every stop even if one axis is rejected by the camera.
                        for data in (pantilt(0, 0), lens('zoom', 0), lens('focus', 0)):
                            try:
                                dispatch(self.camera, data)
                            except Exception as exc:
                                emit('ERROR', f'Stop failed: {exc}')
                    else:
                        dispatch(self.camera, payload)
                except Exception as exc:
                    self.error = str(exc)
                    emit('ERROR', self.error)
                    with self.condition:
                        self.pending.clear()
                        self.pending.append(('stop', None))
        except Exception as exc:
            self.error = str(exc)
            emit('ERROR', self.error)
        finally:
            if self.camera:
                self.camera.close_connection()

    def close(self):
        with self.condition:
            self.pending.clear()
            self.pending.append(('stop', None))
            self.closing = True
            self.condition.notify()
        # Worker events never acquire the engine lock, so joining is safe here.
        self.worker.join(timeout=3)
        self.drain_logs()
