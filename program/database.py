import sqlite3
from contextlib import contextmanager


class ServiceError(Exception):
    def __init__(self, http_status, code, message):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


def invalid(message):
    return ServiceError(400, 'invalid_input', message)


def missing(entity):
    return ServiceError(404, 'not_found', f'{entity} was not found')


def conflict(code, message):
    return ServiceError(409, code, message)


def name(value, field):
    if not isinstance(value, str) or not value.strip():
        raise invalid(f'{field} must be a non-empty string')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise invalid(f'{field} must contain valid Unicode') from exc
    return value


def entity_id(value, field):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise invalid(f'{field} must be a positive database identifier')
    return value


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS station_defaults (
           singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
           background_id TEXT NOT NULL
       )''',
    '''CREATE TABLE IF NOT EXISTS drafts (
           draft_id INTEGER PRIMARY KEY,
           recording_id TEXT NOT NULL,
           frame_count INTEGER NOT NULL CHECK (frame_count > 0)
       )''',
    '''CREATE TABLE IF NOT EXISTS frames (
           draft_id INTEGER NOT NULL REFERENCES drafts(draft_id),
           frame_index INTEGER NOT NULL CHECK (frame_index >= 0),
           sample TEXT NOT NULL,
           PRIMARY KEY (draft_id, frame_index)
       )''',
    '''CREATE TABLE IF NOT EXISTS releases (
           release_id INTEGER PRIMARY KEY,
           draft_id INTEGER NOT NULL REFERENCES drafts(draft_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS sessions (
           session_id INTEGER PRIMARY KEY,
           program_id TEXT NOT NULL,
           background_id TEXT NOT NULL,
           status TEXT NOT NULL CHECK (status IN ('open', 'closed'))
       )''',
    '''CREATE TABLE IF NOT EXISTS assets (
           session_id INTEGER NOT NULL REFERENCES sessions(session_id),
           release_id INTEGER NOT NULL REFERENCES releases(release_id),
           frame_count INTEGER NOT NULL CHECK (frame_count > 0),
           kind TEXT NOT NULL CHECK (kind IN ('insert', 'ident')),
           PRIMARY KEY (session_id, release_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS playout (
           session_id INTEGER PRIMARY KEY REFERENCES sessions(session_id),
           beat INTEGER NOT NULL CHECK (beat >= 0),
           active_asset_id INTEGER,
           source TEXT CHECK (source IN ('promise', 'ident')),
           request_id TEXT,
           frame_index INTEGER,
           FOREIGN KEY (session_id, active_asset_id)
               REFERENCES assets(session_id, release_id),
           CHECK ((active_asset_id IS NULL AND source IS NULL AND request_id IS NULL
                       AND frame_index IS NULL)
               OR (active_asset_id IS NOT NULL AND source IS NOT NULL AND request_id IS NOT NULL
                       AND frame_index IS NOT NULL AND frame_index >= 0))
       )''',
    '''CREATE TABLE IF NOT EXISTS edit_requests (
           session_id INTEGER NOT NULL,
           request_id TEXT NOT NULL,
           asset_id INTEGER NOT NULL,
           window_start TEXT NOT NULL,
           window_end TEXT NOT NULL,
           status TEXT CHECK (status IN ('accepted', 'unschedulable')),
           PRIMARY KEY (session_id, request_id),
           FOREIGN KEY (session_id, asset_id) REFERENCES assets(session_id, release_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS admission_outcomes (
           session_id INTEGER NOT NULL,
           request_id TEXT NOT NULL,
           asset_id INTEGER NOT NULL,
           window_start TEXT NOT NULL,
           window_end TEXT NOT NULL,
           status TEXT NOT NULL CHECK (status IN ('accepted', 'unschedulable')),
           PRIMARY KEY (session_id, request_id),
           FOREIGN KEY (session_id, asset_id) REFERENCES assets(session_id, release_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS obligations (
           session_id INTEGER NOT NULL,
           request_id TEXT NOT NULL,
           progress TEXT NOT NULL CHECK (progress IN ('pending', 'active', 'fulfilled')),
           PRIMARY KEY (session_id, request_id),
           FOREIGN KEY (session_id, request_id)
               REFERENCES admission_outcomes(session_id, request_id)
       )''',
    '''CREATE TABLE IF NOT EXISTS outputs (
           session_id INTEGER NOT NULL REFERENCES sessions(session_id),
           beat INTEGER NOT NULL CHECK (beat >= 0),
           source TEXT NOT NULL,
           background_id TEXT,
           release_id INTEGER,
           frame_index INTEGER,
           sample TEXT,
           request_id TEXT,
           PRIMARY KEY (session_id, beat),
           FOREIGN KEY (session_id, release_id) REFERENCES assets(session_id, release_id),
           CHECK ((source = 'background' AND background_id IS NOT NULL AND release_id IS NULL
                       AND frame_index IS NULL AND sample IS NULL AND request_id IS NULL)
               OR (source IN ('ident', 'promise') AND background_id IS NULL AND release_id IS NOT NULL
                       AND frame_index IS NOT NULL AND frame_index >= 0
                       AND sample IS NOT NULL AND request_id IS NOT NULL))
       )''',
    '''CREATE TABLE IF NOT EXISTS shift_reports (
           report_id INTEGER PRIMARY KEY REFERENCES sessions(session_id),
           payload TEXT NOT NULL
       )''',
    '''CREATE INDEX IF NOT EXISTS obligations_pending
       ON obligations(session_id, progress, request_id)''',
)


@contextmanager
def _transaction(database, begin):
    connection = sqlite3.connect(database, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute(begin)
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def read_transaction(database):
    return _transaction(database, 'BEGIN')


def write_transaction(database):
    return _transaction(database, 'BEGIN IMMEDIATE')


def initialize_database(database):
    with write_transaction(database) as connection:
        for statement in SCHEMA:
            connection.execute(statement)
        connection.execute(
            'INSERT OR IGNORE INTO station_defaults VALUES (1, ?)', ('river-room',)
        )
