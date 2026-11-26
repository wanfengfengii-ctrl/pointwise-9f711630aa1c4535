import json
from dataclasses import asdict, dataclass

from air.obligations import read_all_obligations
from review.reconciliation import PromiseCheck, reconcile_promises
from review.timeline import read_timeline


@dataclass(frozen=True)
class ShiftReport:
    report_id: int
    session_id: int
    outputs: list[dict]
    promises: list[PromiseCheck]


def finalize_report(connection, session_id):
    timeline = read_timeline(connection, session_id)
    obligations = read_all_obligations(connection, session_id)
    outputs = timeline['outputs']
    checks = reconcile_promises(connection, obligations, outputs)
    # There is one report per session, so its number can be derived on close retry.
    report = ShiftReport(session_id, session_id, outputs, checks)
    connection.execute(
        'INSERT INTO shift_reports (report_id, payload) VALUES (?, ?)',
        (report.report_id, json.dumps(asdict(report), ensure_ascii=False)),
    )
    return report
