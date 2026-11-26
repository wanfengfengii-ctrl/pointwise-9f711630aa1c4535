from dataclasses import dataclass

from program.assets import read_asset
from program.database import conflict, invalid, name
from program.opening import require_open_session


@dataclass(frozen=True)
class EditRequest:
    session_id: int
    request_id: str
    asset_id: int
    window: tuple[int, int]
    status: str | None = None

    def receipt(self):
        return {
            'session_id': self.session_id,
            'request_id': self.request_id,
            'asset_id': self.asset_id,
            'window': list(self.window),
            'status': self.status or 'pending',
        }


def normalize_window(window):
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        raise invalid('window must contain two integer beat boundaries')
    start, end = window
    if type(start) is not int or type(end) is not int or start >= end:
        raise invalid('window must have integer boundaries with start < end')
    return start, end


def read_request(connection, session_id, request_id):
    row = connection.execute(
        'SELECT * FROM edit_requests WHERE session_id = ? AND request_id = ?',
        (session_id, request_id),
    ).fetchone()
    if row is None:
        return None
    return EditRequest(
        session_id=row['session_id'], request_id=row['request_id'], asset_id=row['asset_id'],
        window=(int(row['window_start']), int(row['window_end'])), status=row['status'],
    )


def begin_request(connection, session_id, request_id, asset_id, window):
    request_id = name(request_id, 'request_id')
    window = normalize_window(window)
    previous = read_request(connection, session_id, request_id)
    if previous is not None:
        if (previous.asset_id, previous.window) != (asset_id, window):
            raise conflict('request_conflict', 'the request already has different parameters')
        return previous

    require_open_session(connection, session_id)
    read_asset(connection, session_id, asset_id, kind='insert')
    start, end = window
    connection.execute(
        '''INSERT INTO edit_requests (session_id, request_id, asset_id, window_start, window_end)
           VALUES (?, ?, ?, ?, ?)''',
        (session_id, request_id, asset_id, str(start), str(end)),
    )
    return EditRequest(session_id, request_id, asset_id, window)


def confirm_outcome(connection, outcome):
    request = read_request(connection, outcome.session_id, outcome.request_id)
    if request is None or (request.asset_id, request.window) != (outcome.asset_id, outcome.window):
        raise RuntimeError('playout outcome does not match the original edit request')
    if request.status is not None and request.status != outcome.status:
        raise RuntimeError('playout outcome changed after confirmation')
    if request.status is None:
        connection.execute(
            'UPDATE edit_requests SET status = ? WHERE session_id = ? AND request_id = ?',
            (outcome.status, request.session_id, request.request_id),
        )
    return EditRequest(request.session_id, request.request_id, request.asset_id,
                       request.window, outcome.status)
