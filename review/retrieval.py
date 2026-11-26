import json

from program.database import entity_id, missing, read_transaction


def report(database, report_id):
    report_id = entity_id(report_id, 'report_id')
    with read_transaction(database) as connection:
        row = connection.execute(
            'SELECT payload FROM shift_reports WHERE report_id = ?', (report_id,)
        ).fetchone()
        if row is None:
            raise missing('shift report')
        return json.loads(row['payload'])
