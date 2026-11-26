"""Serialized UDP commands with bounded, coalesced motion queues.

Send validated VISCA bytes unchanged, including legal nonzero stop speeds.
Worker logs are drained by the engine; network waits never hold its lock.
"""
from collections import deque
from queue import Empty, SimpleQueue
import socket
import struct
import threading
import time
from .visca import pantilt, lens, frame


class WireCamera:
    def __init__(self, settings, emit):
        self.settings, self.emit = settings, emit
        self.received = False
        self.sequence = 0
        self.last_send = 0.
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.connect((settings['host'], settings['port']))
            if settings['framing'] == 'sony':
                self.reset_sequence_number()
        except Exception:
            self.sock.close()
            raise

    def _exchange(self, wire, sequence=None, reset=False, payload=None):
        # Leave at least one video cycle between commands, even with instant ACKs.
        time.sleep(max(0, .05 - (time.monotonic() - self.last_send)))
        self.sock.send(wire)
        self.last_send = time.monotonic()
        self.emit('TX', wire.hex(' '))
        deadline = time.monotonic() + .3
        while time.monotonic() < deadline:
            self.sock.settimeout(max(.001, deadline - time.monotonic()))
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                break
            if self.settings['framing'] == 'sony':
                if len(data) < 8:
                    continue
                kind, size, reply_sequence = struct.unpack('!HHI', data[:8])
                if len(data) != 8 + size or reply_sequence != sequence:
                    continue
                data = data[8:]
                if reset:
                    if kind == 0x0201 and data == b'\x01':
                        return
                    continue
                if kind == 0x0201:
                    self.emit('ERROR', 'VISCA network error: ' + data.hex(' ') + '; try sequence reset')
                    return
                if kind != 0x0111:
                    continue
            for reply in data.split(b'\xff')[:-1]:
                if len(reply) < 2 or reply[0] != (self.settings['address'] + 8) << 4:
                    continue
                self.received = True
                self.emit('RX', (reply + b'\xff').hex(' '))
                status = reply[1] & 0xf0
                if status == 0x60:
                    reason = {1: 'message length error', 2: 'syntax error / unsupported command',
                              3: 'command buffer full', 4: 'command canceled',
                              5: 'no socket', 0x41: 'command not executable'}.get(
                                  reply[2] if len(reply) > 2 else 0, 'unknown error')
                    self.emit('ERROR', f'Camera rejected {payload.hex(" ")}: {reason} '
                              f'({(reply + bytes([255])).hex(" ")})')
                    return
                if status == 0x40 and payload[1] != 9:
                    return
                if status == 0x50:
                    # Raw UDP has no sequence; discard delayed socket completions.
                    if self.settings['framing'] == 'sony' or reply[1] == 0x50:
                        return
        self.emit('WARN', 'No matching camera reply for ' + wire.hex(' ') +
                  '; command was not retried')

    def send_payload(self, payload):
        sony = self.settings['framing'] == 'sony'
        sequence = self.sequence
        wire = frame(payload, sequence, payload[1] == 9) if sony else payload
        self.sequence = (sequence + 1) & 0xffffffff
        self._exchange(wire, sequence, payload=payload)

    def reset_sequence_number(self):
        self._exchange(bytes.fromhex('02 00 00 01 00 00 00 00 01'), 0, reset=True)
        self.sequence = 0

    def close_connection(self):
        self.sock.close()


def motion_group(payload):
    if payload[1:4] == b'\x01\x06\x01':
        return 'pantilt'
    if payload[1:4] in (b'\x01\x04\x07', b'\x01\x04\x08'):
        return str(payload[3])
    return None


class PackageTransport:
    backend = 'Queued VISCA UDP'

    def __init__(self, settings, log):
        self.settings, self.log = settings, log
        self.backend += ' · Sony' if settings['framing'] == 'sony' else ' · Raw'
        self.error = ''
        self.events = SimpleQueue()
        self.condition = threading.Condition()
        self.pending = deque()
        self.closing = False
        self.camera = None
        self.worker = threading.Thread(target=self._run, daemon=True, name='visca-udp')
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
            self.camera = WireCamera(self.settings, emit)
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
                        address = self.settings['address']
                        for data in (pantilt(0, 0, address), lens('zoom', 0, address),
                                     lens('focus', 0, address)):
                            try:
                                self.camera.send_payload(data)
                            except Exception as exc:
                                emit('ERROR', f'Stop failed: {exc}')
                    else:
                        self.camera.send_payload(payload)
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
