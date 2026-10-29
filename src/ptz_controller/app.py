import secrets
import json
import time
from pathlib import Path
from flask import Flask, Response, jsonify, render_template, request


def create_app(engine):
    root = Path(__file__).resolve().parent
    app = Flask(__name__, template_folder=str(root/'templates'), static_folder=str(root/'static'))
    token = secrets.token_urlsafe(32)
    app.config['MAX_CONTENT_LENGTH'] = 128 * 1024

    @app.before_request
    def protect():
        # Browser-origin requests cannot command a camera through this local API.
        if request.method == 'POST' and not secrets.compare_digest(request.headers.get('X-PTZ-Token', ''), token):
            return jsonify(error='Invalid session token'), 403

    @app.errorhandler(ValueError)
    @app.errorhandler(KeyError)
    @app.errorhandler(TypeError)
    @app.errorhandler(OSError)
    def invalid(exc):
        engine.log('ERROR', str(exc))
        return jsonify(error=str(exc)), 400

    @app.get('/')
    def index():
        return render_template('index.html', token=token)

    @app.get('/api/state')
    def state():
        return jsonify(engine.snapshot())

    @app.get('/api/live')
    def live():
        def frames():
            previous = -1
            while not engine.quitting.is_set():
                sample = engine.wait_for_sample(previous)
                if sample['sequence'] != previous:
                    previous = sample['sequence']
                    yield 'data: ' + json.dumps(sample, separators=(',', ':')) + '\n\n'
                else:
                    yield ': keepalive\n\n'
        return Response(frames(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache, no-store', 'X-Accel-Buffering': 'no'})

    @app.post('/api/<operation>')
    def update(operation):
        data = request.get_json() or {}
        result = {'ok': True}
        if operation == 'test-connection':
            return jsonify(ok=True, result=engine.test_connection(data['camera']))
        with engine.lock:
            if operation == 'heartbeat':
                engine.heartbeat = time.monotonic()
            elif operation == 'config':
                engine.save(data)
            elif operation == 'controller':
                engine.select_controller(data['controller'])
            elif operation == 'connect':
                if 'config' in data:
                    engine.save(data['config'])
                try:
                    engine.connect()
                    engine.arm()
                except Exception:
                    engine.disconnect()
                    raise
            elif operation == 'disconnect':
                engine.disconnect()
            elif operation == 'arm':
                engine.arm()
            elif operation == 'stop':
                engine.stop()
                engine.virtual = {'axes': [0]*8, 'pressed': []}
                engine.cancel_capture()
                engine.log('INFO', 'Motion stopped; controller disarmed')
            elif operation == 'preset-save':
                engine.save_preset(data['name'], data['config'])
                result['state'] = engine.snapshot()
            elif operation == 'preset-load':
                engine.load_preset(data['name'])
                result['state'] = engine.snapshot()
            elif operation == 'preset-delete':
                engine.delete_preset(data['name'])
                result['state'] = engine.snapshot()
            elif operation == 'capture-start':
                result['capture_id'] = engine.start_capture()
            elif operation == 'capture-cancel':
                if data.get('capture_id') == engine.capture['id']:
                    engine.cancel_capture()
            elif operation == 'command':
                engine.execute(data['action'], data.get('param', 0))
            elif operation == 'virtual':
                axes = [float(v) for v in data['axes']]
                if len(axes) > 64 or any(not -1 <= v <= 1 for v in axes):
                    raise ValueError('Invalid virtual axes')
                pressed = data['pressed']
                if not isinstance(pressed, list) or len(pressed) > 128 or any(not isinstance(v, str) for v in pressed):
                    raise ValueError('Invalid virtual buttons')
                engine.virtual = {'axes': axes, 'pressed': pressed}
            elif operation == 'sequence-reset':
                if not engine.transport or engine.config['camera']['framing'] != 'sony':
                    raise ValueError('Connect a Sony-framed transport first')
                engine.stop()
                engine.transport.reset_sequence()
            else:
                return jsonify(error='Unknown operation'), 404
        return jsonify(result)

    return app
