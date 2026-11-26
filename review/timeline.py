from air.state import read_outputs
from program.database import read_transaction
from program.opening import read_session


def read_timeline(connection, session_id):
    return {'session_id': session_id, 'outputs': read_outputs(connection, session_id)}


def timeline(database, session_id):
    with read_transaction(database) as connection:
        read_session(connection, session_id)
        return read_timeline(connection, session_id)
