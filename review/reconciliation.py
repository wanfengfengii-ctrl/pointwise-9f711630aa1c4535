from collections import defaultdict
from dataclasses import dataclass

from catalog.releases import read_release


@dataclass(frozen=True)
class PromiseCheck:
    request_id: str
    asset_id: int
    release_id: int
    window: list[int]
    beats: list[int]
    first_beat: int | None
    end_beat: int | None
    fulfilled: bool


def reconcile_promises(connection, obligations, outputs):
    played = defaultdict(list)
    for output in outputs:
        if output['source'] == 'promise':
            played[output['request_id']].append(output)

    releases = {}
    checks = []
    for obligation in obligations:
        release_id = obligation.asset_id
        if release_id not in releases:
            releases[release_id] = read_release(connection, release_id, include_frames=True)
        version = releases[release_id]
        rows = played[obligation.request_id]
        beats = [row['beat'] for row in rows]
        first = beats[0] if beats else None
        end = beats[-1] + 1 if beats else None
        fulfilled = (
            obligation.progress == 'fulfilled'
            and len(rows) == obligation.duration == len(version['frames'])
            and first is not None and obligation.start <= first
            and end <= obligation.end
            and beats == list(range(first, first + obligation.duration))
            and all(
                row['output_id'] == obligation.asset_id
                and row['release_id'] == release_id
                and row['frame_index'] == index
                and row['sample'] == version['frames'][index]
                for index, row in enumerate(rows)
            )
        )
        checks.append(PromiseCheck(
            obligation.request_id, obligation.asset_id, release_id,
            [obligation.start, obligation.end], beats, first, end, fulfilled,
        ))
    return checks
