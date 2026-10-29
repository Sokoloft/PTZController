"""Flask/pywebview GUI with visca_over_ip camera control and Linux gamepads.

Use --browser for development without Qt; the same Engine serves both modes.
"""
import argparse
import threading
from werkzeug.serving import make_server, WSGIRequestHandler
from ptz_controller import __version__


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
    parser.add_argument('--config', help='Configuration file (default: config.json beside the app)')
    args = parser.parse_args()
    from ptz_controller.app import create_app
    from ptz_controller.engine import Engine

    engine = Engine(args.config)
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
            webview.create_window('PTZ Controller', url, width=1280, height=900, min_size=(850, 650))
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
