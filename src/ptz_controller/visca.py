"""Command validation and raw/TCP compatibility transport.

Sony UDP camera control uses visca_over_ip through package_camera.py.
"""
import socket
import struct
import threading
import time


ACTIONS = {
    'none': 'Unmapped', 'stop': 'Stop all motion', 'home': 'Pan/tilt home',
    'pan_left': 'Pan left', 'pan_right': 'Pan right', 'tilt_up': 'Tilt up',
    'tilt_down': 'Tilt down', 'zoom_in': 'Zoom in', 'zoom_out': 'Zoom out',
    'focus_near': 'Focus near', 'focus_far': 'Focus far',
    'autofocus': 'Autofocus', 'manual_focus': 'Manual focus',
    'power_on': 'Power on', 'power_off': 'Standby',
    'preset_recall': 'Recall preset', 'preset_save': 'Save preset',
    'preset_reset': 'Clear preset', 'exposure_auto': 'Auto exposure',
    'exposure_manual': 'Manual exposure', 'white_balance_auto': 'Auto white balance',
    'white_balance_one_push': 'One-push white balance',
    'iris_up': 'Iris up', 'iris_down': 'Iris down',
    'gain_up': 'Gain up', 'gain_down': 'Gain down',
    'shutter_up': 'Shutter up', 'shutter_down': 'Shutter down',
    'backlight_on': 'Backlight on', 'backlight_off': 'Backlight off',
    'custom': 'Custom VISCA hex',
}
CONTINUOUS = {'pan_left', 'pan_right', 'tilt_up', 'tilt_down', 'zoom_in',
              'zoom_out', 'focus_near', 'focus_far'}


def packet(body, address=1):
    return bytes([0x80 + address]) + bytes(body) + b'\xff'


def pantilt(pan, tilt, address=1):
    return packet([1, 6, 1, max(1, round(abs(pan)*24)), max(1, round(abs(tilt)*20)),
                   2 if pan > 0 else 1 if pan < 0 else 3,
                   2 if tilt > 0 else 1 if tilt < 0 else 3], address)


def lens(kind, value, address=1):
    speed = min(7, max(1, round(abs(value)*7))) if value else 0
    return packet([1, 4, 7 if kind == 'zoom' else 8,
                   (0x20 if value > 0 else 0x30) + speed if value else 0], address)


def command(action, param=0, address=1):
    fixed = {
        'home': [1, 6, 4], 'autofocus': [1, 4, 0x38, 2],
        'manual_focus': [1, 4, 0x38, 3], 'power_on': [1, 4, 0, 2],
        'power_off': [1, 4, 0, 3], 'exposure_auto': [1, 4, 0x39, 0],
        'exposure_manual': [1, 4, 0x39, 3], 'white_balance_auto': [1, 4, 0x35, 0],
        'white_balance_one_push': [1, 4, 0x35, 3],
        'backlight_on': [1, 4, 0x33, 2], 'backlight_off': [1, 4, 0x33, 3],
    }
    if action in fixed:
        return packet(fixed[action], address)
    if action.startswith('preset_'):
        number = int(param)
        if not 0 <= number <= 127:
            raise ValueError('Preset must be 0–127 (camera limits may be lower)')
        return packet([1, 4, 0x3f, {'preset_recall': 2, 'preset_save': 1,
                                  'preset_reset': 0}[action], number], address)
    for name, code in [('iris', 0x0b), ('gain', 0x0c), ('shutter', 0x0a)]:
        if action in (name+'_up', name+'_down'):
            return packet([1, 4, code, 2 if action.endswith('_up') else 3], address)
    if action == 'custom':
        raw = bytes.fromhex(str(param))
        if not 3 <= len(raw) <= 16 or raw[0] != 0x80+address or raw[-1] != 255 or 255 in raw[1:-1]:
            raise ValueError('Use one complete VISCA packet, matching address, ending FF (3–16 bytes)')
        return raw
    raise ValueError('Unknown command')


def frame(payload, sequence, inquiry=False):
    return struct.pack('!HHI', 0x0110 if inquiry else 0x0100, len(payload), sequence) + payload


class Transport:
    backend = 'Built-in compatibility transport'
    def __init__(self, settings, log):
        self.settings, self.log = settings, log
        self.sequence = 0
        self.sock = None
        self.closed = threading.Event()
        self.error = ''
        self.confirmed = False
        if settings['mode'] != 'simulator':
            tcp = settings['mode'] == 'tcp'
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
            self.sock.settimeout(1)
            try:
                self.sock.connect((settings['host'], settings['port']))
            except Exception:
                self.sock.close()
                raise
            self.sock.settimeout(.2)
            self.reader = threading.Thread(target=self._receive, daemon=True)
            self.reader.start()

    def send(self, payload):
        data = frame(payload, self.sequence, payload[1] == 9) if self.settings['framing'] == 'sony' else payload
        self.sequence = (self.sequence + 1) & 0xffffffff
        if self.sock:
            if self.error:
                raise OSError(self.error)
            if self.settings['mode'] == 'tcp':
                self.sock.sendall(data)
            else:
                self.sock.send(data)
        self.log('TX', data.hex(' '))

    def _receive(self):
        buffer = b''
        while not self.closed.is_set():
            try:
                data = self.sock.recv(4096)
                if not data:
                    raise OSError('Camera closed connection')
                if self.settings['mode'] == 'udp':
                    buffer = data
                else:
                    buffer += data
                while buffer:
                    if self.settings['framing'] == 'sony':
                        if len(buffer) < 8:
                            break
                        size = int.from_bytes(buffer[2:4], 'big') + 8
                        if size > 4096:
                            raise OSError('Invalid VISCA frame length')
                        if len(buffer) < size:
                            break
                        message, buffer = buffer[8:size], buffer[size:]
                    else:
                        end = buffer.find(b'\xff')
                        if end < 0:
                            if len(buffer) > 4096:
                                raise OSError('Unterminated VISCA reply')
                            break
                        message, buffer = buffer[:end+1], buffer[end+1:]
                    self.confirmed = True
                    self.log('RX', message.hex(' '))
                    if len(message) > 1 and message[1] & 0xf0 == 0x60:
                        self.log('ERROR', 'Camera rejected command: ' + message.hex(' '))
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.closed.is_set():
                    self.error = str(exc)
                    self.log('ERROR', self.error)
                break

    def close(self):
        self.closed.set()
        if self.sock:
            self.sock.close()

    def stop(self, address=1):
        for data in (pantilt(0, 0, address), lens('zoom', 0, address), lens('focus', 0, address)):
            self.send(data)

    def reset_sequence(self):
        raw = bytes.fromhex('02 00 00 01 00 00 00 00 01')
        if self.sock:
            if self.settings['mode'] == 'tcp':
                self.sock.sendall(raw)
            else:
                self.sock.send(raw)
        self.sequence = 0
        self.log('TX', raw.hex(' '))


def open_transport(settings, log):
    if settings['mode'] == 'udp' and settings['framing'] == 'sony':
        from .package_camera import PackageTransport
        return PackageTransport(settings, log)
    return Transport(settings, log)
