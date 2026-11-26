from catalog.frames import read_frame, read_frames
from catalog.recordings import read_draft
from program.database import entity_id, missing, write_transaction


def release(database, draft_id):
    with write_transaction(database) as connection:
        draft = read_draft(connection, draft_id)
        cursor = connection.execute('INSERT INTO releases (draft_id) VALUES (?)', (draft_id,))
        return {'release_id': cursor.lastrowid, **draft}


def read_release(connection, release_id, *, include_frames=False):
    release_id = entity_id(release_id, 'release_id')
    row = connection.execute(
        '''SELECT releases.release_id, drafts.*
           FROM releases JOIN drafts USING (draft_id)
           WHERE releases.release_id = ?''',
        (release_id,),
    ).fetchone()
    if row is None:
        raise missing('release')
    version = dict(row)
    if include_frames:
        version['frames'] = read_frames(connection, version['draft_id'])
    return version


def read_release_frame(connection, release_id, frame_index):
    version = read_release(connection, release_id)
    return read_frame(connection, version['draft_id'], frame_index)
