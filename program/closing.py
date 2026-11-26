from air.obligations import read_pending_obligations
from air.state import read_cursor
from program.database import conflict, write_transaction
from program.opening import read_session, session_is_open
from review.final_report import finalize_report


def close(database, session_id):
    with write_transaction(database) as connection:
        session = read_session(connection, session_id)
        report_id = session_id
        if session_is_open(session):
            cursor = read_cursor(connection, session_id)
            if cursor.active is not None or read_pending_obligations(connection, session_id):
                raise conflict('session_busy', 'playout must finish before closing the session')
            connection.execute(
                'UPDATE sessions SET status = ? WHERE session_id = ?', ('closed', session_id)
            )
            report_id = finalize_report(connection, session_id).report_id
        return {'status': 'closed', 'session_id': session_id, 'report_id': report_id}
