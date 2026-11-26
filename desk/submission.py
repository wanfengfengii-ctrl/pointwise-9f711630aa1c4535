from air.admission import admit
from air.outcomes import read_outcome
from desk.receipts import ResultUnavailable, resolve_receipt
from desk.requests import begin_request
from program.database import entity_id, read_transaction, write_transaction


class AirLink:
    """In-process delivery between independent desk and playout transactions."""

    def __init__(self, database, *, before_admit=None, after_admit=None, before_lookup=None):
        self.database = database
        self.before_admit = before_admit
        self.after_admit = after_admit
        self.before_lookup = before_lookup

    def admit(self, request):
        if self.before_admit is not None:
            self.before_admit(request)
        outcome = admit(
            self.database, request.session_id, request.request_id, request.asset_id, request.window,
        )
        if self.after_admit is not None:
            self.after_admit(request, outcome)
        return outcome

    def read_outcome(self, session_id, request_id):
        if self.before_lookup is not None:
            self.before_lookup(session_id, request_id)
        with read_transaction(self.database) as connection:
            return read_outcome(connection, session_id, request_id)


def submit(database, link, session_id, request_id, asset_id, window):
    session_id = entity_id(session_id, 'session_id')
    asset_id = entity_id(asset_id, 'asset_id')
    with write_transaction(database) as connection:
        request = begin_request(connection, session_id, request_id, asset_id, window)
    if request.status is not None:
        return request.receipt()

    try:
        outcome = link.admit(request)
    except ResultUnavailable:
        outcome = None
    return resolve_receipt(database, request, outcome)
