from air.obligations import Obligation, add_obligation, read_pending_obligations
from air.outcomes import AdmissionOutcome, read_outcome, save_first_outcome
from air.scheduling import find_schedule
from air.state import read_cursor
from program.assets import read_asset
from program.database import conflict, read_transaction, write_transaction
from program.opening import read_session, require_open_session, session_is_open


def _schedule_candidate(connection, session_id, candidate):
    cursor = read_cursor(connection, session_id)
    pending = read_pending_obligations(connection, session_id)
    return find_schedule([*pending, candidate], cursor.first_free)


def estimate_slot(database, session_id, asset_id, window):
    with read_transaction(database) as connection:
        require_open_session(connection, session_id)
        asset = read_asset(connection, session_id, asset_id, kind='insert')
        # An empty string is outside the formal request ID domain.
        candidate = Obligation('', asset_id, *window, asset.frame_count)
        schedule = _schedule_candidate(connection, session_id, candidate)
        if schedule is None:
            return None
        return next(placement.start for placement in schedule if placement.obligation is candidate)


def admit(database, session_id, request_id, asset_id, window):
    with write_transaction(database) as connection:
        session = read_session(connection, session_id)
        first = read_outcome(connection, session_id, request_id)
        if first is not None:
            if (first.asset_id, first.window) != (asset_id, window):
                raise conflict('request_conflict', 'the request already has different parameters')
            return first

        asset = read_asset(connection, session_id, asset_id, kind='insert')
        status = 'unschedulable'
        if session_is_open(session):
            candidate = Obligation(request_id, asset_id, *window, asset.frame_count)
            if _schedule_candidate(connection, session_id, candidate) is not None:
                status = 'accepted'
        outcome = AdmissionOutcome(session_id, request_id, asset_id, window, status)
        save_first_outcome(connection, outcome)
        if status == 'accepted':
            add_obligation(connection, outcome)
        return outcome
