from catalog.audition import audition
from catalog.frames import normalize_frames, store_frames
from program.database import entity_id, missing, name, write_transaction


def record(database, recording_id, frames):
    recording_id = name(recording_id, 'recording_id')
    frames = normalize_frames(frames)
    inspected = audition(frames)
    with write_transaction(database) as connection:
        cursor = connection.execute(
            'INSERT INTO drafts (recording_id, frame_count) VALUES (?, ?)',
            (recording_id, inspected.frame_count),
        )
        draft_id = cursor.lastrowid
        store_frames(connection, draft_id, frames)
    return {
        'draft_id': draft_id,
        'recording_id': recording_id,
        'frame_count': inspected.frame_count,
    }


def read_draft(connection, draft_id):
    draft_id = entity_id(draft_id, 'draft_id')
    row = connection.execute('SELECT * FROM drafts WHERE draft_id = ?', (draft_id,)).fetchone()
    if row is None:
        raise missing('draft')
    return dict(row)
