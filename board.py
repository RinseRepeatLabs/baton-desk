"""Shape the session relay and coordinator protocol v1 for the local board.

Reads rPocketBase's records API. The key stays in this process.
"""

from __future__ import annotations

import collections
import json
import os
import ssl
import threading
import time
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


def request_json(url, token, ca_file, path, query=None, method="GET", body=None):
    full = url.rstrip("/") + path
    if query:
        full += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(full, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
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


# ── rPocketBase's pulse, like top's load average ─────────────────────────────
# Each read times /api/health and, where the server reports it (rPocketBase with
# health metrics, for a caller with a key), keeps its running request count. The
# rates are that count's growth over the last 1, 5 and 15 minutes.

WINDOWS = (1, 5, 15)            # minutes
MIN_SPAN = 20                   # seconds of history before a rate means anything
_samples = collections.deque()  # (monotonic seconds, requestsTotal, uptimeSecs)
_samples_lock = threading.Lock()


def request_rates(samples, now, total, uptime=None):
    """Requests per minute over each window, from a running counter. A counter that went
    backwards means the server restarted: start again rather than report a negative."""
    if samples and (total < samples[-1][1] or (uptime is not None and samples[-1][2] is not None
                                               and uptime < samples[-1][2])):
        samples.clear()
    samples.append((now, total, uptime))
    # Keep one sample at least the longest window old, so that window can be whole.
    while len(samples) > 1 and now - samples[1][0] >= WINDOWS[-1] * 60:
        samples.popleft()
    rates, covered = {}, {}
    for minutes in WINDOWS:
        # Measure from the newest sample a full window old, over the span actually covered,
        # so gaps between reads don't blank the figure; with less history, from the oldest.
        older = [s for s in samples if now - s[0] >= minutes * 60]
        base = older[-1] if older else samples[0]
        span = now - base[0]
        if span < MIN_SPAN:
            rates[str(minutes)] = None
            covered[str(minutes)] = round(span)
            continue
        rates[str(minutes)] = round((total - base[1]) / span * 60, 1)
        covered[str(minutes)] = round(span)
    return {"per_minute": rates, "covered_secs": covered}


_names = {}


def host_name(host, cfg=None, ssh_config=None):
    """What to call the machine rPocketBase runs on: RPB_HOST_NAME if set, else reverse
    DNS, else the ~/.ssh/config alias whose HostName is this address (how this LAN names
    dockge3). "" when nothing knows, and the page shows the address alone."""
    cfg = cfg or {}
    if cfg.get("RPB_HOST_NAME"):
        return cfg["RPB_HOST_NAME"]
    if not host:
        return ""
    if host in _names:
        return _names[host]
    name = ""
    try:
        import ipaddress
        ipaddress.ip_address(host)
        try:
            import socket
            found = socket.gethostbyaddr(host)[0]
            name = found.split(".")[0] if found and found != host else ""
        except (OSError, UnicodeError):
            name = ""
        if not name:
            path = ssh_config or (Path.home() / ".ssh" / "config")
            aliases = []
            if path.is_file():
                for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    words = raw.split()
                    if len(words) >= 2 and words[0].lower() == "host":
                        aliases = [w for w in words[1:] if not any(ch in w for ch in "*?!")]
                    elif len(words) >= 2 and words[0].lower() == "hostname" and words[1] == host and aliases:
                        name = aliases[0]
                        break
    except ValueError:
        name = ""            # already a name, not an address
    _names[host] = name
    return name


def health(url, ca_file, token=""):
    """Up or down, the round trip, and request rates when the server reports a counter."""
    started = time.monotonic()
    try:
        try:
            got = request_json(url, token, ca_file, "/api/health")
        except ApiError as err:
            if not token or err.status not in (401, 403):
                raise
            got = request_json(url, "", ca_file, "/api/health")
    except ApiError as err:
        return {"ok": False, "error": err.message, "ping_ms": None}
    ping = round((time.monotonic() - started) * 1000)
    data = got.get("data") or {}
    out = {
        "ok": True,
        "name": data.get("displayName") or data.get("name") or "rPocketBase",
        "version": data.get("version") or "",
        "ping_ms": ping,
    }
    metrics = data.get("metrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("requestsTotal"), int):
        with _samples_lock:
            out["rates"] = request_rates(_samples, time.monotonic(), metrics["requestsTotal"],
                                         metrics.get("uptimeSecs"))
        out["errors_5xx"] = metrics.get("requests5xx")
        out["latency_avg_ms"] = metrics.get("latencyAvgMs")
        out["uptime_secs"] = metrics.get("uptimeSecs")
    return out


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
            # Notes addressed to this station that it hasn't read: what it still owes a look.
            "unread": sum(1 for thread in threads for note in thread["unread"]
                          if note["recipient"] == person),
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
        "health": {"ok": True, "name": "rPocketBase", "version": "fixture", "ping_ms": 4,
                   "rates": {"per_minute": {"1": 12.0, "5": 9.6, "15": 7.9},
                             "covered_secs": {"1": 60, "5": 300, "15": 900}},
                   "errors_5xx": 0, "latency_avg_ms": 3, "uptime_secs": 86400},
        "message_count": len(notes),
        "threads": threads,
        "stations": stations,
        "settled": settled,
        "protocol": shape_protocol(rules, [], [], 404, 404),
        "error": "",
    }


# ── Ian writing from the desk ───────────────────────────────────────────────
# The desk is Ian's: it reads and replies as `ian`, never as a session. The browser
# sends ids and text; everything that decides what gets written is checked here.

WRITER = "ian"
SUBJECT_MAX = 200
BODY_MAX = 20000


class DeskError(Exception):
    """A sentence for the page: a refused write, never a traceback or a secret."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _writer(cfg=None):
    if os.environ.get("BATON_DESK_FIXTURE") == "1":
        raise DeskError("The sample data is read-only.", 409)
    cfg = cfg if cfg is not None else load_config()
    url = (cfg.get("RPB_URL") or "").strip()
    token = (cfg.get("RELAY_KEY") or cfg.get("RPB_API_KEY") or "").strip()
    if not url or not token:
        raise DeskError("RPB_URL and RELAY_KEY (or RPB_API_KEY) are needed to write.", 503)
    return url, token, cfg.get("RPB_CA_FILE") or ""


def _now():
    t = datetime.now(timezone.utc)
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _filter_str(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mark_read(ids, cfg=None):
    """Mark Ian's notes read. A note to linux or mac stays unread: that is the session's
    inbox, and reading it here would hide it from the session that has to act on it."""
    if not isinstance(ids, list) or not ids or len(ids) > 200 or not all(
            isinstance(i, str) and i.isalnum() and len(i) <= 32 for i in ids):
        raise DeskError("Expected a list of note ids.")
    url, token, ca = _writer(cfg)
    done = []
    for note_id in dict.fromkeys(ids):
        path = f"/api/collections/{COLLECTION}/records/{note_id}"
        try:
            row = request_json(url, token, ca, path)
        except ApiError as err:
            raise DeskError(f"Couldn't read note {note_id}: {err.message}", 502) from err
        if row.get("recipient") != WRITER:
            raise DeskError(f"Note {note_id} is for {row.get('recipient') or 'nobody'}, not Ian, "
                            "so it stays unread for them.", 403)
        if row.get("read_at"):
            continue
        try:
            request_json(url, token, ca, path, method="PATCH", body={"read_at": _now()})
        except ApiError as err:
            raise DeskError(f"Couldn't mark {note_id} read: {err.message}", 502) from err
        done.append(note_id)
    return done


def reply(thread, subject, body="", baton="", cfg=None):
    """A note from Ian on an existing thread. It goes to whoever gets the baton, or back to
    whoever last wrote if the baton stays with Ian or is set down. The thread's issue rides
    along, so its #numbers keep their repo. Ian's unread notes on the thread are read now."""
    thread = (thread or "").strip() if isinstance(thread, str) else ""
    subject = (subject or "").strip() if isinstance(subject, str) else ""
    body = (body or "").strip() if isinstance(body, str) else ""
    if not thread:
        raise DeskError("Which thread?")
    if not subject:
        raise DeskError("A reply needs a subject line.")
    if len(subject) > SUBJECT_MAX or len(body) > BODY_MAX:
        raise DeskError(f"Keep the subject under {SUBJECT_MAX} characters and the note under {BODY_MAX}.")
    if baton not in PEOPLE + ("none",):
        raise DeskError("The baton goes to linux, mac, ian or none.")
    url, token, ca = _writer(cfg)
    try:
        notes = request_json(url, token, ca, f"/api/collections/{COLLECTION}/records",
                             {"filter": f"thread = {_filter_str(thread)}", "sort": "at", "perPage": 200})
    except ApiError as err:
        raise DeskError(f"Couldn't read {thread}: {err.message}", 502) from err
    notes = notes.get("items") or []
    if not notes:
        raise DeskError(f"There's no thread called {thread}.", 404)
    others = [n.get("sender") for n in reversed(notes) if n.get("sender") in PEOPLE and n.get("sender") != WRITER]
    recipient = baton if baton in ("linux", "mac") else (others[0] if others else "linux")
    issue = next((n.get("issue") for n in reversed(notes) if n.get("issue")), "")
    try:
        row = request_json(url, token, ca, f"/api/collections/{COLLECTION}/records", method="POST", body={
            "thread": thread, "sender": WRITER, "recipient": recipient, "at": _now(),
            "subject": subject, "body": body, "baton": baton, "issue": issue,
        })
    except ApiError as err:
        raise DeskError(f"The reply wasn't sent: {err.message}", 502) from err
    unread = [n["id"] for n in notes if n.get("recipient") == WRITER and not n.get("read_at")]
    if unread:
        mark_read(unread, cfg)
    return {"id": row.get("id"), "recipient": recipient, "baton": baton}


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
    info = health(url, ca, token)
    return {
        "ok": True,
        "fixture": False,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "me": this_machine(cfg),
        "host": host,
        "host_name": host_name(host, cfg),
        "health": info,
        "message_count": len(records),
        "threads": threads,
        "stations": stations,
        "settled": settled,
        "protocol": shape_protocol(rules, builds, plans, builds_status, plans_status),
        "error": "",
    }
