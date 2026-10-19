import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ntfy


def payload(holding):
    return {
        "stations": [{
            "id": "ian",
            "holding": holding,
            "waiting": [],
        }],
    }


class NtfyTests(unittest.TestCase):
    def test_arrivals_are_new_thread_or_new_note(self):
        seen = {("lane", "2026-10-18T12:00:00Z")}
        holds = [
            {"thread": "lane", "at": "2026-10-18T12:00:00Z", "subject": "old", "sender": "mac"},
            {"thread": "lane", "at": "2026-10-18T13:00:00Z", "subject": "again", "sender": "mac"},
            {"thread": "other", "at": "2026-10-18T13:00:00Z", "subject": "new", "sender": "linux"},
        ]
        fresh = ntfy.arrivals(seen, holds)
        self.assertEqual([item["thread"] for item in fresh], ["lane", "other"])
        self.assertEqual(fresh[0]["at"], "2026-10-18T13:00:00Z")

    def test_message_uses_the_subject_and_leaves_the_body_out(self):
        title, message = ntfy.format_message({
            "thread": "release-1",
            "subject": "Need a look",
            "sender": "mac",
            "body": "beta code and a device row",
        }, "ian")
        self.assertEqual(title, "Baton on ian")
        self.assertIn("Need a look", message)
        self.assertIn("release-1", message)
        self.assertIn("from mac", message)
        self.assertNotIn("beta code", message)

    def test_first_read_is_a_baseline_and_the_next_hold_posts(self):
        path = Path(self.id().replace(".", "-") + ".json")
        path = Path("/tmp") / path.name
        path.unlink(missing_ok=True)
        posted = []

        def opener(request, timeout=0):
            posted.append((request.full_url, request.headers, request.data, timeout))

            class Response:
                def read(self):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        cfg = {"BATON_DESK_NTFY_URL": "https://ntfy.example/topic", "BATON_DESK_NTFY_TOKEN": "secret"}
        first = payload([{"thread": "old", "at": "t1", "subject": "sitting", "sender": "linux"}])
        self.assertEqual(ntfy.publish(first, path, opener=opener, cfg=cfg), 0)
        self.assertEqual(posted, [])
        self.assertEqual(ntfy.load_seen(path, "ian"), {("old", "t1")})

        second = payload([
            {"thread": "old", "at": "t1", "subject": "sitting", "sender": "linux"},
            {"thread": "new", "at": "t2", "subject": "over to you", "sender": "mac",
             "body": "do not send this"},
        ])
        self.assertEqual(ntfy.publish(second, path, opener=opener, cfg=cfg), 1)
        url, headers, data, timeout = posted[0]
        self.assertEqual(url, "https://ntfy.example/topic")
        self.assertEqual(headers["Title"], "Baton on ian")
        self.assertEqual(headers["Priority"], "4")
        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(timeout, 15)
        text = data.decode()
        self.assertIn("over to you", text)
        self.assertIn("new", text)
        self.assertNotIn("do not send this", text)
        self.assertNotIn("sitting", text)
        path.unlink(missing_ok=True)

    def test_a_failed_push_is_tried_again(self):
        path = Path("/tmp/baton-desk-ntfy-retry.json")
        path.unlink(missing_ok=True)
        calls = {"n": 0}

        def opener(request, timeout=0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("down")

            class Response:
                def read(self):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        cfg = {"BATON_DESK_NTFY_URL": "https://ntfy.example/topic"}
        board = payload([{"thread": "new", "at": "t2", "subject": "over", "sender": "mac"}])
        ntfy.publish(payload([]), path, opener=opener, cfg=cfg)
        self.assertEqual(ntfy.publish(board, path, opener=opener, cfg=cfg), 0)
        self.assertEqual(ntfy.load_seen(path, "ian"), set())
        self.assertEqual(ntfy.publish(board, path, opener=opener, cfg=cfg), 1)
        self.assertEqual(ntfy.load_seen(path, "ian"), {("new", "t2")})
        path.unlink(missing_ok=True)

    def test_off_remembers_holds_and_does_not_push(self):
        path = Path("/tmp/baton-desk-ntfy-off.json")
        path.unlink(missing_ok=True)
        posted = []
        cfg = {"BATON_DESK_NTFY_URL": "https://ntfy.example/topic"}
        ntfy.publish(payload([{"thread": "old", "at": "t1", "subject": "sitting", "sender": "linux"}]), path, opener=posted.append, cfg=cfg)
        ntfy.set_enabled(path, "ian", False)
        status = ntfy.public_status(path, cfg)
        self.assertEqual(status["stations"]["ian"], False)
        self.assertEqual(status["stations"]["linux"], False)
        self.assertTrue(status["configured"])
        self.assertNotIn("url", status)
        ntfy.publish(payload([
            {"thread": "old", "at": "t1", "subject": "sitting", "sender": "linux"},
            {"thread": "new", "at": "t2", "subject": "over", "sender": "mac"},
        ]), path, opener=posted.append, cfg=cfg)
        self.assertEqual(posted, [])
        self.assertEqual(ntfy.load_seen(path, "ian"), {("old", "t1"), ("new", "t2")})
        ntfy.set_enabled(path, "ian", True)
        self.assertTrue(ntfy.is_enabled(path, "ian"))
        again = payload([
            {"thread": "old", "at": "t1", "subject": "sitting", "sender": "linux"},
            {"thread": "new", "at": "t2", "subject": "over", "sender": "mac"},
        ])
        self.assertEqual(ntfy.publish(again, path, opener=posted.append, cfg=cfg), 0)
        self.assertEqual(posted, [])
        path.unlink(missing_ok=True)

    def test_linux_can_be_turned_on_without_waking_ian(self):
        path = Path("/tmp/baton-desk-ntfy-linux.json")
        path.unlink(missing_ok=True)
        posted = []

        def opener(request, timeout=0):
            posted.append(request.headers["Title"])

            class Response:
                def read(self):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        cfg = {"BATON_DESK_NTFY_URL": "https://ntfy.example/topic"}
        board = {
            "stations": [
                {"id": "linux", "holding": [{"thread": "a", "at": "t1", "subject": "pc", "sender": "mac"}]},
                {"id": "mac", "holding": []},
                {"id": "ian", "holding": [{"thread": "b", "at": "t1", "subject": "yours", "sender": "linux"}]},
            ],
        }
        self.assertEqual(ntfy.publish(board, path, opener=opener, cfg=cfg), 0)
        ntfy.set_enabled(path, "linux", True)
        self.assertEqual(ntfy.publish(board, path, opener=opener, cfg=cfg), 0)
        board["stations"][0]["holding"] = [
            {"thread": "a", "at": "t2", "subject": "again", "sender": "mac"},
        ]
        self.assertEqual(ntfy.publish(board, path, opener=opener, cfg=cfg), 1)
        self.assertEqual(posted, ["Baton on linux"])
        path.unlink(missing_ok=True)

    def test_legacy_ian_file_is_kept(self):
        path = Path("/tmp/baton-desk-ntfy-legacy.json")
        path.write_text('{"station": "ian", "holds": [["old", "t1"]]}\n', encoding="utf-8")
        self.assertEqual(ntfy.load_seen(path, "ian"), {("old", "t1")})
        self.assertTrue(ntfy.is_enabled(path, "ian"))
        self.assertFalse(ntfy.is_enabled(path, "mac"))
        path.unlink(missing_ok=True)

    def test_fixture_and_missing_url_stay_quiet(self):
        path = Path("/tmp/baton-desk-ntfy-quiet.json")
        path.unlink(missing_ok=True)
        called = []
        board = payload([{"thread": "x", "at": "t", "subject": "s", "sender": "mac"}])
        self.assertEqual(ntfy.publish({**board, "fixture": True}, path, opener=called.append, cfg={"BATON_DESK_NTFY_URL": "https://ntfy.example/t"}), 0)
        self.assertEqual(ntfy.publish(board, path, opener=called.append, cfg={}), 0)
        self.assertEqual(called, [])
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
