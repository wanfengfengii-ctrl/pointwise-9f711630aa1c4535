from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveClip:
    asset_id: int
    source: str
    request_id: str
    frame_index: int
    frame_count: int

    @property
    def remaining(self):
        return self.frame_count - self.frame_index


@dataclass(frozen=True)
class PlayoutCursor:
    beat: int
    active: ActiveClip | None

    @property
    def first_free(self):
        return self.beat + (self.active.remaining if self.active else 0)


def initialize_playout(connection, session_id):
    connection.execute('INSERT INTO playout (session_id, beat) VALUES (?, 0)', (session_id,))


def read_cursor(connection, session_id):
    row = connection.execute(
        '''SELECT p.*, a.frame_count
           FROM playout AS p LEFT JOIN assets AS a
               ON a.session_id = p.session_id AND a.release_id = p.active_asset_id
           WHERE p.session_id = ?''',
        (session_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f'missing playout cursor for session {session_id}')
    active = None
    if row['active_asset_id'] is not None:
        active = ActiveClip(
            row['active_asset_id'], row['source'], row['request_id'],
            row['frame_index'], row['frame_count'],
        )
        if active.remaining <= 0:
            raise RuntimeError('invalid active clip cursor')
    return PlayoutCursor(row['beat'], active)


def start_clip(connection, session_id, clip):
    updated = connection.execute(
        '''UPDATE playout SET active_asset_id = ?, source = ?, request_id = ?, frame_index = ?
           WHERE session_id = ? AND active_asset_id IS NULL''',
        (clip.asset_id, clip.source, clip.request_id, clip.frame_index, session_id),
    )
    if updated.rowcount != 1:
        raise RuntimeError('cannot start a clip over existing playout')


def advance_cursor(connection, session_id, cursor, active):
    params = (None, None, None, None)
    if active is not None:
        params = (active.asset_id, active.source, active.request_id, active.frame_index)
    updated = connection.execute(
        '''UPDATE playout SET beat = beat + 1, active_asset_id = ?, source = ?,
               request_id = ?, frame_index = ?
           WHERE session_id = ? AND beat = ?''',
        (*params, session_id, cursor.beat),
    )
    if updated.rowcount != 1:
        raise RuntimeError('playout cursor changed during tick')


def append_output(connection, session_id, output):
    background_id = output['output_id'] if output['source'] == 'background' else None
    sample = output['sample']
    connection.execute(
        '''INSERT INTO outputs (session_id, beat, source, background_id, release_id,
                                frame_index, sample, request_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (session_id, output['beat'], output['source'], background_id,
         output['release_id'], output['frame_index'],
         str(sample) if sample is not None else None, output['request_id']),
    )


def read_outputs(connection, session_id):
    rows = connection.execute(
        '''SELECT beat, source, background_id, release_id, frame_index, sample, request_id
           FROM outputs WHERE session_id = ? ORDER BY beat''',
        (session_id,),
    )
    outputs = []
    for row in rows:
        output = dict(row)
        background_id = output.pop('background_id')
        output['output_id'] = background_id if background_id is not None else output['release_id']
        if output['sample'] is not None:
            output['sample'] = int(output['sample'])
        outputs.append(output)
    return outputs
