from dataclasses import dataclass


@dataclass(frozen=True)
class Obligation:
    request_id: str
    asset_id: int
    start: int
    end: int
    duration: int
    progress: str = 'pending'


def add_obligation(connection, outcome):
    if outcome.status != 'accepted':
        raise RuntimeError('only an accepted outcome can create an obligation')
    connection.execute(
        'INSERT INTO obligations (session_id, request_id, progress) VALUES (?, ?, ?)',
        (outcome.session_id, outcome.request_id, 'pending'),
    )


def _read_obligations(connection, session_id, progress=None):
    query = '''SELECT o.request_id, o.progress, r.asset_id, r.window_start, r.window_end,
                      a.frame_count
               FROM obligations AS o
               JOIN admission_outcomes AS r USING (session_id, request_id)
               JOIN assets AS a ON a.session_id = r.session_id AND a.release_id = r.asset_id
               WHERE o.session_id = ?'''
    parameters = (session_id,)
    if progress is not None:
        query += ' AND o.progress = ?'
        parameters += (progress,)
    rows = connection.execute(query + ' ORDER BY o.request_id', parameters)
    return [Obligation(
        request_id=row['request_id'], asset_id=row['asset_id'],
        start=int(row['window_start']), end=int(row['window_end']),
        duration=row['frame_count'], progress=row['progress'],
    ) for row in rows]


def read_pending_obligations(connection, session_id):
    return _read_obligations(connection, session_id, 'pending')


def read_all_obligations(connection, session_id):
    return _read_obligations(connection, session_id)


def _set_progress(connection, session_id, request_id, before, after):
    updated = connection.execute(
        '''UPDATE obligations SET progress = ?
           WHERE session_id = ? AND request_id = ? AND progress = ?''',
        (after, session_id, request_id, before),
    )
    if updated.rowcount != 1:
        raise RuntimeError(f'invalid obligation transition to {after}')


def mark_started(connection, session_id, request_id):
    _set_progress(connection, session_id, request_id, 'pending', 'active')


def mark_fulfilled(connection, session_id, request_id):
    _set_progress(connection, session_id, request_id, 'active', 'fulfilled')
