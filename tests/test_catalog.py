from program.database import read_transaction
from tests.support import LiveCase, emit_scenario


class TestCatalog(LiveCase):
    def test_empty_recording(self):
        fault = self.call('record', http=400, recording_id='empty', frames=[])
        self.assertEqual(fault['error'], 'invalid_input')
        self.assertEqual(self.call('release', http=404, draft_id=1)['error'], 'not_found')
        with read_transaction(self.database) as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM drafts').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT count(*) FROM releases').fetchone()[0], 0)
        self.assertEqual(self.call('record', recording_id='valid', frames=[0])['frame_count'], 1)

    def test_non_integer_recording_does_not_replace_valid_draft(self):
        good = self.call('record', recording_id='weather', frames=[7])
        for sample in (False, True, '7', 7.0, None, [7]):
            with self.subTest(sample=sample):
                self.call('record', http=400, recording_id='weather', frames=[0, sample])
        with read_transaction(self.database) as connection:
            drafts = connection.execute('SELECT draft_id, frame_count FROM drafts').fetchall()
            self.assertEqual([tuple(row) for row in drafts], [(good['draft_id'], 1)])
        release = self.call('release', draft_id=good['draft_id'])
        self.assertEqual(release['frame_count'], 1)

    def test_open_uses_released_frames(self):
        old = self.issue('correction', [0, 7])
        first = self.call('open', program_id='morning', release_ids=[old])
        new = self.issue('correction', [9])
        second = self.call('open', program_id='afternoon', release_ids=[new])
        self.assertNotEqual(old, new)
        self.assertNotEqual(first['session_id'], second['session_id'])
        self.assertEqual(first['assets'][0]['frame_count'], 2)
        self.assertEqual(second['assets'][0]['frame_count'], 1)
        old_id, new_id = first['session_id'], second['session_id']
        self.assertEqual(self.submit(old_id, 'insert', old, [0, 2])['status'], 'accepted')
        self.assertEqual(self.submit(new_id, 'insert', new, [0, 1])['status'], 'accepted')
        old_frames = self.play(old_id, 2)
        new_frames = self.play(new_id, 1)
        expected = [[0, 0, old], [1, 7, old]]
        actual = [[row['beat'], row['sample'], row['release_id']] for row in old_frames]
        self.assertEqual(actual, expected)
        new_actual = [[row['beat'], row['sample'], row['release_id']] for row in new_frames]
        self.assertEqual(new_actual, [[0, 9, new]])
        old_report = self.read_report(old_id)
        self.assertEqual(old_report['outputs'], old_frames)
        self.assertTrue(old_report['promises'][0]['fulfilled'])
        emit_scenario(
            'freeze', {'released': [0, 7], 'recorded_later': [9]}, expected, actual,
            {'old_samples': [0, 7], 'new_samples': [9]},
            {'old_samples': [row['sample'] for row in old_report['outputs']],
             'new_samples': [row['sample'] for row in new_frames]},
            ['catalog/releases.py', 'program/assets.py', 'air/playout.py', 'review/final_report.py'],
            'Two selected releases retain their original literal sample sequences and start at beat zero.',
        )

    def test_unknown_release_leaves_no_partial_session(self):
        release = self.issue('guide', [10])
        fault = self.call('open', http=404, program_id='morning', release_ids=[release, release + 1])
        self.assertEqual(fault['error'], 'not_found')
        with read_transaction(self.database) as connection:
            for table in ('sessions', 'assets', 'playout'):
                self.assertEqual(connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.call('open', program_id='morning', release_ids=[release])['next_beat'], 0)

    def test_large_integer_is_preserved(self):
        value = -(10**30)
        release = self.issue('sample', [value])
        session = self.call('open', program_id='morning', release_ids=[release])['session_id']
        self.assertEqual(self.submit(session, 'large', release, [-(10**30), 1])['status'], 'accepted')
        self.assertEqual(self.play(session, 1)[0]['sample'], value)
        self.assertTrue(self.read_report(session)['promises'][0]['fulfilled'])
