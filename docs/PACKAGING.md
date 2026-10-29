# Linux distribution and installation

The end-user artifact is an architecture-specific AppImage. `build.sh` freezes the
application with PyInstaller, stages an AppDir and packages it with appimagetool.
Publish an AppImage release before using the installer with a real repository.

## Repository layout

```
install/
  install.sh
  ptz-controller.desktop     # installer template; not a launcher to copy as-is
  icon.svg
requirements.txt
src/
  main.py                    # source / PyInstaller entry point
  config.json                # local user settings, ignored by git
  ptz_controller/
    cli.py                   # CLI and server/window lifecycle
    app.py                   # Flask API
    engine.py                # input, presets, simulator
    config.py
    controllers.py
    package_camera.py
    probe.py
    visca.py
    static/
    templates/
tests/
```

## GitHub release contract

Edit `GITHUB_REPO` near the top of install/install.sh to `OWNER/REPO` or a full HTTPS
GitHub repository URL. It can also be set in the environment. The installer expects
these release assets, named consistently on each release:

- `PTZ_Controller-x86_64.AppImage` for x86-64 Linux
- `PTZ_Controller-aarch64.AppImage` for ARM64 Linux (when supported/built)

`APPIMAGE_ASSET` can override the filename for versioned release assets.
`RELEASE_TAG` defaults to `latest` or may name a specific tag. `GITHUB_REF` defaults
to `main` and selects the repository branch/tag containing `install/icon.svg`
and `install/ptz-controller.desktop`. Upload the assets to GitHub Releases; committing a
filename or publishing source archives alone is insufficient.

The installer downloads the AppImage from the release download URL and the two
small desktop assets from raw.githubusercontent.com. It requires curl or GNU wget,
Bash and normal Linux core utilities; Python/pip/git are not installation
requirements. Running it as the desktop user installs:

- `~/.local/bin/ptz-controller.AppImage`
- `${XDG_DATA_HOME:-~/.local/share}/icons/hicolor/scalable/apps/ptz-controller.svg`
- `${XDG_DATA_HOME:-~/.local/share}/applications/ptz-controller.desktop`

It substitutes quoted absolute executable/config paths into the launcher; desktop
entries do not expand `~` or `$HOME` themselves. The launcher always passes
`--config "${XDG_CONFIG_HOME:-$HOME/.config}/ptz-controller/config.json"` (expanded
at installation time). Existing configuration contents are never overwritten by
installation. All downloads complete and basic validation succeeds before an
existing installation is replaced. AppImage replacement is atomic. Installer tests
use local downloader fixtures and never access GitHub or install into the real home.

## Build approach

Install `requirements.txt` and `pyinstaller` in the project virtual environment,
then run `./build.sh`. It uses the active virtual environment, `.venv-build`, or
`.venv`, in that order. `PYTHON=/absolute/path/to/python ./build.sh` overrides this.
The script works from any working directory, detects the host architecture, and
creates `PTZ_Controller-x86_64.AppImage` or `PTZ_Controller-aarch64.AppImage` in the
repository root. It never includes the developer's config.json.

An installed `appimagetool` or explicit `APPIMAGETOOL=/absolute/path/to/tool` is
used when available. Otherwise the official appimagetool 1.9.1 release is downloaded
with curl/wget into `build/tools`, checked against its release SHA-256 digest and
reused on later builds. The tool runs in extract-and-run mode so the build does
not require FUSE. appimagetool may also download its AppImage runtime, so initial
packaging requires internet access. Errors stop the script; an existing finished
AppImage is replaced only after the new artifact passes its `--version` check.

1. Build in a clean Linux container/VM representing the oldest supported Linux
   baseline, with a supported Python/Qt combination. A build made on current Arch
   should not be assumed to work on older distributions.
2. Lock release dependencies in a dedicated build environment. requirements.txt
   currently contains development-compatible ranges, not a release lock.
3. Freeze `src/main.py` with PyInstaller in **onedir** mode. Include
   `src/ptz_controller/templates` at `ptz_controller/templates` and
   `src/ptz_controller/static` at `ptz_controller/static`. Bundle Flask, pywebview's
   Qt backend, PyQt6/WebEngine, pygame-ce/SDL and visca_over_ip; check dynamically
   imported `ptz_controller` modules, `pygame._sdl2.controller` and
   `webview.platforms.qt`. Exclude unused GUI backends.
4. Verify QtWebEngineProcess, Qt plugins, WebEngine resources/locales, shared
   libraries and third-party license notices are included. Test the frozen
   directory independently.
5. Place it in an AppDir with an AppRun launcher forwarding arguments and preserving
   APPIMAGE/APPDIR. The AppDir's internal .desktop should have an executable name
   suitable for AppRun; do not copy the user-install template without adapting it.
   Use install/icon.svg for desktop integration and appimagetool to make the AppImage.
   Avoid nesting PyInstaller onefile inside AppImage.
6. Build separately for supported architectures. Test on clean systems without
   Python, pip or Qt: startup, menus, controller capture, simulator, TCP/UDP probes,
   presets, shutdown and --version. Test as a normal desktop user.

The version source is `ptz_controller.__version__`; source and bundled `--version`
output must match. Do not bundle developer config.json or the tests as user data.

## Standalone behavior and configuration

The AppImage should bundle Python and application libraries, while relying on the
supported Linux graphics stack, controller drivers/device permissions and base
system libraries. Its runtime may need FUSE; extraction is a fallback. Qt/Chromium
makes the download substantial. A successful build and `--version` check do not
replace GUI and controller testing on the supported target distributions.

If launching reports missing FUSE/fusermount support, use extraction mode:

```sh
APPIMAGE_EXTRACT_AND_RUN=1 ./PTZ_Controller-x86_64.AppImage
```

The current x86-64 build has passed `--version`, packaged browser/API/static-asset
checks and an offscreen Qt startup check using a temporary configuration. These
checks used extraction mode because FUSE is unavailable in the build environment.
Testing on clean target distributions and with camera hardware remains necessary.

Direct launches default to `config.json` beside `src/main.py`, the AppImage, or a
plain frozen executable. This path is independent of the working directory. Use
`--config PATH` to choose another file; the desktop launcher uses this to store
settings under the user's .config directory. There is no PTZ_CONFIG environment
override. Moving the app and an app-local config.json together carries settings;
updating only the AppImage in the same folder preserves them.

If the default local file is absent, settings from the former
`~/.config/ptz-controller/config.json` location are copied once, preserving the
original. Explicit --config paths are used directly. Camera position presets remain
in camera memory; the virtual camera's position presets are temporary.

## References

- https://pywebview.flowrl.com/guide/freezing.html
- https://pyinstaller.org/en/stable/runtime-information.html
- https://docs.appimage.org/packaging-guide/environment-variables.html
- https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases
- https://specifications.freedesktop.org/desktop-entry-spec/latest/exec-variables.html
