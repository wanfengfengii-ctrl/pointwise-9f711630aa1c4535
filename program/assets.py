from dataclasses import dataclass

from catalog.releases import read_release, read_release_frame
from program.database import entity_id, invalid


@dataclass(frozen=True)
class FrozenAsset:
    asset_id: int
    release_id: int
    recording_id: str
    frame_count: int
    kind: str


def _release_list(values, field):
    if not isinstance(values, list):
        raise invalid(f'{field} must be an array of release identifiers')
    identifiers = [entity_id(value, field) for value in values]
    if len(identifiers) != len(set(identifiers)):
        raise invalid(f'{field} contains duplicate release identifiers')
    return identifiers


def build_asset_table(connection, release_ids, ident_release_ids):
    release_ids = _release_list(release_ids, 'release_ids')
    ident_ids = set(_release_list(ident_release_ids, 'ident_release_ids'))
    if not ident_ids.issubset(release_ids):
        raise invalid('each ident release must also be selected in release_ids')
    assets = []
    for release_id in release_ids:
        version = read_release(connection, release_id)
        assets.append(FrozenAsset(
            asset_id=release_id,
            release_id=release_id,
            recording_id=version['recording_id'],
            frame_count=version['frame_count'],
            kind='ident' if release_id in ident_ids else 'insert',
        ))
    return assets


def store_asset_table(connection, session_id, assets):
    connection.executemany(
        'INSERT INTO assets (session_id, release_id, frame_count, kind) VALUES (?, ?, ?, ?)',
        ((session_id, item.release_id, item.frame_count, item.kind) for item in assets),
    )


def read_asset(connection, session_id, asset_id, *, kind=None):
    asset_id = entity_id(asset_id, 'asset_id')
    row = connection.execute(
        '''SELECT a.release_id, d.recording_id, a.frame_count, a.kind
           FROM assets AS a
           JOIN releases AS r ON r.release_id = a.release_id
           JOIN drafts AS d ON d.draft_id = r.draft_id
           WHERE a.session_id = ? AND a.release_id = ?''',
        (session_id, asset_id),
    ).fetchone()
    if row is None:
        raise invalid('asset does not belong to this session')
    if kind is not None and row['kind'] != kind:
        raise invalid(f'asset must be frozen as {kind}')
    return FrozenAsset(asset_id=asset_id, **dict(row))


def read_asset_frame(connection, asset, frame_index):
    if not 0 <= frame_index < asset.frame_count:
        raise RuntimeError('frame cursor outside frozen asset')
    return read_release_frame(connection, asset.release_id, frame_index)
