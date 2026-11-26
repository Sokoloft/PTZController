"""Locate and write user settings in the user's config directory."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def default_config_path():
    base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')
    if not base.is_absolute():
        base = Path.home()/'.config'
    return base/'ptz-controller'/'config.json'


def preset_path(directory, name):
    if (not isinstance(name, str) or not 1 <= len(name) <= 80 or
            name != name.strip() or name in ('.', '..') or
            any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in name) or
            len(name.encode('utf-8')) > 250):
        raise ValueError('Use a preset name of 1–80 characters without path separators or control characters')
    return directory/f'{name}.json'


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                            suffix='.tmp', delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(data, stream, indent=2)
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
