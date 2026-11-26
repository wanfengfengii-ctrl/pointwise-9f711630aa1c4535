from program.database import name


def bind_background(connection, background_id):
    if background_id is None:
        row = connection.execute(
            'SELECT background_id FROM station_defaults WHERE singleton = 1'
        ).fetchone()
        return row['background_id']
    return name(background_id, 'background_id')


def background_output(beat, background_id):
    return {
        'beat': beat,
        'source': 'background',
        'output_id': background_id,
        'release_id': None,
        'frame_index': None,
        'sample': None,
        'request_id': None,
    }
