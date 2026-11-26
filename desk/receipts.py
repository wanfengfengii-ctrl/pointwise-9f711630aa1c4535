from desk.requests import confirm_outcome, read_request
from program.database import entity_id, missing, name, read_transaction, write_transaction


class ResultUnavailable(Exception):
    """The result could not be carried across the link at this time."""


def resolve_receipt(database, request, outcome):
    if outcome is None:
        with read_transaction(database) as connection:
            return read_request(connection, request.session_id, request.request_id).receipt()
    if (outcome.session_id, outcome.request_id) != (request.session_id, request.request_id):
        raise RuntimeError('playout replied for a different request')
    with write_transaction(database) as connection:
        return confirm_outcome(connection, outcome).receipt()


def lookup(database, link, session_id, request_id):
    session_id = entity_id(session_id, 'session_id')
    request_id = name(request_id, 'request_id')
    with read_transaction(database) as connection:
        request = read_request(connection, session_id, request_id)
    if request is None:
        raise missing('edit request')
    if request.status is not None:
        return request.receipt()
    try:
        outcome = link.read_outcome(session_id, request_id)
    except ResultUnavailable:
        outcome = None
    return resolve_receipt(database, request, outcome)
