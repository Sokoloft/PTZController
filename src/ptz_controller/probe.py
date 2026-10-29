"""A one-shot, read-only VISCA reachability test.

Use a separate socket so an existing control connection and its receiver stay
untouched. Do not construct visca_over_ip.Camera here: its constructor resets the
camera sequence and clears the interface. The probe sends only CAM_PowerInq.
"""
import socket
import struct
import time
from .visca import frame, packet


def probe_connection(settings, log=lambda *_: None, timeout=1.5):
    settings = dict(settings)
    if settings['mode'] == 'simulator':
        return {'status': 'simulator', 'responded': False, 'latency_ms': None,
                'message': 'Virtual camera selected. No physical camera or IP address was tested.'}
    tcp = settings['mode'] == 'tcp'
    sony = settings['framing'] == 'sony'
    sequence = 0
    inquiry = packet([9, 4, 0], settings['address'])
    wire = frame(inquiry, sequence, inquiry=True) if sony else inquiry
    peer = f"{settings['host']}:{settings['port']}"
    started = time.monotonic()
    acknowledged = False
    received_data = False
    buffer = b''

    def result(status, message, responded=False):
        return {'status': status, 'responded': responded,
                'latency_ms': round((time.monotonic()-started)*1000, 1) if responded else None,
                'message': message}

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            # Connected UDP also rejects datagrams from any other address/port.
            sock.connect((settings['host'], settings['port']))
            deadline = started + timeout
            if tcp:
                sock.sendall(wire)
            else:
                sock.send(wire)
            log('TX', '[connection test] ' + wire.hex(' '))
            while time.monotonic() < deadline:
                sock.settimeout(max(.001, deadline-time.monotonic()))
                data = sock.recv(4096)
                if not data:
                    break
                received_data = True
                buffer = buffer + data if tcp else data
                while buffer:
                    kind = 0x0111
                    if sony:
                        if len(buffer) < 8:
                            break
                        kind, length, reply_sequence = struct.unpack('!HHI', buffer[:8])
                        if not 1 <= length <= 16:
                            return result('invalid', f'{peer} returned malformed data. Check VISCA framing and port.')
                        if len(buffer) < 8+length:
                            break
                        payload, buffer = buffer[8:8+length], buffer[8+length:]
                        if reply_sequence != sequence:
                            continue
                        if kind not in (0x0111, 0x0201):
                            continue
                    else:
                        end = buffer.find(b'\xff')
                        if end < 0:
                            if len(buffer) > 64:
                                return result('invalid', f'{peer} returned non-VISCA data. Check framing and port.')
                            break
                        payload, buffer = buffer[:end+1], buffer[end+1:]
                    log('RX', '[connection test] ' + payload.hex(' '))
                    if kind == 0x0201 and payload[:1] == b'\x0f':
                        return result('rejected', f'{peer} replied with a VISCA network error ({payload.hex(" ")}). Check framing and camera sequence settings.', True)
                    header = (settings['address'] + 8) << 4
                    if not (3 <= len(payload) <= 16 and payload[0] == header and payload[-1] == 255):
                        continue
                    if len(payload) == 4 and payload[1] == 0x50 and payload[2] in (2, 3):
                        power = 'on' if payload[2] == 2 else 'standby'
                        ms = (time.monotonic()-started)*1000
                        return result('success', f'Camera responded at {peer} ({settings["mode"].upper()}) in {ms:.0f} ms. VISCA communication verified; camera power is {power}.', True)
                    if payload[1] & 0xf0 == 0x60:
                        return result('rejected', f'{peer} replied but rejected the power inquiry ({payload.hex(" ")}). The device is reachable; check camera VISCA support/settings.', True)
                    if len(payload) == 3 and payload[1] & 0xf0 == 0x40:
                        acknowledged = True
    except socket.timeout:
        pass
    except OSError as exc:
        return result('error', f'Could not verify {peer}: {exc}. Check the IP/hostname, port, transport and network.')
    if acknowledged:
        return result('incomplete', f'{peer} acknowledged the inquiry but did not return its power state. Communication was not fully verified.', True)
    if received_data:
        return result('invalid', f'{peer} sent data, but no matching VISCA inquiry reply was received. Check framing, address and port.')
    return result('timeout', f'No VISCA reply from {peer} within {timeout:g} seconds. Check the IP/hostname, port, transport, framing, camera VISCA settings and firewall.')
