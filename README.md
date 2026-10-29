# Linux PTZ Controller

A Flask GUI in a pywebview desktop window. pygame-ce (SDL) reads Linux gamepads;
all buttons and D-pad directions are assignable. Analog pan, tilt, zoom and focus
have independent axis selection, inversion and rescaled deadzones.

## Install and run

Download and run the AppImage installer:

```sh
curl -fsSL https://github.com/Sokoloft/PTZController/releases/latest/download/install.sh | bash
```

It downloads the latest architecture-matched release into `~/.local/bin`, installs
the gamepad icon and application-menu launcher, and preserves existing settings.
The desktop launcher passes `--config ~/.config/ptz-controller/config.json` using
the user's absolute path (or XDG_CONFIG_HOME when set). No root access, pip or git
is needed for this installation. Publish `install.sh` as a release asset alongside
a matching `PTZ_Controller-x86_64.AppImage` or
`PTZ_Controller-aarch64.AppImage` on
[GitHub Releases](https://github.com/Sokoloft/PTZController/releases) first. The
installer defaults to `Sokoloft/PTZController`; its `GITHUB_REPO` setting remains
editable for forks.

For development from source:

Use Python 3.10+ in a virtual environment:

```sh
pip install -r requirements.txt
python src/main.py
```

Print the application version without starting the GUI/server:

```sh
python src/main.py --version
```

The initial application version is 0.1.0, defined in `ptz_controller.__version__`. See
[PACKAGING.md](docs/PACKAGING.md) for the standalone AppImage release workflow
and persistent-configuration behavior.

To build an AppImage on Linux using your project virtual environment:

```sh
.venv/bin/python -m pip install -r requirements.txt pyinstaller
./build.sh
```

The script creates `PTZ_Controller-x86_64.AppImage` (or `aarch64` on ARM64).
It downloads and caches the official appimagetool if it is not installed, and
stops immediately on a build error. Use `PYTHON=/absolute/path/to/python ./build.sh`
to choose another build environment. Build on the oldest Linux distribution you
intend to support, then test the GUI on a clean system before publishing a release.

The desktop uses pywebview's Qt backend. Linux needs a working graphical session
and Qt platform libraries (on Debian/Ubuntu these commonly include libxcb-cursor0,
libxkbcommon-x11-0 and libegl1). Controller access uses SDL; ensure your Linux user
has access to the controller device. No root execution is required.

For GUI development without Qt:

```sh
python src/main.py --browser --port 5000
```

Open the printed localhost URL. This mode still uses Flask, the same input worker,
and the same control engine. Only the desktop window is omitted.

## Try without a camera or controller

1. Leave Virtual camera selected. Open Settings and select Virtual controller. Click Connect.
2. The default axes are 0 pan, 1 tilt and 3 zoom. Move the
   test sliders. The virtual camera crosshair and values move; centering sends stop packets.
3. Add a mapping from `button:0` to Recall preset with parameter `1`, and another
   from `button:1` to Save preset with parameter `1`. Click Connect, move the
   camera, center the sliders, hold B1, move elsewhere, center, and press B0.
4. Enable Include VISCA packets in Activity Log to inspect generated packets. The virtual camera models
   movement, home and preset save/recall/clear. Other commands are logged, not
   physically simulated. Its presets are temporary and disappear at app exit.

For a real controller, select it in Controller: selection immediately switches the
live input monitor, even without a camera connection or arming. Move the sticks and
watch the axis meters; press buttons and watch the Pressed list. Edit mappings,
click Connect to apply the displayed setup and enable motion. Connect saves displayed settings
automatically. Buttons start unmapped; add mappings for the buttons you want to use.
You can use a real controller with Virtual camera to test without any camera.

Selecting a controller disarms motion without disconnecting the camera. Save as preset
to remember a named setup, or click Connect to apply and remember the current setup. Controller IDs
use SDL GUIDs instead of temporary instance IDs. Identical controllers use an ordinal;
if their connection order changes, reselect the desired one. Older instance-ID based
configurations are still readable, but reselect and save to migrate to a stable ID.
The current setup and named presets default to `config.json` next to `src/main.py`
when running from source, or next to the AppImage/executable when packaged. The
location does not depend on the working directory. Override it with
`--config /path/to/config.json`; the installed desktop launcher uses the user's
`.config/ptz-controller/config.json` instead. There is no environment override.
Keep an app-local config directory writable. Existing
settings from `~/.config/ptz-controller/config.json` are copied automatically on
first launch if no local `config.json` exists; the original file is preserved.

## Application presets and mapping capture

Camera Presets below Camera Connection stores complete application setups, separate
from the camera's own position-memory slots. Enter a name and click Save as preset.
Saving an existing name updates it. When disconnected, selecting a preset only loads
its settings; use Connect to enable it. When connected, selecting a preset stops the
old camera and reconnects with the new settings automatically. Unconfirmed or failed
connections do not trigger automatic reconnection on preset selection. Connect and Disconnect
are in the header. Green Connected text means the camera has replied (or the
simulator is ready); an unconfirmed or lost connection displays red Disconnected
text, with more detail in its tooltip. Disconnect stops motion and closes the camera
connection. Connect applies the displayed setup and enables controls; it does not
toggle the connection off. If controls were automatically disarmed, Connect enables
them again. The active preset name appears beside the header status, with edits
marked modified. The interface uses neutral grays, retaining connection-status and
controller-button colors for recognition.
Named presets and the active setup survive restarts; existing configuration files
are migrated automatically. Starting the application does not connect or arm.

Expand Settings for Controller & Motion, Button & D-Pad Mappings, Camera Commands,
and Camera-free Test Bench. Add mapping starts listening: press the intended
controller button or D-pad direction to populate its input source. Held buttons
must be released first. Click an input box or Listen to capture again; Cancel
listening exits capture. Capturing disarms controls and cannot execute a mapping.
Each input may be assigned only once. Click Connect to apply mappings or Save as
preset to store them under a name.

Recognized Xbox and Sony controllers show styled button icons in mappings and the
live pressed-button display: face buttons, bumpers, menu/select, home, D-pad
directions and stick clicks. Digital triggers, touchpad, mute/share and rear paddles
also have icons when SDL identifies them. Sony Select/Start, Share/Options and
Create/Options labels follow the detected controller generation. Additional buttons
on recognized controllers receive numbered button icons.
SDL's device mapping determines which raw source each icon represents. Unrecognized
controllers, unmapped buttons and unavailable device metadata retain their source
strings. Presets still store the original source codes, so decoration does not change
controller behavior. Dropdowns open with one click and support wheel scrolling,
arrow keys, Enter, Escape and typing to find an option.

Activity Log shows connections, preset changes, commands, warnings and errors.
Include VISCA packets reveals packet details. Packet traffic has a separate bounded
history so it cannot displace activity/errors. Heartbeats, live streaming and routine
polling are omitted from the UI log and normal HTTP request logging.

## Camera setup and commands

Live input and simulator positions stream over a persistent SSE connection at
60 Hz. The frontend renders on display animation frames, interpolating a 35 ms
buffer of timestamped measurements rather than predicting camera movement.
Home, preset recall and stop reset the buffer. Meters reuse their DOM elements;
settings and traffic logs refresh separately once per second. GUI heartbeats
remain independent of the stream. Motion packets are limited to 30 Hz, and
releases bypass this limit. Simulator movement uses measured elapsed time.

Choose UDP or TCP, host, port, address and framing from your camera manual:

Test Connection in Camera Connection tests the displayed fields without saving
them, opening a control session or arming the controller. It sends only a read-only
power inquiry and requires a matching VISCA power-state reply for success; a TCP
socket opening alone is insufficient. The result shows response time and on/standby
state, or explains a timeout, invalid reply or camera rejection. Testing a virtual
camera explicitly reports that no physical IP was tested. Results also appear in
Activity Log, and editing the connection fields clears the previous result.
Tests use a separate short-lived socket and do not reset camera sequences, clear
the interface or send motion commands. Successful testing verifies VISCA
communication, rather than support for every camera command.

- Sony-framed UDP uses the installed `visca-over-ip==0.5.1` package, including its
  `Camera.pantilt`, `zoom`, `manual_focus`, preset and other public methods. The
  package handles network framing, sequence numbers and responses. Sony normally
  uses UDP port 52381 and camera address 1. It resets the sequence at connection.
  After connecting, Reset Sony sequence
  sends the sequence reset control packet and disarms controls. Check its reply
  before using Connect to resume; do not reset a camera controlled by another application.
- TCP and raw UDP retain the compatibility transport because `visca_over_ip` supports
  Sony UDP only. Raw VISCA sends only address, command and FF terminator. Ports vary by
  manufacturer. TCP connection success and UDP socket creation do not prove
  camera compatibility: status reports whether a reply has been received.

The built-in catalog covers movement, zoom/focus, autofocus/manual focus, home,
power/standby, presets, exposure, white balance, iris, gain, shutter and backlight.
Custom hex mappings expose additional vendor commands/inquiries using one complete
VISCA packet (3–16 bytes, matching address and ending FF). Supply the VISCA payload,
not the IP header. Custom continuous commands need a separate stop mapping; the
application cannot infer a vendor command's release behavior. One-push white balance
selects the mode; a camera may also require a custom trigger command.

Presets use the camera's memory slot numbering (0–127 accepted by the app; actual
camera limits differ). Standard VISCA does not provide a portable preset-name list,
so recall uses a slot number. Save and clear modify camera memory immediately.
The GUI settings file stores mappings, not camera preset contents.

Motion buttons run while held; other mappings run once per press. Each target has
one analog axis; held motion buttons override that target. Speed caps motion.
Stop disarms. Disconnect, controller loss, window blur, and lost GUI heartbeat also
stop/disarm. A broken network cannot guarantee delivery of a stop packet.
Use the controller only with the GUI focused. Sliders latch until centered.

Package calls run on a dedicated worker so camera timeouts do not block controller
polling or GUI heartbeats. Pending motion updates are coalesced; Stop discards queued
actions and sends stops after the in-flight operation. Package logs show VISCA
payloads (marked `visca_over_ip`), responses consumed by the package and timeouts.
The package's generic command and response hooks are private, so its version is
pinned; public methods handle named operations wherever available. The adapter
uses one attempt per command after startup to avoid retrying non-idempotent actions;
some package methods, such as power, issue repeated commands internally.

The compatibility transport logs raw packet traffic and replies asynchronously.
Reply logging and socket creation are not proof that an operation completed.
Real camera behavior and vendor-specific command support require hardware testing.

## Tests

```sh
python -m unittest discover -s tests -t . -v
node tests/test_gui.cjs
node tests/test_live.cjs
```

Tests cover packet bytes, deadzones, input edges/release, preset simulation,
configuration/API validation, controller selection and stable IDs, command queue
stop behavior, the real package against a localhost UDP camera, and compatibility
TCP/UDP mock-camera traffic. No camera,
controller, desktop session or Qt is needed for the tests.

The optional native dropdown regression uses the Qt backend already installed for
the GUI and sends real mouse, wheel and keyboard events to a temporary local page:

```sh
QT_QPA_PLATFORM=offscreen QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu python tests/qt_controls_smoke.py
```

## Protocol and library references

- [Sony VISCA command reference](https://pro.sony/support/res/manuals/E042/3230148ee903c204b912cca5c2c3f5aa/E0421001M.pdf)
- [PTZOptics network examples](https://docs.ptzoptics.com/dev/examples/)
- [pywebview usage](https://pywebview.flowrl.com/guide/usage.html)
- [pygame joystick API](https://www.pygame.org/docs/ref/joystick.html)
- [visca_over_ip package](https://pypi.org/project/visca-over-ip/0.5.1/)

Flask, pywebview and pygame-ce provide the GUI, HTTP server and controller input.
Sony UDP camera control uses visca_over_ip. The validated command catalog and
simulator are shared with the raw/TCP compatibility transport.
