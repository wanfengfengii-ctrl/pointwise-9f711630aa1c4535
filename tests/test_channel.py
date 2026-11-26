import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from program.database import read_transaction
from tests.support import LiveCase, await_event, emit_scenario

HANDOFF_PATH = Path(__file__).resolve().parents[1] / 'samples' / 'channel_handoff.json'

DEFAULT = object()


class ChannelCase(LiveCase):
    def setUp(self):
        super().setUp()
        self.handoff = json.loads(HANDOFF_PATH.read_text(encoding='utf-8'))
        self.handoff_beat = self.handoff['handoff_beat']

    def open_side(self, side, **overrides):
        config = {**self.handoff[side], **overrides}
        release = self.issue(config['recording_id'], config['frames'])
        response = self.call(
            'open', program_id=config['program_id'], release_ids=[release],
            background_id=config['background_id'],
        )
        return response['session_id'], release

    def pair(self, front, back, handoff_beat=DEFAULT, http=200):
        if handoff_beat is DEFAULT:
            handoff_beat = self.handoff_beat
        return self.call('channel/pair', http=http, front_session_id=front,
                         back_session_id=back, handoff_beat=handoff_beat)

    def channel_play(self, channel_id, count):
        return [self.call('channel/tick', channel_id=channel_id) for _ in range(count)]

    def channel_row(self, channel_id):
        with read_transaction(self.database) as connection:
            row = connection.execute(
                'SELECT * FROM channels WHERE channel_id = ?', (channel_id,),
            ).fetchone()
        return dict(row) if row is not None else None


class TestChannelPairing(ChannelCase):
    def test_pair_validation(self):
        front, _ = self.open_side('front')
        back, _ = self.open_side('back')
        for bad_handoff in (0, -1, True, False, 1.5, '4', None):
            with self.subTest(handoff_beat=bad_handoff):
                self.pair(front, back, handoff_beat=bad_handoff, http=400)
        for bad_session in ('x', 0, -1, True, 1.5, None):
            with self.subTest(session_id=bad_session):
                self.pair(bad_session, back, http=400)
                self.pair(front, bad_session, http=400)
        self.pair(front, front, http=400)
        self.pair(front, back + 100, http=404)
        self.pair(front + 100, back, http=404)
        self.call('channel/tick', http=404, channel_id=1)
        self.call('channel/tick', http=400, channel_id='one')
        with read_transaction(self.database) as connection:
            count = connection.execute('SELECT count(*) FROM channels').fetchone()[0]
        self.assertEqual(count, 0, 'rejected pairings must not register a channel')
        paired = self.pair(front, back)
        self.assertIsInstance(paired['channel_id'], int)
        self.assertGreaterEqual(paired['channel_id'], 1)
        self.assertEqual(self.channel_row(paired['channel_id'])['beat'], 0)

    def test_pair_requires_fresh_open_sessions(self):
        busy_request, asset = self.open_side('front')
        self.assertEqual(self.submit(busy_request, 'held', asset, [0, 3])['status'], 'accepted')
        busy_output, _ = self.open_side('front')
        self.play(busy_output, 1)
        ident_release = self.issue('station-ident', [50])
        busy_ident = self.call('open', program_id='ident-hour', release_ids=[ident_release],
                               ident_release_ids=[ident_release])['session_id']
        self.call('ident', session_id=busy_ident, request_id='station', asset_id=ident_release)
        closed, _ = self.open_side('back')
        self.call('close', session_id=closed)

        front, _ = self.open_side('front')
        for busy in (busy_request, busy_output, busy_ident):
            with self.subTest(busy=busy):
                fault = self.pair(front, busy, http=409)
                self.assertEqual(fault['error'], 'session_busy')
                fault = self.pair(busy, front, http=409)
                self.assertEqual(fault['error'], 'session_busy')
        self.assertEqual(self.pair(front, closed, http=409)['error'], 'session_closed')
        self.assertEqual(self.pair(closed, front, http=409)['error'], 'session_closed')

        # Rejections changed nothing: the fresh sessions still pair and play.
        back, _ = self.open_side('back')
        channel_id = self.pair(front, back)['channel_id']
        self.assertEqual(self.channel_play(channel_id, 1)[0]['output_id'], 'morning-room')
        self.assertEqual(self.play(busy_output, 1)[0]['beat'], 1)

    def test_paired_sessions_cannot_pair_again(self):
        front, _ = self.open_side('front')
        back, _ = self.open_side('back')
        self.pair(front, back)
        self.assertEqual(self.pair(front, back, http=409)['error'], 'session_paired')
        other, _ = self.open_side('back')
        self.assertEqual(self.pair(front, other, http=409)['error'], 'session_paired')
        self.assertEqual(self.pair(other, back, http=409)['error'], 'session_paired')
        spare, _ = self.open_side('front')
        self.pair(other, spare)

    def test_preview_does_not_block_pairing(self):
        front, front_asset = self.open_side('front')
        back, back_asset = self.open_side('back')
        self.call('preview', session_id=front, asset_id=front_asset, window=[0, 3])
        self.call('preview', session_id=back, asset_id=back_asset, window=[4, 6])
        self.pair(front, back)

    def test_paired_sessions_reject_single_session_playout(self):
        ident_release = self.issue('station-ident', [50])
        front = self.call('open', program_id='with-ident', release_ids=[ident_release],
                          ident_release_ids=[ident_release])['session_id']
        back, _ = self.open_side('back')
        self.pair(front, back)
        self.assertEqual(self.call('tick', http=409, session_id=front)['error'], 'session_paired')
        self.assertEqual(self.call('tick', http=409, session_id=back)['error'], 'session_paired')
        fault = self.call('ident', http=409, session_id=front, request_id='station',
                          asset_id=ident_release)
        self.assertEqual(fault['error'], 'session_paired')
        # Unpaired sessions keep the original single-session behavior.
        solo, _ = self.open_side('front')
        self.assertEqual(self.play(solo, 1)[0]['source'], 'background')


class TestChannelPlayout(ChannelCase):
    def test_handoff_playout_restart_and_reports(self):
        front, front_asset = self.open_side('front')
        back, back_asset = self.open_side('back')
        channel_id = self.pair(front, back)['channel_id']
        contested = self.handoff['contested']
        front_window = self.handoff['front']['window']
        back_window = self.handoff['back']['window']

        preview = self.call('preview', session_id=front, asset_id=front_asset, window=front_window)
        self.assertEqual(preview, {'status': 'suggested', 'start_beat': 3})
        self.assertEqual(self.submit(front, 'same', front_asset, front_window)['status'], 'accepted')
        # The contested back window cannot fit next to the front promise.
        preview = self.call('preview', session_id=back, asset_id=back_asset,
                            window=contested['window'])
        self.assertEqual(preview, {'status': 'unschedulable'})
        self.assertEqual(
            self.submit(back, contested['request_id'], back_asset, contested['window'])['status'],
            'unschedulable',
        )
        preview = self.call('preview', session_id=back, asset_id=back_asset, window=back_window)
        self.assertEqual(preview, {'status': 'suggested', 'start_beat': 6})
        self.assertEqual(self.submit(back, 'same', back_asset, back_window)['status'], 'accepted')

        # Neither session may close while its promises are pending.
        self.assertEqual(self.call('close', http=409, session_id=front)['error'], 'session_busy')
        self.assertEqual(self.call('close', http=409, session_id=back)['error'], 'session_busy')

        aired = self.channel_play(channel_id, 4)
        self.assertEqual([row['source'] for row in aired],
                         ['background', 'background', 'background', 'promise'])
        self.assertEqual([row['session_id'] for row in aired], [front] * 4)
        self.assertEqual([row['output_id'] for row in aired],
                         ['morning-room', 'morning-room', 'morning-room', front_asset])
        self.assertEqual(aired[3], {
            'beat': 3, 'source': 'promise', 'output_id': front_asset,
            'release_id': front_asset, 'frame_index': 0, 'sample': 31,
            'request_id': 'same', 'session_id': front,
        })

        # The service restarts while the front clip crosses the handoff beat.
        self.restart()
        self.assertEqual(self.call('close', http=409, session_id=front)['error'], 'session_busy')
        aired += self.channel_play(channel_id, 2)
        self.assertEqual([(row['beat'], row['sample'], row['session_id']) for row in aired[3:]],
                         [(3, 31, front), (4, 32, front), (5, 33, front)])

        # The front session closes as soon as its tail finishes; the back
        # session's two frames have not aired yet.
        closed = self.call('close', session_id=front)
        self.assertEqual(closed['report_id'], front)
        front_report = self.call('report', report_id=closed['report_id'])
        self.assertEqual([row['beat'] for row in front_report['outputs']], [0, 1, 2, 3, 4, 5])
        front_same = {row['request_id']: row for row in front_report['promises']}['same']
        self.assertEqual(front_same['window'], [3, 6])
        self.assertEqual(front_same['beats'], [3, 4, 5])
        self.assertEqual((front_same['first_beat'], front_same['end_beat']), (3, 6))
        self.assertTrue(front_same['fulfilled'])
        self.assertEqual(self.call('timeline', session_id=back)['outputs'], [])

        aired += self.channel_play(channel_id, 2)
        self.assertEqual([(row['beat'], row['sample'], row['session_id']) for row in aired[6:]],
                         [(6, 41, back), (7, 42, back)])
        back_report = self.read_report(back)
        back_same = {row['request_id']: row for row in back_report['promises']}['same']
        self.assertEqual(back_same['window'], [6, 8])
        self.assertEqual(back_same['beats'], [6, 7])
        self.assertTrue(back_same['fulfilled'])

        # The shared request id resolves per session.
        self.assertEqual(self.call('lookup', session_id=front, request_id='same')['window'], [3, 6])
        self.assertEqual(self.call('lookup', session_id=back, request_id='same')['window'], [6, 8])

        # With both sessions closed the channel cannot advance.
        fault = self.call('channel/tick', http=409, channel_id=channel_id)
        self.assertEqual(fault['error'], 'channel_idle')
        self.assertEqual(self.channel_row(channel_id)['beat'], 8)

        # Reports and rulings survive another restart under their original numbers.
        self.restart()
        self.assertEqual(self.call('report', report_id=front), front_report)
        self.assertEqual(self.call('report', report_id=back), back_report)
        self.assertEqual(self.submit(front, 'same', front_asset, front_window)['status'], 'accepted')
        self.assertEqual(
            self.submit(back, contested['request_id'], back_asset, contested['window'])['status'],
            'unschedulable',
        )

        emit_scenario(
            'channel.handoff',
            {'handoff_beat': self.handoff_beat, 'front_window': front_window,
             'back_window': back_window, 'front_frames': self.handoff['front']['frames'],
             'back_frames': self.handoff['back']['frames']},
            [[3, 31], [4, 32], [5, 33], [6, 41], [7, 42]],
            [[row['beat'], row['sample']] for row in aired if row['source'] == 'promise'],
            {'front': [3, 4, 5], 'back': [6, 7]},
            {'front': front_same['beats'], 'back': back_same['beats']},
            ['air/channel.py', 'air/admission.py', 'air/playout.py',
             'program/closing.py', 'review/reconciliation.py'],
            'Front frames started before the handoff beat finish across it; new starts and '
            'empty-beat background switch to the back session at the handoff beat.',
        )

    def test_movable_front_shares_one_schedule_with_back(self):
        movable = self.handoff['movable_front']
        contested = self.handoff['contested']
        front, front_asset = self.open_side('front', frames=movable['frames'])
        back, back_asset = self.open_side('back')
        channel_id = self.pair(front, back)['channel_id']
        # Both requests land before beat 2 and jointly fit the channel.
        self.assertEqual(self.submit(front, 'same', front_asset, movable['window'])['status'],
                         'accepted')
        preview = self.call('preview', session_id=back, asset_id=back_asset,
                            window=contested['window'])
        self.assertEqual(preview, {'status': 'suggested', 'start_beat': 4})
        self.assertEqual(
            self.submit(back, contested['request_id'], back_asset, contested['window'])['status'],
            'accepted',
        )
        aired = self.channel_play(channel_id, 6)
        self.assertEqual([row['beat'] for row in aired], list(range(6)))
        self.assertEqual([row['sample'] for row in aired], [None, None, 31, 32, 41, 42])
        self.assertEqual([row['session_id'] for row in aired],
                         [front, front, front, front, back, back])
        front_report = self.read_report(front)
        back_report = self.read_report(back)
        self.assertEqual(front_report['promises'][0]['beats'], [2, 3])
        self.assertEqual(front_report['promises'][0]['window'], [2, 6])
        self.assertEqual(back_report['promises'][0]['beats'], [4, 5])
        self.assertEqual(back_report['promises'][0]['window'], [4, 6])

    def test_empty_beats_follow_handoff_and_close_waits_for_it(self):
        front, _ = self.open_side('front')
        back, _ = self.open_side('back')
        channel_id = self.pair(front, back)['channel_id']
        aired = self.channel_play(channel_id, 3)
        self.assertEqual([row['output_id'] for row in aired], ['morning-room'] * 3)
        self.assertEqual([row['session_id'] for row in aired], [front] * 3)
        # Neither session can close before the channel reaches the handoff beat.
        self.assertEqual(self.call('close', http=409, session_id=front)['error'], 'session_busy')
        self.assertEqual(self.call('close', http=409, session_id=back)['error'], 'session_busy')
        aired = self.channel_play(channel_id, 2)
        self.assertEqual((aired[0]['output_id'], aired[0]['session_id']), ('morning-room', front))
        self.assertEqual((aired[1]['output_id'], aired[1]['session_id']), ('midday-room', back))
        # Past the handoff beat an idle session closes on its own; the channel
        # keeps airing the remaining session's background.
        self.call('close', session_id=front)
        aired = self.channel_play(channel_id, 1)
        self.assertEqual((aired[0]['output_id'], aired[0]['session_id']), ('midday-room', back))
        self.call('close', session_id=back)
        fault = self.call('channel/tick', http=409, channel_id=channel_id)
        self.assertEqual(fault['error'], 'channel_idle')
        self.assertEqual(self.channel_row(channel_id)['beat'], 6)

    def test_back_close_lets_front_tail_finish(self):
        front, front_asset = self.open_side('front')
        back, _ = self.open_side('back')
        channel_id = self.pair(front, back, handoff_beat=2)['channel_id']
        self.assertEqual(self.submit(front, 'same', front_asset, [1, 4])['status'], 'accepted')
        aired = self.channel_play(channel_id, 2)
        self.assertEqual([(row['beat'], row['source']) for row in aired],
                         [(0, 'background'), (1, 'promise')])
        # The back session has nothing to play and closes once the channel
        # reaches the handoff beat, even though the front tail is still running.
        self.call('close', session_id=back)
        aired = self.channel_play(channel_id, 2)
        self.assertEqual([(row['beat'], row['sample'], row['session_id']) for row in aired],
                         [(2, 32, front), (3, 33, front)])
        # No remainder and no open owner: the channel refuses to advance.
        fault = self.call('channel/tick', http=409, channel_id=channel_id)
        self.assertEqual(fault['error'], 'channel_idle')
        self.assertEqual(self.channel_row(channel_id)['beat'], 4)
        report = self.read_report(front)
        self.assertEqual(report['promises'][0]['beats'], [1, 2, 3])
        self.assertTrue(report['promises'][0]['fulfilled'])
        self.assertEqual(self.call('timeline', session_id=back)['outputs'], [])

    def test_channel_submission_uses_live_cursor(self):
        movable = self.handoff['movable_front']
        front, front_asset = self.open_side('front', frames=movable['frames'])
        back, back_asset = self.open_side('back')
        channel_id = self.pair(front, back)['channel_id']
        self.assertEqual(self.submit(front, 'same', front_asset, movable['window'])['status'],
                         'accepted')
        arrived = Event()
        resume = Event()

        def hold_before_admit(request):
            if request.request_id == 'late':
                arrived.set()
                await_event(resume, 'submission was not released')

        self.restart(before_admit=hold_before_admit)
        with ThreadPoolExecutor(max_workers=1) as pool:
            submission = pool.submit(self.submit, back, 'late', back_asset, [4, 6])
            try:
                await_event(arrived, 'submission did not reach the playout link')
                pending = self.call('lookup', http=202, session_id=back, request_id='late')
                self.assertEqual(pending['status'], 'pending')
                aired = self.channel_play(channel_id, 5)
            finally:
                resume.set()
            result = submission.result(timeout=15)
        # The request crossed beats in transit, so admission used the live
        # channel cursor: beat 4 has already aired and [4, 6) no longer fits.
        self.assertEqual(result['status'], 'unschedulable')
        self.assertEqual([row['sample'] for row in aired], [None, None, 31, 32, None])
        self.assertEqual((aired[4]['output_id'], aired[4]['session_id']), ('midday-room', back))
        first = self.submit(back, 'late', back_asset, [4, 6])
        self.assertEqual(first['status'], 'unschedulable')
        self.assertEqual(self.call('lookup', session_id=back, request_id='late'), first)
