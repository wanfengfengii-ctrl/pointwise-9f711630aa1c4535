"""HANDOFF.md 的联动演练：相邻两场在固定拍交班，共用频道时钟。

在临时目录与操作系统分配的本机端口运行真实 HTTP 服务，无需常驻进程。
演示配对、联合裁定、跨界续播、中途重开接续以及两场分别核账的实际响应。
业务断言不符时打印 HANDOFF_DEMO_MISMATCH 并以非零退出。
"""
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from program.database import initialize_database
from program.http_api import make_handler

ROOT = Path(__file__).resolve().parent
SAMPLE = json.loads((ROOT / 'samples' / 'channel_handoff.json').read_text(encoding='utf-8'))


class Studio:
    """Live HTTP service on an ephemeral port, restartable on the same database."""

    def __init__(self, database):
        self.database = database
        self.server = None

    def start(self):
        initialize_database(self.database)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.database))
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02})
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=10)
            self.server = None

    def restart(self):
        self.stop()
        self.start()

    def call(self, operation, **fields):
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
            return response.code, json.load(response)


def show(operation, status, body):
    print(f'{operation} -> HTTP {status} '
          + json.dumps(body, ensure_ascii=False, sort_keys=True), flush=True)


def issue(studio, config):
    _, draft = studio.call('record', recording_id=config['recording_id'], frames=config['frames'])
    _, release = studio.call('release', draft_id=draft['draft_id'])
    return release['release_id']


def open_session(studio, config, release_id):
    _, opened = studio.call('open', program_id=config['program_id'], release_ids=[release_id],
                            background_id=config['background_id'])
    return opened['session_id']


def pair(studio, front, back):
    status, paired = studio.call('channel/pair', front_session_id=front, back_session_id=back,
                                 handoff_beat=SAMPLE['handoff_beat'])
    show('channel/pair', status, paired)
    return paired['channel_id']


def play(studio, channel_id, count):
    aired = []
    for _ in range(count):
        status, body = studio.call('channel/tick', channel_id=channel_id)
        show('channel/tick', status, body)
        aired.append(body)
    return aired


def run_handoff(studio, check):
    print('-- 主场景：H=4 配对，前场尾段跨界，中途重开服务 --', flush=True)
    front_release = issue(studio, SAMPLE['front'])
    back_release = issue(studio, SAMPLE['back'])
    front = open_session(studio, SAMPLE['front'], front_release)
    back = open_session(studio, SAMPLE['back'], back_release)
    channel_id = pair(studio, front, back)

    status, body = studio.call('submit', session_id=front, request_id='same',
                               asset_id=front_release, window=SAMPLE['front']['window'])
    show('submit front same [3,6)', status, body)
    check('front same accepted', body['status'], 'accepted')

    status, body = studio.call('preview', session_id=back, asset_id=back_release,
                               window=SAMPLE['contested']['window'])
    show('preview back crowded [4,6)', status, body)
    check('contested preview unschedulable', body, {'status': 'unschedulable'})

    status, body = studio.call('submit', session_id=back,
                               request_id=SAMPLE['contested']['request_id'],
                               asset_id=back_release, window=SAMPLE['contested']['window'])
    show('submit back crowded [4,6)', status, body)
    check('contested submit unschedulable', body['status'], 'unschedulable')

    status, body = studio.call('submit', session_id=back, request_id='same',
                               asset_id=back_release, window=SAMPLE['back']['window'])
    show('submit back same [6,8)', status, body)
    check('back same accepted', body['status'], 'accepted')

    aired = play(studio, channel_id, 4)
    check('beat 3 airs the first front frame',
          (aired[3]['beat'], aired[3]['sample'], aired[3]['session_id']), (3, 31, front))

    print('-- 重开服务 --', flush=True)
    studio.restart()

    aired += play(studio, channel_id, 2)
    check('front tail crosses the handoff beat',
          [(row['beat'], row['sample']) for row in aired[3:]], [(3, 31), (4, 32), (5, 33)])

    status, body = studio.call('close', session_id=front)
    show('close front', status, body)
    check('front closes once its tail finished', status, 200)
    status, front_report = studio.call('report', report_id=body['report_id'])
    show('report front', status, front_report)

    aired += play(studio, channel_id, 2)
    check('back frames air after the handoff beat',
          [(row['beat'], row['sample'], row['session_id']) for row in aired[6:]],
          [(6, 41, back), (7, 42, back)])

    status, body = studio.call('close', session_id=back)
    show('close back', status, body)
    status, back_report = studio.call('report', report_id=body['report_id'])
    show('report back', status, back_report)

    front_same = {row['request_id']: row for row in front_report['promises']}['same']
    back_same = {row['request_id']: row for row in back_report['promises']}['same']
    check('front reconciles its own beats', front_same['beats'], [3, 4, 5])
    check('back reconciles its own beats', back_same['beats'], [6, 7])
    check('front promise fulfilled', front_same['fulfilled'], True)
    check('back promise fulfilled', back_same['fulfilled'], True)

    status, front_lookup = studio.call('lookup', session_id=front, request_id='same')
    show('lookup front same', status, front_lookup)
    status, back_lookup = studio.call('lookup', session_id=back, request_id='same')
    show('lookup back same', status, back_lookup)
    check('front keeps its original window', front_lookup['window'], [3, 6])
    check('back keeps its original window', back_lookup['window'], [6, 8])

    return aired, {'front': {'same': front_same['beats']}, 'back': {'same': back_same['beats']}}


def run_movable(studio, check):
    print('-- 对照：可挪动的前场与后场共用同一时序 --', flush=True)
    movable = SAMPLE['movable_front']
    contested = SAMPLE['contested']
    front_release = issue(studio, {**SAMPLE['front'], 'frames': movable['frames']})
    back_release = issue(studio, SAMPLE['back'])
    front = open_session(studio, SAMPLE['front'], front_release)
    back = open_session(studio, SAMPLE['back'], back_release)
    channel_id = pair(studio, front, back)

    status, body = studio.call('submit', session_id=front, request_id='same',
                               asset_id=front_release, window=movable['window'])
    show('submit front same [2,6)', status, body)
    check('movable front accepted', body['status'], 'accepted')
    status, body = studio.call('submit', session_id=back, request_id=contested['request_id'],
                               asset_id=back_release, window=contested['window'])
    show('submit back crowded [4,6)', status, body)
    check('movable back accepted', body['status'], 'accepted')

    aired = play(studio, channel_id, 6)
    check('front occupies [2,4)', [(row['beat'], row['sample']) for row in aired[2:4]],
          [(2, 31), (3, 32)])
    check('back occupies [4,6)', [(row['beat'], row['sample']) for row in aired[4:6]],
          [(4, 41), (5, 42)])


def run_background_control(studio, check):
    print('-- 对照：空拍背景在交班拍切换归属 --', flush=True)
    front_release = issue(studio, SAMPLE['front'])
    back_release = issue(studio, SAMPLE['back'])
    front = open_session(studio, SAMPLE['front'], front_release)
    back = open_session(studio, SAMPLE['back'], back_release)
    channel_id = pair(studio, front, back)

    aired = play(studio, channel_id, 5)
    check('beat 3 keeps the front background',
          (aired[3]['output_id'], aired[3]['session_id']),
          (SAMPLE['front']['background_id'], front))
    check('beat 4 switches to the back background',
          (aired[4]['output_id'], aired[4]['session_id']),
          (SAMPLE['back']['background_id'], back))


def main():
    failures = []

    def check(label, actual, expected):
        if actual != expected:
            failures.append(label)
            print(f'MISMATCH {label}: expected {expected!r}, got {actual!r}', flush=True)

    with TemporaryDirectory(prefix='riverbridge-handoff-') as directory:
        studio = Studio(Path(directory) / 'radio.sqlite3')
        studio.start()
        try:
            aired, reports = run_handoff(studio, check)
            run_movable(studio, check)
            run_background_control(studio, check)
        finally:
            studio.stop()

    record = {
        'case_id': 'channel.handoff',
        'input': {
            'handoff_beat': SAMPLE['handoff_beat'],
            'front': {'frames': SAMPLE['front']['frames'], 'window': SAMPLE['front']['window']},
            'back': {'frames': SAMPLE['back']['frames'], 'window': SAMPLE['back']['window']},
        },
        'expected': [[3, 31], [4, 32], [5, 33], [6, 41], [7, 42]],
        'actual': [[row['beat'], row['sample']] for row in aired if row['source'] == 'promise'],
        'downstream_expected': {'front': {'same': [3, 4, 5]}, 'back': {'same': [6, 7]}},
        'downstream_actual': reports,
        'code_paths': ['air/channel.py', 'air/admission.py', 'air/playout.py',
                       'program/closing.py', 'review/reconciliation.py'],
        'oracle': 'Front frames started before the handoff beat finish across it; new starts '
                  'and empty-beat background switch to the back session at the handoff beat, '
                  'and each session reconciles only its own promises.',
    }
    print('POINTWISE_SCENARIO ' + json.dumps(record, ensure_ascii=False), flush=True)
    if failures:
        print(f'HANDOFF_DEMO_MISMATCH {len(failures)} checks failed: {failures}', file=sys.stderr)
        return 1
    print('HANDOFF DEMO PASS', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
