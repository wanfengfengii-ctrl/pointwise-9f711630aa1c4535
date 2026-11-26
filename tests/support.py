import json
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from desk.submission import AirLink
from program.database import initialize_database
from program.http_api import make_handler


SAMPLE_PATH = Path(__file__).resolve().parents[1] / 'samples' / 'shift.json'


def emit_scenario(case_id, inputs, expected, actual, downstream_expected, downstream_actual,
                  code_paths, oracle):
    record = {
        'case_id': case_id,
        'input': inputs,
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': code_paths,
        'oracle': oracle,
    }
    print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False), flush=True)


def await_event(event, description):
    if not event.wait(10):
        raise TimeoutError(description)


class LiveCase(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='riverbridge-')
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'radio.sqlite3'
        self.sample = json.loads(SAMPLE_PATH.read_text(encoding='utf-8'))
        self.server = None
        self.addCleanup(self.stop_server)
        self.restart()

    def restart(self, **hooks):
        self.stop_server()
        initialize_database(self.database)
        link = AirLink(self.database, **hooks)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.database, link))
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02})
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise TimeoutError('HTTP fixture server did not stop')
            self.server = None

    def request(self, operation, **fields):
        request = Request(
            self.base + '/' + operation,
            data=json.dumps(fields).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as exc:
            response = exc
        with response:
            status = response.code
            if response.headers.get_content_type() != 'application/json':
                raise RuntimeError(f'{operation}: unexpected HTTP content type')
            body = json.load(response)
        if not isinstance(body, dict) or status >= 500:
            raise RuntimeError(f'{operation}: HTTP {status} {body}')
        return status, body

    def call(self, operation, *, http=200, **fields):
        status, body = self.request(operation, **fields)
        self.assertEqual(status, http, (operation, body))
        return body

    def issue(self, recording_id, frames):
        draft = self.call('record', recording_id=recording_id, frames=frames)
        return self.call('release', draft_id=draft['draft_id'])['release_id']

    def open_sample(self, *names):
        assets = {name: self.issue(name, self.sample['clips'][name]) for name in names}
        response = self.call(
            'open', program_id=self.sample['program_id'], release_ids=list(assets.values()),
            ident_release_ids=[assets[name] for name in self.sample['idents'] if name in assets],
            background_id=self.sample['background_id'],
        )
        return response['session_id'], assets

    def submit(self, session_id, request_id, asset_id, window, http=200):
        return self.call('submit', http=http, session_id=session_id, request_id=request_id,
                         asset_id=asset_id, window=window)

    def play(self, session_id, count):
        return [self.call('tick', session_id=session_id) for _ in range(count)]

    def read_report(self, session_id):
        closed = self.call('close', session_id=session_id)
        return self.call('report', report_id=closed['report_id'])
