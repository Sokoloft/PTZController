"""Flask/pywebview GUI with VISCA camera control and Linux gamepads.

Use --browser for development without Qt; the same Engine serves both modes.
"""
import argparse
from pathlib import Path
import threading
from werkzeug.serving import make_server, WSGIRequestHandler
from ptz_controller import __version__
from ptz_controller.config import write_json


class PresetDialogs:
    def __init__(self, engine, open_dialog, save_dialog):
        self._engine = engine
        self._open_dialog = open_dialog
        self._save_dialog = save_dialog
        self._window = None

    def choose_preset(self):
        try:
            paths = self._window.create_file_dialog(
                self._open_dialog, directory=str(Path.home()),
                file_types=('JSON files (*.json)',))
            if not paths:
                return {}
            path = Path(paths if isinstance(paths, str) else paths[0])
            if path.suffix.lower() != '.json':
                raise ValueError('Choose a JSON preset file.')
            with path.open('rb') as stream:
                data = stream.read(128 * 1024 + 1)
            if len(data) > 128 * 1024:
                raise ValueError('The preset file exceeds the 128 KB limit.')
            return {'name': path.name, 'content': data.decode('utf-8-sig')}
        except (OSError, ValueError) as exc:
            return {'error': str(exc)}

    def export_preset(self, name):
        try:
            preset = self._engine.export_preset(name)
            paths = self._window.create_file_dialog(
                self._save_dialog, directory=str(Path.home()), save_filename=preset['filename'],
                file_types=('JSON files (*.json)',))
            if not paths:
                return {'saved': False}
            path = Path(paths if isinstance(paths, str) else paths[0])
            if path.suffix.lower() != '.json':
                raise ValueError('Choose an export filename ending in .json')
            write_json(path, preset['config'])
            return {'saved': True}
        except (OSError, ValueError) as exc:
            return {'error': str(exc)}


class RequestHandler(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        quiet = {'/api/heartbeat', '/api/state', '/api/live', '/api/virtual'}
        if str(code).startswith(('4', '5')) or self.path.split('?')[0] not in quiet:
            super().log_request(code, size)


def main():
    parser = argparse.ArgumentParser(description='Linux controller to VISCA PTZ bridge')
    parser.add_argument('-V', '--version', action='version', version=f'PTZ Controller {__version__}')
    parser.add_argument('--browser', action='store_true', help='Serve GUI without pywebview')
    parser.add_argument('--port', type=int, default=0, help='Local GUI port; 0 chooses a free port')
    parser.add_argument('--config', help='Use the directory containing this path for presets (default: user config directory)')
    args = parser.parse_args()
    from ptz_controller.app import create_app
    from ptz_controller.engine import Engine

    engine = Engine(args.config, native_window=not args.browser)
    server = make_server('127.0.0.1', args.port, create_app(engine), threaded=True,
                         request_handler=RequestHandler)
    worker = threading.Thread(target=engine.run, daemon=True)
    http = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    http.start()
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'PTZ Controller: {url}', flush=True)
    try:
        if args.browser:
            http.join()
        else:
            import webview
            open_dialog = webview.FileDialog.OPEN if hasattr(webview, 'FileDialog') else webview.OPEN_DIALOG
            save_dialog = webview.FileDialog.SAVE if hasattr(webview, 'FileDialog') else webview.SAVE_DIALOG
            dialogs = PresetDialogs(engine, open_dialog, save_dialog)
            dialogs._window = webview.create_window('PTZ Controller', url, js_api=dialogs,
                                                    width=1280, height=900, min_size=(850, 650))
            webview.start(gui='qt')
    except KeyboardInterrupt:
        pass
    finally:
        engine.quitting.set()
        worker.join(timeout=4)
        engine.disconnect()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
