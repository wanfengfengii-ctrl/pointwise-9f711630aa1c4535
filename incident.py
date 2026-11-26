import sys
import unittest

from tests.test_broadcast import TestBroadcast
from tests.test_http import TestHttp


def main():
    suite = unittest.TestSuite([
        TestBroadcast('test_replan_with_narrow_window'),
        TestBroadcast('test_ident_cannot_consume_promised_time'),
        TestBroadcast('test_lost_reply_keeps_promise'),
        TestBroadcast('test_started_ident_blocks_late_insert'),
        TestHttp('test_waiting_submission_uses_live_cursor'),
        TestHttp('test_committed_reply_can_wait_while_air_keeps_playing'),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.failures:
        cases = ', '.join(str(case) for case, _ in result.failures)
        print(f'AIRTIME_PROMISE_MISSED {cases}', file=sys.stderr)
    if result.errors:
        cases = ', '.join(str(case) for case, _ in result.errors)
        print(f'INCIDENT_RUNTIME_ERROR {cases}', file=sys.stderr)
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
