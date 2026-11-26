from air.admission import estimate_slot
from desk.requests import normalize_window


def preview(database, session_id, asset_id, window):
    start_beat = estimate_slot(database, session_id, asset_id, normalize_window(window))
    if start_beat is None:
        return {'status': 'unschedulable'}
    return {'status': 'suggested', 'start_beat': start_beat}
