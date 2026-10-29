"""Locate writable user settings beside the source app or distributed executable."""
import os
from pathlib import Path
import sys


def default_config_path():
    if getattr(sys, 'frozen', False):
        # APPIMAGE points outside the read-only mount; sys.executable points inside it.
        executable = Path(sys.executable).resolve()
        image, mount = os.environ.get('APPIMAGE'), os.environ.get('APPDIR')
        # A standalone frozen app can also inherit an IDE's AppImage environment.
        if image and (not mount or executable.is_relative_to(Path(mount).resolve())):
            executable = Path(image).resolve()
        return executable.parent/'config.json'
    # An IDE itself may be an AppImage: ignore inherited APPIMAGE in source runs.
    return Path(__file__).resolve().parents[1]/'config.json'


def legacy_config_path():
    return Path.home()/'.config/ptz-controller/config.json'
