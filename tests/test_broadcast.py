from desk.receipts import ResultUnavailable
from program.database import read_transaction
from tests.support import LiveCase, emit_scenario


class TestBroadcast(LiveCase):
    def test_preview_does_not_reserve(self):
        session, assets = self.open_sample('A')
        preview = self.call('preview', session_id=session, asset_id=assets['A'], window=[0, 5])
        self.assertEqual(preview, {'status': 'suggested', 'start_beat': 0})
        output = self.play(session, 1)[0]
        self.assertEqual(output, {
            'beat': 0, 'source': 'background', 'output_id': 'river-room', 'release_id': None,
            'frame_index': None, 'sample': None, 'request_id': None,
        })
        self.assertEqual(self.read_report(session)['promises'], [])

    def test_preview_rejects_invalid_window_and_foreign_asset(self):
        session, assets = self.open_sample('A')
        foreign = self.issue('foreign', [6])
        for window in ([True, 3], [2, 2], [3, 2], [0.0, 2], [1]):
            with self.subTest(window=window):
                self.call('preview', http=400, session_id=session, asset_id=assets['A'], window=window)
        self.call('preview', http=400, session_id=session, asset_id=foreign, window=[0, 5])
        with read_transaction(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM obligations').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT count(*) FROM edit_requests').fetchone()[0], 0)
        self.assertEqual(self.play(session, 1)[0]['source'], 'background')

    def test_replan_with_narrow_window(self):
        session, assets = self.open_sample('A', 'B', 'short')
        preview = self.call('preview', session_id=session, asset_id=assets['A'], window=[0, 5])
        self.assertEqual(preview['start_beat'], 0)
        self.assertEqual(self.submit(session, 'a', assets['A'], [0, 5])['status'], 'accepted')
        second = self.submit(session, 'b', assets['B'], [1, 3])
        self.assertEqual(second['status'], 'accepted', 'B fits when A moves to [3,5)')
        ident = self.call('ident', session_id=session, request_id='station', asset_id=assets['short'])
        self.assertEqual(ident, {'status': 'started', 'start_beat': 0})
        outputs = self.play(session, 5)
        expected = [
            [0, 'ident', 'station', 0, 50],
            [1, 'promise', 'b', 0, 70],
            [2, 'promise', 'b', 1, 71],
            [3, 'promise', 'a', 0, 10],
            [4, 'promise', 'a', 1, 11],
        ]
        actual = [[row[key] for key in ('beat', 'source', 'request_id', 'frame_index', 'sample')]
                  for row in outputs]
        self.assertEqual(actual, expected)
        self.assertEqual([row['release_id'] for row in outputs],
                         [assets['short'], assets['B'], assets['B'], assets['A'], assets['A']])
        report = self.read_report(session)
        self.assertEqual(report['outputs'], outputs)
        report_expected = [['a', [3, 4], True], ['b', [1, 2], True]]
        report_actual = [[row['request_id'], row['beats'], row['fulfilled']]
                         for row in report['promises']]
        self.assertEqual(report_actual, report_expected)
        emit_scenario(
            'move', {'A': [10, 11], 'A_window': [0, 5], 'B': [70, 71], 'B_window': [1, 3],
                     'local_ident': [50]}, expected, actual, report_expected, report_actual,
            ['desk/submission.py', 'air/admission.py', 'air/scheduling.py', 'air/local_ident.py',
             'air/playout.py', 'review/reconciliation.py'],
            'The one-beat ident occupies 0, B has to fill 1 and 2, and A then fits at 3 and 4.',
        )

    def test_fragmented_capacity_is_not_a_contiguous_slot(self):
        session, assets = self.open_sample('A', 'pin')
        self.assertEqual(self.submit(session, 'pin', assets['pin'], [1, 2])['status'], 'accepted')
        self.assertEqual(self.submit(session, 'wide', assets['A'], [0, 3])['status'], 'unschedulable')
        self.assertEqual([output['sample'] for output in self.play(session, 3)], [None, 8, None])
        self.assertEqual([item['request_id'] for item in self.read_report(session)['promises']], ['pin'])

    def test_ident_cannot_consume_promised_time(self):
        session, assets = self.open_sample('B', 'long')
        self.assertEqual(self.submit(session, 'b', assets['B'], [1, 3])['status'], 'accepted')
        local = self.call('ident', session_id=session, request_id='station', asset_id=assets['long'])
        self.assertEqual(local['status'], 'unschedulable', 'the ident would take the first beat of B')
        outputs = self.play(session, 3)
        self.assertEqual([row['sample'] for row in outputs], [None, 70, 71])
        self.assertEqual([row['request_id'] for row in outputs], [None, 'b', 'b'])

    def test_lost_reply_keeps_promise(self):
        session, assets = self.open_sample('B', 'long')
        outcomes = []

        def drop_reply(request, outcome):
            if request.request_id == 'b':
                outcomes.append(outcome.status)
                raise ResultUnavailable('playout committed before reply was lost')

        def hide_result(session_id, request_id):
            if (session_id, request_id) == (session, 'b'):
                raise ResultUnavailable('receipt cannot be looked up during the interruption')

        self.restart(after_admit=drop_reply, before_lookup=hide_result)
        pending = self.submit(session, 'b', assets['B'], [1, 3], http=202)
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(outcomes, ['accepted'])
        self.assertEqual(self.call('lookup', http=202, session_id=session, request_id='b'), pending)
        self.call('lookup', http=404, session_id=session, request_id='never-submitted')
        ident = self.call('ident', session_id=session, request_id='station', asset_id=assets['long'])
        self.assertEqual(ident['status'], 'unschedulable')
        outputs = self.play(session, 5)
        self.assertEqual([row['sample'] for row in outputs], [None, 70, 71, None, None])
        self.restart()
        first = self.submit(session, 'b', assets['B'], [1, 3])
        self.assertEqual(first, {**pending, 'status': 'accepted'})
        self.assertEqual(self.call('lookup', session_id=session, request_id='b'), first)
        fault = self.call('submit', http=409, session_id=session, request_id='b',
                          asset_id=assets['B'], window=[1, 4])
        self.assertEqual(fault['error'], 'request_conflict')
        self.assertEqual(self.call('timeline', session_id=session)['outputs'], outputs)
        promises = self.read_report(session)['promises']
        self.assertEqual([(p['request_id'], p['beats'], p['fulfilled']) for p in promises],
                         [('b', [1, 2], True)])
        self.assertEqual(self.submit(session, 'b', assets['B'], [1, 3]), first)

    def test_started_ident_blocks_late_insert(self):
        session, assets = self.open_sample('B', 'long')
        start = self.call('ident', session_id=session, request_id='station', asset_id=assets['long'])
        self.assertEqual(start, {'status': 'started', 'start_beat': 0})
        result = self.submit(session, 'b', assets['B'], [1, 3])
        self.assertEqual(result['status'], 'unschedulable')
        outputs = self.play(session, 3)
        self.assertEqual([row['sample'] for row in outputs], [51, 52, None])
        self.assertEqual([row['frame_index'] for row in outputs], [0, 1, None])
        self.restart()
        self.assertEqual(self.call('lookup', session_id=session, request_id='b'), result)
        self.assertEqual(self.submit(session, 'b', assets['B'], [1, 3]), result)
        self.assertEqual(self.read_report(session)['promises'], [])
