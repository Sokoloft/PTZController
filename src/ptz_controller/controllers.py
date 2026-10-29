"""SDL joystick discovery and reads, including stable IDs across restarts."""
import re


def face_labels(name, guid, mapping, button_count=0, hat_count=0):
    """Decorate every recognized controller input without changing raw bindings."""
    name = name.lower()
    vendor = guid[8:12].lower()
    if re.search(r'xbox|x-box|xinput', name) or vendor == '5e04':
        family = 'xbox'
        faces = {'a': ('A', 'A'), 'b': ('B', 'B'), 'x': ('X', 'X'), 'y': ('Y', 'Y')}
    elif re.search(r'sony|playstation|dualshock|dualsense|\bps[345]\b', name) or vendor == '4c05':
        family = 'sony'
        faces = {'a': ('Cross', '×'), 'b': ('Circle', '○'), 'x': ('Square', '□'), 'y': ('Triangle', '△')}
    else:
        return {}
    legacy_xbox = '360' in name
    ps3 = bool(re.search(r'dualshock\s*3|playstation.*3|\bps3\b', name)) or guid[16:20].lower() == '6802'
    ps5 = bool(re.search(r'dualsense|\bps5\b', name)) or guid[16:20].lower() in ('e60c', 'f20d')
    if family == 'xbox':
        controls = {
            'leftshoulder': ('Left bumper (LB)', 'LB', 'leftshoulder'),
            'rightshoulder': ('Right bumper (RB)', 'RB', 'rightshoulder'),
            'lefttrigger': ('Left trigger (LT)', 'LT', 'lefttrigger'),
            'righttrigger': ('Right trigger (RT)', 'RT', 'righttrigger'),
            'back': ('Back / Select' if legacy_xbox else 'View / Select', 'View', 'legacy-back' if legacy_xbox else 'back'),
            'start': ('Start' if legacy_xbox else 'Menu / Start', 'Menu', 'legacy-start' if legacy_xbox else 'start'),
            'guide': ('Xbox Home', 'Xbox', 'guide'),
            'leftstick': ('Left stick click (LS)', 'LS', 'leftstick'),
            'rightstick': ('Right stick click (RS)', 'RS', 'rightstick'),
            'misc1': ('Share', 'Share', 'share'),
        }
    else:
        controls = {
            'leftshoulder': ('Left bumper (L1)', 'L1', 'leftshoulder'),
            'rightshoulder': ('Right bumper (R1)', 'R1', 'rightshoulder'),
            'lefttrigger': ('Left trigger (L2)', 'L2', 'lefttrigger'),
            'righttrigger': ('Right trigger (R2)', 'R2', 'righttrigger'),
            'back': ('Select' if ps3 else 'Create' if ps5 else 'Share',
                     'Select' if ps3 else 'Create' if ps5 else 'Share', 'select' if ps3 else 'back' if ps5 else 'share'),
            'start': ('Start' if ps3 else 'Options', 'Start' if ps3 else 'Options', 'legacy-start' if ps3 else 'start'),
            'guide': ('PlayStation Home', 'PS', 'guide'),
            'leftstick': ('Left stick click (L3)', 'L3', 'leftstick'),
            'rightstick': ('Right stick click (R3)', 'R3', 'rightstick'),
            'touchpad': ('Touchpad click', 'Touch', 'touchpad'),
            'misc1': ('Microphone mute' if ps5 else 'Extra button', 'Mute' if ps5 else '•',
                      'mute' if ps5 else 'extra'),
        }
    for direction, arrow in (('up', '↑'), ('down', '↓'), ('left', '←'), ('right', '→')):
        controls['dp' + direction] = ('D-pad ' + direction, arrow, 'dpad-' + direction)
    for number in range(1, 5):
        controls['paddle' + str(number)] = ('Rear paddle ' + str(number), 'P' + str(number), 'paddle')
    labels = {}
    for semantic, (label, symbol) in faces.items():
        match = re.fullmatch(r'b(\d+)', str(mapping.get(semantic, '')))
        if match:
            style = semantic if family == 'xbox' else label.lower()
            labels['button:' + match[1]] = {'label': label, 'symbol': symbol, 'style': family + '-' + style}

    def descriptor(label, symbol, icon):
        return {'label': label, 'symbol': symbol, 'style': family + '-' + icon,
                'icon': family + '-' + icon}

    for semantic, (label, symbol, icon) in controls.items():
        binding = str(mapping.get(semantic, ''))
        button = re.fullmatch(r'b(\d+)', binding)
        hat = re.fullmatch(r'h(\d+)\.(1|2|4|8)', binding)
        if button:
            labels['button:' + button[1]] = descriptor(label, symbol, icon)
        elif hat:
            direction = {'1': 'up', '2': 'right', '4': 'down', '8': 'left'}[hat[2]]
            labels[f'hat:{hat[1]}:{direction}'] = descriptor(label, symbol, icon)
    for index in range(button_count):
        labels.setdefault(f'button:{index}', {'label': f'Button {index}', 'symbol': f'B{index}',
                                             'style': family + '-extra'})
    for index in range(hat_count):
        for direction, arrow in (('up', '↑'), ('down', '↓'), ('left', '←'), ('right', '→')):
            labels.setdefault(f'hat:{index}:{direction}',
                              descriptor(f'Hat {index} {direction}', arrow, 'dpad-' + direction))
    return labels


class ControllerReader:
    def __init__(self, pygame, controller_api=None):
        self.pygame = pygame
        self.joysticks = {}
        self.by_id = {}
        self.devices = []
        self.controller_api = controller_api
        self.controllers = {}

    def scan(self):
        current = {}
        controllers = {}
        counts = {}
        self.devices = []
        self.by_id = {}
        for index in range(self.pygame.joystick.get_count()):
            candidate = self.pygame.joystick.Joystick(index)
            instance = candidate.get_instance_id()
            joy = self.joysticks.get(instance, candidate)
            # pygame-ce initializes Joystick objects on construction.
            current[instance] = joy
            guid = joy.get_guid()
            ordinal = counts.get(guid, 0)
            counts[guid] = ordinal + 1
            key = f'{guid}:{ordinal}'
            self.by_id[key] = joy
            # Accept configurations saved by older versions using instance IDs.
            self.by_id[str(instance)] = joy
            mapping = {}
            if self.controller_api:
                try:
                    controller = self.controllers.get(instance)
                    if controller is None and self.controller_api.is_controller(index):
                        controller = self.controller_api.Controller(index)
                    if controller is not None:
                        controllers[instance] = controller
                        mapping = controller.get_mapping()
                except self.pygame.error:
                    pass  # Keep raw input usable when SDL has no controller mapping.
            self.devices.append({'id': key, 'name': joy.get_name(), 'axes': joy.get_numaxes(),
                                 'buttons': joy.get_numbuttons(), 'hats': joy.get_numhats(),
                                 'button_labels': face_labels(joy.get_name(), guid, mapping,
                                                             joy.get_numbuttons(), joy.get_numhats())})
        self.joysticks = current
        self.controllers = controllers

    def read(self, selected):
        joy = self.by_id.get(selected)
        if joy is None:
            return None
        axes = [joy.get_axis(i) for i in range(joy.get_numaxes())]
        pressed = {f'button:{i}' for i in range(joy.get_numbuttons()) if joy.get_button(i)}
        for i in range(joy.get_numhats()):
            x, y = joy.get_hat(i)
            for active, direction in ((x < 0, 'left'), (x > 0, 'right'),
                                      (y > 0, 'up'), (y < 0, 'down')):
                if active:
                    pressed.add(f'hat:{i}:{direction}')
        return axes, pressed
