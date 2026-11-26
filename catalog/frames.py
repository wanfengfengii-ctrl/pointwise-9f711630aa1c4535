from program.database import invalid


def normalize_frames(frames):
    if not isinstance(frames, list) or not frames:
        raise invalid('frames must be a non-empty array')
    if any(type(sample) is not int for sample in frames):
        raise invalid('each frame must be an integer sample')
    return tuple(frames)


def store_frames(connection, draft_id, frames):
    # Decimal text preserves integer samples outside SQLite's signed 64-bit range.
    connection.executemany(
        'INSERT INTO frames (draft_id, frame_index, sample) VALUES (?, ?, ?)',
        ((draft_id, index, str(sample)) for index, sample in enumerate(frames)),
    )


def read_frames(connection, draft_id):
    rows = connection.execute(
        'SELECT sample FROM frames WHERE draft_id = ? ORDER BY frame_index', (draft_id,)
    )
    return tuple(int(row['sample']) for row in rows)


def read_frame(connection, draft_id, frame_index):
    row = connection.execute(
        'SELECT sample FROM frames WHERE draft_id = ? AND frame_index = ?',
        (draft_id, frame_index),
    ).fetchone()
    if row is None:
        raise RuntimeError(f'missing frame {frame_index} for draft {draft_id}')
    return int(row['sample'])
