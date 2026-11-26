import sqlite3

from air.playout import tick
from program.closing import close
from program.database import initialize_database, write_transaction
from tests.support import LiveCase, emit_scenario


class TestReview(LiveCase):
    def test_close_waits_for_playout(self):
        session, assets = self.open_sample('B')
        self.assertEqual(self.submit(session, 'b', assets['B'], [1, 3])['status'], 'accepted')
        fault = self.call('close', http=409, session_id=session)
        self.assertEqual(fault['error'], 'session_busy')
        self.call('report', http=404, report_id=session)
        self.assertEqual(self.play(session, 2)[1]['sample'], 70)
        self.assertEqual(self.call('close', http=409, session_id=session)['error'], 'session_busy')
        self.call('report', http=404, report_id=session)
        self.assertEqual(self.play(session, 1)[0]['sample'], 71)
        self.assertTrue(self.read_report(session)['promises'][0]['fulfilled'])
        self.assertEqual(self.call('tick', http=409, session_id=session)['error'], 'session_closed')
        self.call('tick', http=404, session_id=session + 1)
        self.call('timeline', http=404, session_id=session + 1)

    def test_close_waits_for_local_ident_tail(self):
        session, assets = self.open_sample('long')
        self.assertEqual(self.call('ident', session_id=session, request_id='local',
                                   asset_id=assets['long'])['status'], 'started')
        self.assertEqual(self.play(session, 1)[0]['sample'], 51)
        self.assertEqual(self.call('close', http=409, session_id=session)['error'], 'session_busy')
        self.call('report', http=404, report_id=session)
        self.assertEqual(self.play(session, 1)[0]['sample'], 52)
        self.assertEqual(self.read_report(session)['promises'], [])

    def test_report_survives_reopen(self):
        session, assets = self.open_sample('A')
        self.assertEqual(self.submit(session, 'a', assets['A'], [1, 3])['status'], 'accepted')
        outputs = self.play(session, 3)
        self.assertEqual([row['sample'] for row in outputs], [None, 10, 11])
        first = self.read_report(session)
        self.assertEqual(first['outputs'], outputs)
        self.assertTrue(first['promises'][0]['fulfilled'])
        draft = self.call('record', recording_id='evening-update', frames=[99])
        later, later_assets = self.open_sample('B')
        self.assertEqual(self.submit(later, 'b', later_assets['B'], [0, 2])['status'], 'accepted')
        later_outputs = self.play(later, 2)
        later_report = self.read_report(later)
        with write_transaction(self.database) as connection:
            connection.execute('UPDATE station_defaults SET background_id = ?', ('studio-quiet',))
        self.stop_server()
        initialize_database(self.database)
        initialize_database(self.database)
        self.restart()
        self.assertEqual(self.call('report', report_id=first['report_id']), first)
        self.assertEqual(self.call('report', report_id=later_report['report_id']), later_report)
        self.assertEqual(self.call('timeline', session_id=later)['outputs'], later_outputs)
        self.assertEqual(self.call('release', draft_id=draft['draft_id'])['recording_id'], 'evening-update')
        self.assertEqual(self.call('close', session_id=session)['report_id'], first['report_id'])
        empty = self.call('open', program_id='silent', release_ids=[])['session_id']
        self.assertEqual(self.play(empty, 1)[0]['output_id'], 'studio-quiet')

    def test_report_groups_identical_clips_by_request(self):
        session, assets = self.open_sample('A')
        self.assertEqual(self.submit(session, 'first', assets['A'], [0, 2])['status'], 'accepted')
        self.assertEqual(self.submit(session, 'second', assets['A'], [2, 4])['status'], 'accepted')
        outputs = self.play(session, 4)
        expected_origins = ['first', 'first', 'second', 'second']
        origins = [row['request_id'] for row in outputs]
        self.assertEqual(origins, expected_origins)
        self.assertEqual([row['sample'] for row in outputs], [10, 11, 10, 11])
        report = self.read_report(session)
        self.restart()
        retrieved = self.call('report', report_id=report['report_id'])
        self.assertEqual(retrieved, report)
        expected_ranges = [['first', 0, 2, True], ['second', 2, 4, True]]
        ranges = [[row[key] for key in ('request_id', 'first_beat', 'end_beat', 'fulfilled')]
                  for row in retrieved['promises']]
        self.assertEqual(ranges, expected_ranges)
        emit_scenario(
            'readback.repeat_clip', {'clip': [10, 11], 'windows': [[0, 2], [2, 4]]},
            expected_origins, origins, expected_ranges, ranges,
            ['air/playout.py', 'review/reconciliation.py', 'review/retrieval.py'],
            'Each two-frame window fixes its two beats, with the same asset and different request IDs.',
        )

    def test_failed_tick_rolls_back_frame_and_progress(self):
        session, assets = self.open_sample('pin')
        self.assertEqual(self.submit(session, 'pin', assets['pin'], [0, 1])['status'], 'accepted')
        with write_transaction(self.database) as connection:
            connection.execute('''CREATE TRIGGER reject_advance BEFORE UPDATE OF beat ON playout
                                  BEGIN SELECT RAISE(ABORT, 'cannot advance'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'cannot advance'):
            tick(self.database, session)
        self.assertEqual(self.call('timeline', session_id=session)['outputs'], [])
        with write_transaction(self.database) as connection:
            connection.execute('DROP TRIGGER reject_advance')
        output = self.play(session, 1)[0]
        self.assertEqual((output['beat'], output['sample'], output['request_id']), (0, 8, 'pin'))
        self.assertTrue(self.read_report(session)['promises'][0]['fulfilled'])

    def test_failed_report_write_leaves_session_open(self):
        session, _ = self.open_sample()
        self.play(session, 1)
        with write_transaction(self.database) as connection:
            connection.execute('''CREATE TRIGGER reject_report BEFORE INSERT ON shift_reports
                                  BEGIN SELECT RAISE(ABORT, 'cannot file report'); END''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'cannot file report'):
            close(self.database, session)
        self.call('report', http=404, report_id=session)
        self.assertEqual(self.play(session, 1)[0]['beat'], 1)
        with write_transaction(self.database) as connection:
            connection.execute('DROP TRIGGER reject_report')
        self.assertEqual([row['beat'] for row in self.read_report(session)['outputs']], [0, 1])
