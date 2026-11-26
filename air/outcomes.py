from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionOutcome:
    session_id: int
    request_id: str
    asset_id: int
    window: tuple[int, int]
    status: str


def read_outcome(connection, session_id, request_id):
    row = connection.execute(
        'SELECT * FROM admission_outcomes WHERE session_id = ? AND request_id = ?',
        (session_id, request_id),
    ).fetchone()
    if row is None:
        return None
    return AdmissionOutcome(
        session_id=row['session_id'],
        request_id=row['request_id'],
        asset_id=row['asset_id'],
        window=(int(row['window_start']), int(row['window_end'])),
        status=row['status'],
    )


def save_first_outcome(connection, outcome):
    window_start, window_end = outcome.window
    connection.execute(
        '''INSERT INTO admission_outcomes
           (session_id, request_id, asset_id, window_start, window_end, status)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (outcome.session_id, outcome.request_id, outcome.asset_id,
         str(window_start), str(window_end), outcome.status),
    )
