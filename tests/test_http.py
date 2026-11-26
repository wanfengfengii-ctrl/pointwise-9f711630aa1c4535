from concurrent.futures import ThreadPoolExecutor
from threading import Event

from desk.receipts import ResultUnavailable
from tests.support import LiveCase, await_event


class TestHttp(LiveCase):
    def test_waiting_submission_uses_live_cursor(self):
        for ident_name, expected, values in (
            ('long', 'unschedulable', [51, 52, None]),
            ('short', 'accepted', [50, 70, 71]),
        ):
            with self.subTest(ident=ident_name):
                session, assets = self.open_sample('B', ident_name)
                arrived = Event()
                resume = Event()

                def hold_before_admit(request):
                    if request.request_id == 'b':
                        arrived.set()
                        await_event(resume, 'submission was not released')

                self.restart(before_admit=hold_before_admit)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    submission = pool.submit(self.submit, session, 'b', assets['B'], [1, 3])
                    try:
                        await_event(arrived, 'submission did not reach the playout link')
                        pending = self.call('lookup', http=202, session_id=session, request_id='b')
                        self.assertEqual(pending['status'], 'pending')
                        start = self.call('ident', session_id=session, request_id='station',
                                          asset_id=assets[ident_name])
                        self.assertEqual(start, {'status': 'started', 'start_beat': 0})
                        outputs = self.play(session, 1)
                    finally:
                        resume.set()
                    result = submission.result(timeout=15)
                self.assertEqual(result['status'], expected)
                outputs += self.play(session, 2)
                self.assertEqual([row['sample'] for row in outputs], values)

    def test_committed_reply_can_wait_while_air_keeps_playing(self):
        session, assets = self.open_sample('A', 'B', 'long')
        committed = Event()
        release_reply = Event()

        def hold_after_admit(request, outcome):
            if request.request_id == 'b':
                committed.set()
                await_event(release_reply, 'admission reply was not released')
                raise ResultUnavailable('admission reply lost after commit')

        def hide_result(session_id, request_id):
            if (session_id, request_id) == (session, 'b'):
                raise ResultUnavailable('receipt link unavailable')

        self.restart(after_admit=hold_after_admit, before_lookup=hide_result)
        self.assertEqual(self.submit(session, 'a', assets['A'], [0, 5])['status'], 'accepted')
        with ThreadPoolExecutor(max_workers=1) as pool:
            submission = pool.submit(self.submit, session, 'b', assets['B'], [1, 3], 202)
            try:
                await_event(committed, 'playout did not finish admission')
                self.assertEqual(self.call('lookup', http=202, session_id=session, request_id='b')['status'],
                                 'pending')
                local = self.call('ident', session_id=session, request_id='station', asset_id=assets['long'])
                self.assertEqual(local['status'], 'unschedulable')
                outputs = self.play(session, 5)
            finally:
                release_reply.set()
            self.assertEqual(submission.result(timeout=15)['status'], 'pending')
        self.assertEqual([row['sample'] for row in outputs], [None, 70, 71, 10, 11])
        self.restart()
        first = self.call('lookup', session_id=session, request_id='b')
        self.assertEqual(first['status'], 'accepted')
        self.assertEqual(self.submit(session, 'b', assets['B'], [1, 3]), first)
        self.assertEqual(self.call('timeline', session_id=session)['outputs'], outputs)
