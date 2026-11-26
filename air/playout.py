from dataclasses import replace

from air.channel import clip_output, read_pairing
from air.obligations import mark_fulfilled, mark_started, read_pending_obligations
from air.scheduling import find_schedule
from air.state import ActiveClip, advance_cursor, append_output, read_cursor
from program.background import background_output
from program.database import conflict, write_transaction
from program.opening import require_open_session


def _clip_due(connection, session_id, beat):
    pending = read_pending_obligations(connection, session_id)
    schedule = find_schedule(pending, beat)
    if schedule is None:
        raise RuntimeError('formally accepted obligations cannot be played')
    if not schedule or schedule[0].start > beat:
        return None
    obligation = schedule[0].obligation
    mark_started(connection, session_id, obligation.request_id)
    return ActiveClip(obligation.asset_id, 'promise', obligation.request_id, 0, obligation.duration)


def tick(database, session_id):
    with write_transaction(database) as connection:
        session = require_open_session(connection, session_id)
        if read_pairing(connection, session_id) is not None:
            raise conflict('session_paired', 'the session plays out on its paired channel')
        cursor = read_cursor(connection, session_id)
        clip = cursor.active or _clip_due(connection, session_id, cursor.beat)
        next_active = None
        if clip is None:
            output = background_output(cursor.beat, session['background_id'])
        else:
            output = clip_output(connection, session_id, cursor.beat, clip)
            if clip.remaining > 1:
                next_active = replace(clip, frame_index=clip.frame_index + 1)
            elif clip.source == 'promise':
                mark_fulfilled(connection, session_id, clip.request_id)
        append_output(connection, session_id, output)
        advance_cursor(connection, session_id, cursor, next_active)
        return output
