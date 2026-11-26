from dataclasses import dataclass, replace

from air.obligations import mark_fulfilled, mark_started, read_pending_obligations
from air.scheduling import find_schedule
from air.state import ActiveClip, append_output, read_cursor
from program.assets import read_asset, read_asset_frame
from program.background import background_output
from program.database import conflict, entity_id, invalid, missing, write_transaction
from program.opening import read_session, session_is_open

MAX_BEAT = 2**63 - 1


@dataclass(frozen=True)
class ChannelObligation:
    """A promise placed on the shared channel clock with its handoff-aware window."""

    session_id: int
    request_id: str
    asset_id: int
    start: int
    end: int
    duration: int


def read_channel(connection, channel_id):
    channel_id = entity_id(channel_id, 'channel_id')
    row = connection.execute(
        'SELECT * FROM channels WHERE channel_id = ?', (channel_id,),
    ).fetchone()
    if row is None:
        raise missing('channel')
    return dict(row)


def read_pairing(connection, session_id):
    row = connection.execute(
        '''SELECT * FROM channels
           WHERE front_session_id = ? OR back_session_id = ?''',
        (session_id, session_id),
    ).fetchone()
    return dict(row) if row is not None else None


def _session_ids(channel):
    return channel['front_session_id'], channel['back_session_id']


def channel_candidate(channel, session_id, obligation):
    """Translate a session promise into the window it may occupy on the channel."""
    handoff = channel['handoff_beat']
    start, end = obligation.start, obligation.end
    if session_id == channel['front_session_id']:
        # A front clip must air its first frame no later than the beat before the
        # handoff; frames already started may continue across the handoff beat.
        end = min(end, handoff - 1 + obligation.duration)
    elif session_id == channel['back_session_id']:
        # A back clip cannot air its first frame before the handoff beat.
        start = max(start, handoff)
    else:
        raise RuntimeError('session does not belong to the channel')
    return ChannelObligation(session_id, obligation.request_id, obligation.asset_id,
                             start, end, obligation.duration)


def channel_pending(connection, channel):
    pending = []
    for session_id in _session_ids(channel):
        for obligation in read_pending_obligations(connection, session_id):
            pending.append(channel_candidate(channel, session_id, obligation))
    return pending


def channel_active(connection, channel):
    found = []
    for session_id in _session_ids(channel):
        active = read_cursor(connection, session_id).active
        if active is not None:
            found.append((session_id, active))
    if len(found) > 1:
        raise RuntimeError('a channel cannot have two active clips')
    return found[0] if found else (None, None)


def channel_first_free(connection, channel):
    _, active = channel_active(connection, channel)
    free = channel['beat']
    if active is not None:
        free += active.remaining
    return free


def clip_output(connection, session_id, beat, clip):
    asset = read_asset(connection, session_id, clip.asset_id)
    return {
        'beat': beat,
        'source': clip.source,
        'output_id': asset.asset_id,
        'release_id': asset.release_id,
        'frame_index': clip.frame_index,
        'sample': read_asset_frame(connection, asset, clip.frame_index),
        'request_id': clip.request_id,
    }


def _has_broadcast_progress(connection, session_id):
    requested = connection.execute(
        'SELECT 1 FROM edit_requests WHERE session_id = ? LIMIT 1', (session_id,),
    ).fetchone()
    aired = connection.execute(
        'SELECT 1 FROM outputs WHERE session_id = ? LIMIT 1', (session_id,),
    ).fetchone()
    return (requested is not None or aired is not None
            or read_cursor(connection, session_id).active is not None)


def pair_channel(database, front_session_id, back_session_id, handoff_beat):
    front_session_id = entity_id(front_session_id, 'front_session_id')
    back_session_id = entity_id(back_session_id, 'back_session_id')
    if type(handoff_beat) is not int or not 1 <= handoff_beat <= MAX_BEAT:
        raise invalid('handoff_beat must be a positive integer')
    if front_session_id == back_session_id:
        raise invalid('front and back sessions must be different')
    with write_transaction(database) as connection:
        for session_id in (front_session_id, back_session_id):
            session = read_session(connection, session_id)
            if not session_is_open(session):
                raise conflict('session_closed', 'the session is closed')
            if read_pairing(connection, session_id) is not None:
                raise conflict('session_paired', 'the session is already paired to a channel')
            if _has_broadcast_progress(connection, session_id):
                raise conflict('session_busy', 'the session already has broadcast progress')
        cursor = connection.execute(
            '''INSERT INTO channels (front_session_id, back_session_id, handoff_beat, beat)
               VALUES (?, ?, ?, 0)''',
            (front_session_id, back_session_id, handoff_beat),
        )
        channel_id = cursor.lastrowid
    return {'channel_id': channel_id}


def _clip_due(connection, channel, beat):
    schedule = find_schedule(channel_pending(connection, channel), beat)
    if schedule is None:
        raise RuntimeError('formally accepted obligations cannot be played')
    if not schedule or schedule[0].start > beat:
        return None, None
    obligation = schedule[0].obligation
    mark_started(connection, obligation.session_id, obligation.request_id)
    clip = ActiveClip(obligation.asset_id, 'promise', obligation.request_id, 0,
                      obligation.duration)
    return obligation.session_id, clip


def _background_line(connection, channel, beat):
    if beat < channel['handoff_beat']:
        owner_id = channel['front_session_id']
    else:
        owner_id = channel['back_session_id']
    session = read_session(connection, owner_id)
    if not session_is_open(session):
        raise conflict('channel_idle', 'no open session can own the next channel beat')
    return owner_id, background_output(beat, session['background_id'])


def _advance_channel(connection, channel, owner_id, next_active):
    new_beat = channel['beat'] + 1
    updated = connection.execute(
        'UPDATE channels SET beat = ? WHERE channel_id = ? AND beat = ?',
        (new_beat, channel['channel_id'], channel['beat']),
    )
    if updated.rowcount != 1:
        raise RuntimeError('channel cursor changed during tick')
    for session_id in _session_ids(channel):
        if session_id == owner_id:
            params = (None, None, None, None)
            if next_active is not None:
                params = (next_active.asset_id, next_active.source,
                          next_active.request_id, next_active.frame_index)
            updated = connection.execute(
                '''UPDATE playout SET beat = ?, active_asset_id = ?, source = ?,
                       request_id = ?, frame_index = ?
                   WHERE session_id = ? AND beat = ?''',
                (new_beat, *params, session_id, channel['beat']),
            )
        else:
            updated = connection.execute(
                'UPDATE playout SET beat = ? WHERE session_id = ? AND beat = ?',
                (new_beat, session_id, channel['beat']),
            )
        if updated.rowcount != 1:
            raise RuntimeError('session cursor changed during channel tick')


def channel_tick(database, channel_id):
    with write_transaction(database) as connection:
        channel = read_channel(connection, channel_id)
        beat = channel['beat']
        owner_id, clip = channel_active(connection, channel)
        if clip is None:
            owner_id, clip = _clip_due(connection, channel, beat)
        next_active = None
        if clip is None:
            owner_id, output = _background_line(connection, channel, beat)
        else:
            output = clip_output(connection, owner_id, beat, clip)
            if clip.remaining > 1:
                next_active = replace(clip, frame_index=clip.frame_index + 1)
            elif clip.source == 'promise':
                mark_fulfilled(connection, owner_id, clip.request_id)
        append_output(connection, owner_id, output)
        _advance_channel(connection, channel, owner_id, next_active)
        return {**output, 'session_id': owner_id}
