import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import deque

from board import shape_messages, shape_protocol, stations_from, request_rates, BUILTIN_PROTOCOL


def msg(**kwargs):
    base = {
        "id": "1", "thread": "release-1", "sender": "linux", "recipient": "mac",
        "at": "2026-10-18 15:00:00.000Z", "subject": "hello", "body": "text",
        "baton": "mac", "issue": "TimeFlow#1", "read_at": "2026-10-18 15:01:00.000Z",
    }
    base.update(kwargs)
    return base


class ShapeTests(unittest.TestCase):
    def test_last_message_holds_the_baton(self):
        threads = shape_messages([
            msg(id="1", at="2026-10-18 14:00:00.000Z", baton="mac", subject="first"),
            msg(id="2", at="2026-10-18 16:00:00.000Z", sender="mac", recipient="ian",
                baton="ian", subject="over to you", read_at=None),
        ])
        self.assertEqual(len(threads), 1)
        self.assertEqual(threads[0]["baton"], "ian")
        self.assertEqual(threads[0]["subject"], "over to you")
        self.assertEqual(threads[0]["count"], 2)
        self.assertEqual(len(threads[0]["unread"]), 1)
        self.assertEqual(threads[0]["messages"][0]["at"], "2026-10-18T14:00:00.000Z")

    def test_stations_split_holding_and_waiting(self):
        threads = shape_messages([
            msg(thread="a", baton="mac", sender="linux", recipient="mac"),
            msg(thread="b", baton="ian", sender="mac", recipient="ian",
                at="2026-10-18 17:00:00.000Z"),
            msg(thread="c", baton="none", sender="linux", recipient="mac",
                at="2026-10-18 12:00:00.000Z"),
        ])
        stations, settled = stations_from(threads)
        by_id = {row["id"]: row for row in stations}
        self.assertEqual([row["thread"] for row in by_id["mac"]["holding"]], ["a"])
        self.assertEqual(by_id["linux"]["waiting"][0]["on"], "mac")
        self.assertEqual([row["thread"] for row in by_id["ian"]["holding"]], ["b"])
        self.assertEqual([row["thread"] for row in settled], ["c"])
        self.assertEqual(by_id["linux"]["holding"], [])

    def test_stations_count_their_unread_notes(self):
        threads = shape_messages([
            msg(thread="a", recipient="ian", baton="ian", read_at=None),
            msg(thread="a", id="2", recipient="ian", baton="ian", read_at=None,
                at="2026-10-18 16:00:00.000Z"),
            msg(thread="b", recipient="mac", baton="mac", read_at=None),
            msg(thread="c", recipient="ian", baton="none"),             # read
        ])
        stations, _ = stations_from(threads)
        unread = {row["id"]: row["unread"] for row in stations}
        self.assertEqual(unread, {"linux": 0, "mac": 1, "ian": 2})

    def test_station_rows_carry_the_threads_issue(self):
        # The page resolves a bare #16 in a subject against this, so it must be the
        # thread's own repo, not a guess.
        threads = shape_messages([
            msg(thread="a", baton="mac", issue="tester-pool#16", subject="#16 merged"),
            msg(thread="b", baton="mac", issue="", at="2026-10-18 17:00:00.000Z"),
        ])
        stations, _ = stations_from(threads)
        mac = {row["thread"]: row for row in {r["id"]: r for r in stations}["mac"]["holding"]}
        self.assertEqual(mac["a"]["issue"], "tester-pool#16")
        self.assertEqual(mac["b"]["issue"], "")

    def test_request_rates_read_like_load_averages(self):
        samples = deque()
        # 10 requests a minute for 15 minutes, sampled every 8 seconds.
        for i in range(0, 15 * 60 + 1, 8):
            got = request_rates(samples, 1000.0 + i, 500 + i * 10 // 60, 3600 + i)
        self.assertAlmostEqual(got["per_minute"]["1"], 10, delta=0.6)
        self.assertAlmostEqual(got["per_minute"]["5"], 10, delta=0.3)
        self.assertAlmostEqual(got["per_minute"]["15"], 10, delta=0.3)
        self.assertGreaterEqual(got["covered_secs"]["15"], 890)

    def test_request_rates_wait_for_history_and_survive_a_restart(self):
        samples = deque()
        self.assertIsNone(request_rates(samples, 0.0, 100, 50)["per_minute"]["1"])
        self.assertIsNone(request_rates(samples, 8.0, 104, 58)["per_minute"]["1"])  # 8s: too little
        got = request_rates(samples, 120.0, 160, 170)
        self.assertEqual(got["per_minute"]["1"], 30.0)                  # 104 → 160 over the 112s since t=8
        self.assertLess(got["covered_secs"]["15"], 900)                  # 15-min figure is partial
        # rPocketBase restarted: the counter fell back. Start over, never go negative.
        got = request_rates(samples, 128.0, 3, 2)
        self.assertEqual(len(samples), 1)
        self.assertIsNone(got["per_minute"]["1"])

    def test_protocol_absent_still_draws_lanes(self):
        rules = {**BUILTIN_PROTOCOL, "from_file": False, "ready_means": {}}
        shaped = shape_protocol(rules, [], [], 404, 404)
        self.assertFalse(shaped["live"])
        self.assertEqual([lane["id"] for lane in shaped["lanes"]], ["android", "web", "linux", "ios"])
        self.assertEqual(shaped["lanes"][0]["rows"], [])
        self.assertEqual(shaped["builds_http"], 404)

    def test_protocol_rows_group_by_lane(self):
        rules = {**BUILTIN_PROTOCOL, "from_file": True, "ready_means": {}}
        rows = [
            {"lane": "ios", "status": "processing", "updated_at": "2026-10-18T15:00:00Z",
             "version": "1.1.4", "app_name": "TimeFlow"},
            {"lane": "ios", "status": "ready", "updated_at": "2026-10-17T15:00:00Z",
             "version": "1.1.3", "app_name": "TimeFlow"},
        ]
        shaped = shape_protocol(rules, rows, [], 200, 404)
        ios = next(lane for lane in shaped["lanes"] if lane["id"] == "ios")
        self.assertEqual([row["status"] for row in ios["rows"]], ["processing", "ready"])
        self.assertTrue(shaped["live"])
        self.assertEqual(shaped["plans_http"], 404)


if __name__ == "__main__":
    unittest.main()
