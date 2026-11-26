from air.channel import read_pairing
from air.obligations import read_pending_obligations
from air.scheduling import find_schedule
from air.state import ActiveClip, read_cursor, start_clip
from program.assets import read_asset
from program.database import conflict, name, write_transaction
from program.opening import require_open_session


def ident(database, session_id, request_id, asset_id):
    request_id = name(request_id, 'request_id')
    with write_transaction(database) as connection:
        require_open_session(connection, session_id)
        if read_pairing(connection, session_id) is not None:
            raise conflict('session_paired', 'the session plays out on its paired channel')
        asset = read_asset(connection, session_id, asset_id, kind='ident')
        cursor = read_cursor(connection, session_id)
        if cursor.active is not None:
            return {'status': 'unschedulable'}

        pending = read_pending_obligations(connection, session_id)
        if find_schedule(pending, cursor.beat + asset.frame_count) is None:
            return {'status': 'unschedulable'}
        clip = ActiveClip(asset_id, 'ident', request_id, 0, asset.frame_count)
        start_clip(connection, session_id, clip)
        return {'status': 'started', 'start_beat': cursor.beat}
