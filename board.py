"""Shape the session relay and coordinator protocol v1 for the local board.

Reads rPocketBase's records API. The key stays in this process.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path.home() / ".config" / "rrl-testers" / "env"
PROTOCOL_FILE = Path.home() / "Development" / "tester-pool" / "schema" / "coordinator-protocol-v1.json"
COLLECTION = "session_messages"
PEOPLE = ("linux", "mac", "ian")

# Used only when the protocol file is not on this machine. The live page
# prefers the file so the tracks stay in step with coordinator-protocol-v1.
BUILTIN_PROTOCOL = {
    "protocol_version": "1",
    "lanes": {"android": "linux", "web": "linux", "linux": "linux", "ios": "mac"},
    "flow": ["requested", "accepted", "building", "uploaded", "processing", "ready"],
    "side": ["failed", "blocked"],
    "plans": ["idle", "waiting", "drafted", "needs_review", "open", "closed"],
}


def load_config():
    cfg = {}
    if CONFIG.exists():
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                cfg[key.strip()] = value.strip().strip('"').strip("'")
    cfg.update({
        k: v for k, v in os.environ.items()
        if k.startswith(("RPB_", "RELAY_", "RELEASE_COORD_", "ROUND_COORD_")) and v
    })
    return cfg


def this_machine(cfg):
    if cfg.get("RELAY_ME") in PEOPLE:
        return cfg["RELAY_ME"]
    return "linux"


def bearer(token):
    token = (token or "").strip()
    if not token:
        return ""
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def ssl_context(url, ca_file):
    if not url.startswith("https://"):
        return None
    raw = os.path.expanduser(ca_file or "")
    path = Path(raw)
    if not path.is_file():
        raise RuntimeError(
            "RPB_CA_FILE is missing, so the board will not skip certificate checks. "
            "The path is the one relay.py uses."
        )
    return ssl.create_default_context(cafile=str(path))


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def request_json(url, token, ca_file, path, query=None):
    full = url.rstrip("/") + path
    if query:
        full += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(full, method="GET")
    if token:
        req.add_header("Authorization", bearer(token))
    try:
        with urllib.request.urlopen(req, context=ssl_context(url, ca_file), timeout=25) as res:
            raw = res.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        try:
            message = json.loads(detail).get("message") or detail
        except json.JSONDecodeError:
            message = detail or err.reason
        raise ApiError(err.code, str(message)) from err
    except urllib.error.URLError as err:
        raise ApiError(0, f"Can't reach rPocketBase: {err.reason}") from err


def list_records(url, token, ca_file, collection, sort="-created"):
    items = []
    page = 1
    while True:
        got = request_json(
            url, token, ca_file,
            f"/api/collections/{collection}/records",
            {"page": page, "perPage": 200, "sort": sort},
        )
        items.extend(got.get("items") or [])
        if page >= got.get("totalPages", 1) or not got.get("items"):
            return items
        page += 1


def health(url, ca_file):
    try:
        got = request_json(url, "", ca_file, "/api/health")
    except ApiError:
        return {}
    data = got.get("data") or {}
    return {
        "name": data.get("displayName") or data.get("name") or "rPocketBase",
        "version": data.get("version") or "",
    }


def load_protocol_rules():
    if PROTOCOL_FILE.is_file():
        rules = json.loads(PROTOCOL_FILE.read_text(encoding="utf-8"))
        br = rules["collections"]["build_requests"]
        plans = rules["collections"]["round_plans"]
        return {
            "protocol_version": str(rules["protocol_version"]),
            "lanes": rules["lanes"],
            "flow": ["requested", "accepted", "building", "uploaded", "processing", "ready"],
            "side": ["failed", "blocked"],
            "plans": plans["fields"]["status"]["values"],
            "plan_flow_note": "open and closed are set by a person",
            "ready_means": br.get("ready_means") or {},
            "file": str(PROTOCOL_FILE),
            "from_file": True,
        }
    return {**BUILTIN_PROTOCOL, "file": "", "from_file": False, "ready_means": {}, "plan_flow_note": ""}


def _iso(value):
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    return text


def _people_in(messages):
    found = []
    for message in messages:
        for key in ("sender", "recipient"):
            name = message.get(key) or ""
            if name and name not in found:
                found.append(name)
    return found


def shape_messages(records):
    buckets = {}
    for record in records:
        name = (record.get("thread") or "").strip() or "(no thread)"
        buckets.setdefault(name, []).append(record)
    threads = []
    for name, messages in buckets.items():
        messages.sort(key=lambda row: row.get("at") or "")
        last = messages[-1]
        unread = [
            {"id": row.get("id"), "recipient": row.get("recipient") or ""}
            for row in messages if not row.get("read_at")
        ]
        issues = []
        for row in messages:
            issue = (row.get("issue") or "").strip()
            if issue and issue not in issues:
                issues.append(issue)
        threads.append({
            "name": name,
            "baton": last.get("baton") or "none",
            "subject": last.get("subject") or "",
            "at": _iso(last.get("at")),
            "sender": last.get("sender") or "",
            "recipient": last.get("recipient") or "",
            "count": len(messages),
            "unread": unread,
            "people": _people_in(messages),
            "issues": issues,
            "messages": [
                {
                    "id": row.get("id"),
                    "at": _iso(row.get("at")),
                    "sender": row.get("sender") or "",
                    "recipient": row.get("recipient") or "",
                    "subject": row.get("subject") or "",
                    "body": row.get("body") or "",
                    "baton": row.get("baton") or "none",
                    "issue": (row.get("issue") or "").strip(),
                    "read": bool(row.get("read_at")),
                }
                for row in messages
            ],
        })
    threads.sort(key=lambda row: row["at"], reverse=True)
    return threads


def stations_from(threads):
    stations = []
    for person in PEOPLE:
        holding = []
        waiting = []
        for thread in threads:
            brief = {
                "thread": thread["name"],
                "subject": thread["subject"],
                "at": thread["at"],
                "unread": len(thread["unread"]),
                "sender": thread["sender"],
                "issue": thread["issues"][-1] if thread["issues"] else "",
            }
            involved = person in thread["people"] or thread["baton"] == person
            if thread["baton"] == person:
                holding.append(brief)
            elif involved and thread["baton"] not in (person, "none", ""):
                waiting.append({**brief, "on": thread["baton"]})
        stations.append({
            "id": person,
            "holding": holding,
            "waiting": waiting,
        })
    settled = [
        {"thread": thread["name"], "subject": thread["subject"], "at": thread["at"]}
        for thread in threads if thread["baton"] in ("none", "")
    ]
    return stations, settled


def _public_build(row):
    return {
        "id": row.get("id"),
        "protocol_version": str(row.get("protocol_version") or ""),
        "app_name": row.get("app_name") or "",
        "version": row.get("version") or "",
        "git_sha": row.get("git_sha") or "",
        "git_ref": row.get("git_ref") or "",
        "lane": row.get("lane") or "",
        "owner": row.get("owner") or "",
        "baton": row.get("baton") or "",
        "status": row.get("status") or "",
        "blocked_from": row.get("blocked_from") or "",
        "store_build": row.get("store_build") or "",
        "artifact": row.get("artifact") or "",
        "beta": bool(row.get("beta")),
        "issue": row.get("issue") or "",
        "thread": row.get("thread") or "",
        "detail": row.get("detail") or "",
        "expires_at": _iso(row.get("expires_at")),
        "requested_at": _iso(row.get("requested_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _public_plan(row):
    return {
        "id": row.get("id"),
        "protocol_version": str(row.get("protocol_version") or ""),
        "name": row.get("name") or "",
        "app_name": row.get("app_name") or "",
        "lane": row.get("lane") or "",
        "distribution": row.get("distribution") or "",
        "seed_path": row.get("seed_path") or "",
        "beta_only": bool(row.get("beta_only")),
        "auto_draft": bool(row.get("auto_draft")),
        "allow_open_update": bool(row.get("allow_open_update")),
        "status": row.get("status") or "",
        "last_version": row.get("last_version") or "",
        "last_store_build": row.get("last_store_build") or "",
        "detail": row.get("detail") or "",
    }


def shape_protocol(rules, builds, plans, builds_status, plans_status):
    lanes = []
    for lane, owner in rules["lanes"].items():
        rows = [row for row in builds if row.get("lane") == lane]
        rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
        lanes.append({"id": lane, "owner": owner, "rows": rows})
    known = set(rules["lanes"])
    other = [row for row in builds if row.get("lane") not in known]
    return {
        "version": rules["protocol_version"],
        "from_file": rules.get("from_file", False),
        "flow": rules["flow"],
        "side": rules["side"],
        "plan_statuses": rules["plans"],
        "ready_means": rules.get("ready_means") or {},
        "lanes": lanes,
        "other": other,
        "plans": plans,
        "builds_http": builds_status,
        "plans_http": plans_status,
        "live": builds_status == 200 or plans_status == 200,
    }


def _try_collection(url, tokens, ca_file, name):
    """Try each token. 404 means the collection is not imported yet."""
    last = ApiError(0, "no key")
    for token in tokens:
        if not token:
            continue
        try:
            rows = list_records(url, token, ca_file, name, sort="-updated")
            return 200, [_coerce(name, row) for row in rows], ""
        except ApiError as err:
            last = err
            if err.status == 404:
                return 404, [], err.message
            if err.status in (401, 403):
                continue
            return err.status or 0, [], err.message
    if last.status in (401, 403):
        return last.status, [], "The key on this machine cannot read this collection yet."
    return last.status or 0, [], last.message


def _coerce(name, row):
    if name == "build_requests":
        return _public_build(row)
    return _public_plan(row)


def fixture_board():
    """Sample threads with no real relay text, for layout checks.

    lane-pass spans seconds, minutes, hours, and days so the handoff
    strip has something to measure. The names and bodies are invented.
    """
    now = "2026-10-18T15:04:00.000Z"
    earlier = "2026-10-18T14:40:00.000Z"
    notes = [
        {
            "id": "a1", "thread": "release-1.1.4", "sender": "linux", "recipient": "mac",
            "at": earlier, "subject": "1.1.4 is on main, iOS lane is yours",
            "body": "Tag is cut. Android artifact is on the GitHub release. Play upload was skipped.",
            "baton": "mac", "issue": "TimeFlow#70", "read_at": earlier,
        },
        {
            "id": "a2", "thread": "release-1.1.4", "sender": "mac", "recipient": "linux",
            "at": now, "subject": "TestFlight upload returned, still processing",
            "body": "Uploaded build 251. App Store Connect has not finished processing.",
            "baton": "mac", "issue": "TimeFlow#70", "read_at": None,
        },
        {
            "id": "b1", "thread": "timeflow-68", "sender": "mac", "recipient": "ian",
            "at": "2026-10-17T18:12:00.000Z", "subject": "Need a look at the lock screen (#68)",
            "body": "Completed from the lock screen still needs a real phone.",
            "baton": "ian", "issue": "TimeFlow#68", "read_at": "2026-10-17T18:20:00.000Z",
        },
        {
            "id": "c1", "thread": "lane-pass", "sender": "linux", "recipient": "mac",
            "at": "2026-10-15T08:00:00.000Z", "subject": "Android lane is cut (#70)",
            "body": "The tag is on main. See #70.\n\n1. **Android** artifact is on the release.\n2. Play upload was skipped.\n\nHolding the baton while it uploads. `lane` stays put.",
            "baton": "linux", "issue": "TimeFlow#70", "read_at": "2026-10-15T08:05:00.000Z",
        },
        {
            "id": "c2", "thread": "lane-pass", "sender": "linux", "recipient": "mac",
            "at": "2026-10-17T08:00:00.000Z", "subject": "iOS upload started",
            "body": "Android is done. The kitchen Mac has the lane.",
            "baton": "mac", "issue": "TimeFlow#70", "read_at": "2026-10-17T08:00:20.000Z",
        },
        {
            "id": "c3", "thread": "lane-pass", "sender": "mac", "recipient": "ian",
            "at": "2026-10-17T08:00:25.000Z", "subject": "Need a build number",
            "body": "Upload accepted. The store build number is the open question.",
            "baton": "ian", "issue": "TimeFlow#70", "read_at": "2026-10-17T08:02:00.000Z",
        },
        {
            "id": "c4", "thread": "lane-pass", "sender": "ian", "recipient": "linux",
            "at": "2026-10-17T08:12:25.000Z", "subject": "Build number is 251",
            "body": "Use 251 on the next upload.",
            "baton": "linux", "issue": "TimeFlow#70", "read_at": "2026-10-17T08:13:00.000Z",
        },
        {
            "id": "c5", "thread": "lane-pass", "sender": "linux", "recipient": "mac",
            "at": "2026-10-18T04:12:25.000Z", "subject": "Lane is closed",
            "body": "Both stores have the build. Setting the baton down.",
            "baton": "none", "issue": "TimeFlow#70", "read_at": "2026-10-18T04:20:00.000Z",
        },
        {
            "id": "c6", "thread": "lane-pass", "sender": "linux", "recipient": "mac",
            "at": "2026-10-18T21:30:00.000Z", "subject": "No further handoff",
            "body": "Still set down. Nothing new to pass.",
            "baton": "none", "issue": "", "read_at": "2026-10-18T21:40:00.000Z",
        },
    ]
    threads = shape_messages(notes)
    stations, settled = stations_from(threads)
    rules = load_protocol_rules()
    return {
        "ok": True,
        "fixture": True,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "me": "linux",
        "host": "fixture",
        "health": {"name": "rPocketBase", "version": "fixture"},
        "message_count": len(notes),
        "threads": threads,
        "stations": stations,
        "settled": settled,
        "protocol": shape_protocol(rules, [], [], 404, 404),
        "error": "",
    }


def build_board(cfg=None, fixture=False):
    if fixture or os.environ.get("BATON_DESK_FIXTURE") == "1":
        return fixture_board()
    cfg = cfg if cfg is not None else load_config()
    url = (cfg.get("RPB_URL") or "").strip()
    if not url:
        raise RuntimeError("RPB_URL is not set. relay.py reads it from ~/.config/rrl-testers/env.")
    token = (cfg.get("RELAY_KEY") or cfg.get("RPB_API_KEY") or "").strip()
    if not token:
        raise RuntimeError("RELAY_KEY or RPB_API_KEY is required to read session_messages.")
    ca = cfg.get("RPB_CA_FILE") or ""
    records = list_records(url, token, ca, COLLECTION, sort="at")
    threads = shape_messages(records)
    stations, settled = stations_from(threads)
    rules = load_protocol_rules()
    coord_tokens = [
        cfg.get("RELEASE_COORD_KEY") or "",
        cfg.get("ROUND_COORD_KEY") or "",
        cfg.get("RPB_API_KEY") or "",
        token,
    ]
    builds_status, builds, _builds_err = _try_collection(url, coord_tokens, ca, "build_requests")
    plans_status, plans, _plans_err = _try_collection(url, coord_tokens, ca, "round_plans")
    host = urllib.parse.urlparse(url).hostname or url
    info = health(url, ca)
    return {
        "ok": True,
        "fixture": False,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "me": this_machine(cfg),
        "host": host,
        "health": info,
        "message_count": len(records),
        "threads": threads,
        "stations": stations,
        "settled": settled,
        "protocol": shape_protocol(rules, builds, plans, builds_status, plans_status),
        "error": "",
    }
