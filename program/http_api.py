import argparse
import json
import logging
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from inspect import signature

from air.channel import channel_tick, pair_channel
from air.local_ident import ident
from air.playout import tick
from catalog.recordings import record
from catalog.releases import release
from desk.previews import preview
from desk.receipts import lookup
from desk.submission import AirLink, submit
from program.closing import close
from program.database import ServiceError, initialize_database, invalid
from program.opening import open_program
from review.retrieval import report
from review.timeline import timeline


MAX_BODY_BYTES = 1024 * 1024


def make_handler(database, link=None):
    if link is None:
        link = AirLink(database)
    operations = {
        '/record': partial(record, database),
        '/release': partial(release, database),
        '/open': partial(open_program, database),
        '/preview': partial(preview, database),
        '/submit': partial(submit, database, link),
        '/lookup': partial(lookup, database, link),
        '/ident': partial(ident, database),
        '/tick': partial(tick, database),
        '/timeline': partial(timeline, database),
        '/close': partial(close, database),
        '/report': partial(report, database),
        '/channel/pair': partial(pair_channel, database),
        '/channel/tick': partial(channel_tick, database),
    }
    routes = {path: (operation, signature(operation)) for path, operation in operations.items()}

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            if status == 405:
                self.send_header('Allow', 'POST')
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self):
            if self.headers.get_content_type() != 'application/json':
                raise ServiceError(415, 'unsupported_media_type', 'send application/json')
            try:
                size = int(self.headers.get('Content-Length', ''))
            except ValueError as exc:
                raise invalid('a valid Content-Length is required') from exc
            if size < 0:
                raise invalid('Content-Length cannot be negative')
            if size > MAX_BODY_BYTES:
                raise ServiceError(413, 'body_too_large', 'JSON body exceeds one MiB')
            try:
                payload = json.loads(self.rfile.read(size).decode('utf-8'))
            except (ValueError, RecursionError) as exc:
                raise invalid('request body is not valid JSON') from exc
            if not isinstance(payload, dict):
                raise invalid('request body must be a JSON object')
            return payload

        def do_POST(self):
            if self.path not in routes:
                self._send_json(404, {'error': 'not_found', 'message': 'unknown operation'})
                return
            operation, parameters = routes[self.path]
            try:
                payload = self._read_json()
                try:
                    arguments = parameters.bind(**payload)
                except TypeError as exc:
                    raise invalid(str(exc)) from exc
                result = operation(*arguments.args, **arguments.kwargs)
            except ServiceError as exc:
                self._send_json(exc.http_status, {'error': exc.code, 'message': str(exc)})
                return
            except Exception:
                logging.exception('request failed for %s', self.path)
                self._send_json(500, {'error': 'internal_error', 'message': 'operation failed'})
                return
            self._send_json(202 if result.get('status') == 'pending' else 200, result)

        def do_GET(self):
            self._send_json(405, {'error': 'method_not_allowed', 'message': 'use POST'})

        def log_request(self, code='-', size='-'):
            logging.getLogger(__name__).info('%s %s %s', self.command, self.path, code)

    return Handler


def serve(database, port=8080):
    initialize_database(database)
    with ThreadingHTTPServer(('127.0.0.1', port), make_handler(database)) as server:
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description='River Bridge community radio playout service')
    parser.add_argument('--db', required=True, help='path to the SQLite database')
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    try:
        serve(args.db, args.port)
    except KeyboardInterrupt:
        return


if __name__ == '__main__':
    main()
