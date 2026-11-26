from dataclasses import asdict

from air.state import initialize_playout
from program.assets import build_asset_table, store_asset_table
from program.background import bind_background
from program.database import conflict, entity_id, missing, name, write_transaction


def open_program(database, program_id, release_ids, ident_release_ids=None, background_id=None):
    program_id = name(program_id, 'program_id')
    if ident_release_ids is None:
        ident_release_ids = []
    with write_transaction(database) as connection:
        assets = build_asset_table(connection, release_ids, ident_release_ids)
        background_id = bind_background(connection, background_id)
        cursor = connection.execute(
            'INSERT INTO sessions (program_id, background_id, status) VALUES (?, ?, ?)',
            (program_id, background_id, 'open'),
        )
        session_id = cursor.lastrowid
        store_asset_table(connection, session_id, assets)
        initialize_playout(connection, session_id)
    return {
        'session_id': session_id,
        'program_id': program_id,
        'background_id': background_id,
        'next_beat': 0,
        'assets': [asdict(asset) for asset in assets],
    }


def read_session(connection, session_id):
    session_id = entity_id(session_id, 'session_id')
    row = connection.execute(
        'SELECT * FROM sessions WHERE session_id = ?', (session_id,)
    ).fetchone()
    if row is None:
        raise missing('session')
    return dict(row)


def session_is_open(session):
    return session['status'] == 'open'


def require_open_session(connection, session_id):
    session = read_session(connection, session_id)
    if not session_is_open(session):
        raise conflict('session_closed', 'the session is closed')
    return session
