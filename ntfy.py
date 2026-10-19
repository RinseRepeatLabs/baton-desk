"""Push a handoff onto one station out through ntfy.

The note body stays on this machine. The message is the thread, the
subject, and who handed it over. The first successful read is a baseline,
so a restart does not repeat work that is already sitting on the station.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

import board

DEFAULT_STATION = "ian"
_publish_lock = threading.Lock()


def _pick(cfg, *names):
    for name in names:
        value = (cfg.get(name) or "").strip()
        if value:
            return value
    return ""


def settings(cfg=None):
    """Environment overrides the relay env file. A passed cfg is used as-is."""
    if cfg is None:
        cfg = {
            **board.load_config(),
            **{key: value for key, value in os.environ.items() if value},
        }
    url = _pick(cfg, "BATON_DESK_NTFY_URL", "NTFY_URL")
    token = _pick(cfg, "BATON_DESK_NTFY_TOKEN", "NTFY_TOKEN")
    return url, token


def state_path():
    override = (os.environ.get("BATON_DESK_NTFY_STATE") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "baton-desk" / "ntfy.json"


def holds_of(payload, station):
    if not payload:
        return []
    for row in payload.get("stations") or []:
        if row.get("id") != station:
            continue
        holds = []
        for item in row.get("holding") or []:
            holds.append({
                "thread": item.get("thread") or "",
                "at": item.get("at") or "",
                "subject": item.get("subject") or "",
                "sender": item.get("sender") or "",
            })
        holds.sort(key=lambda item: (item["at"], item["thread"]))
        return holds
    return []


def hold_key(item):
    return (item.get("thread") or "", item.get("at") or "")


def arrivals(seen, holds):
    known = set(seen)
    return [item for item in holds if hold_key(item) not in known]


def _stations(data):
    """Per-station records. An older file stored one station at the top level."""
    raw = data.get("stations")
    if isinstance(raw, dict):
        return raw
    legacy = data.get("station")
    if not isinstance(legacy, str) or legacy not in board.PEOPLE:
        return {}
    record = {"holds": data.get("holds") or []}
    record["enabled"] = bool(data["enabled"]) if "enabled" in data else True
    return {legacy: record}


def _read(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _load(path):
    return _stations(_read(path))


def _store(path, stations):
    _write(path, {"stations": stations})


def load_seen(path, station):
    """Return the remembered keys, or None when this station has no baseline yet."""
    record = _load(path).get(station)
    if not isinstance(record, dict) or "holds" not in record:
        return None
    seen = set()
    for row in record.get("holds") or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            seen.add((str(row[0]), str(row[1])))
    return seen


def save_seen(path, station, holds):
    stations = _load(path)
    record = dict(stations.get(station) or {})
    record["holds"] = [[item["thread"], item["at"]] for item in holds]
    if "enabled" not in record:
        record["enabled"] = station == DEFAULT_STATION
    stations[station] = record
    _store(path, stations)


def is_enabled(path, station):
    """`ian` starts on. Every other station starts off until its badge is clicked."""
    record = _load(path).get(station) or {}
    if "enabled" not in record:
        return station == DEFAULT_STATION
    return bool(record["enabled"])


def set_enabled(path, station, enabled):
    if station not in board.PEOPLE:
        raise ValueError(station)
    stations = _load(path)
    record = dict(stations.get(station) or {})
    record["enabled"] = bool(enabled)
    stations[station] = record
    _store(path, stations)
    return public_status(path)


def public_status(path, cfg=None):
    """What the page may know. The topic URL and token stay here."""
    url, _token = settings(cfg)
    configured = bool(url)
    return {
        "configured": configured,
        "stations": {
            station: configured and is_enabled(path, station)
            for station in board.PEOPLE
        },
    }


def format_message(item, station):
    subject = (item.get("subject") or "").strip() or item.get("thread") or "A thread"
    sender = (item.get("sender") or "").strip() or "someone"
    title = f"Baton on {station}"
    body = f"{subject}\n{item.get('thread') or ''}\nfrom {sender}"
    return title, body


def push(url, token, title, message, opener=urllib.request.urlopen):
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Title": title,
        "Priority": "4",
        "Tags": "baton",
    }
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    request = urllib.request.Request(url, data=message.encode("utf-8"), method="POST", headers=headers)
    with opener(request, timeout=15) as response:
        response.read()


def publish(payload, path, opener=urllib.request.urlopen, cfg=None):
    """Notify on new holds. Return how many pushes were attempted.

    A missing URL, the fixture board, or a failed fetch does nothing.
    The first time a station is seen, the current holds are stored and
    no message is sent.
    """
    if not payload or payload.get("fixture") or payload.get("error"):
        return 0
    if os.environ.get("BATON_DESK_FIXTURE") == "1":
        return 0
    url, token = settings(cfg)
    if not url:
        return 0
    sent = 0
    with _publish_lock:
        for station in board.PEOPLE:
            sent += _publish_station(payload, path, url, token, station, opener)
    return sent


def _publish_station(payload, path, url, token, station, opener):
    holds = holds_of(payload, station)
    seen = load_seen(path, station)
    if seen is None:
        save_seen(path, station, holds)
        print(f"ntfy baseline: {len(holds)} hold(s) on {station}", flush=True)
        return 0
    if not is_enabled(path, station):
        save_seen(path, station, holds)
        return 0
    fresh = arrivals(seen, holds)
    sent = 0
    acked = set(seen)
    for item in fresh:
        title, message = format_message(item, station)
        try:
            push(url, token, title, message, opener=opener)
        except urllib.error.HTTPError as err:
            print(f"ntfy push failed: HTTP {err.code}", flush=True)
        except Exception as err:  # noqa: BLE001 — the page must keep polling
            print(f"ntfy push failed: {err.__class__.__name__}", flush=True)
        else:
            acked.add(hold_key(item))
            sent += 1
            print(f"ntfy sent: {item.get('thread') or '(thread)'} on {station}", flush=True)
    # A failed push stays out of the file, so the next read tries it again.
    save_seen(path, station, [item for item in holds if hold_key(item) in acked])
    return sent
