#!/usr/bin/env python3
"""Local page for the session relay and, when it lands, coordinator protocol v1.

    python3 server.py
    # http://127.0.0.1:8734

Binds to loopback only. The rPocketBase key never goes to the browser.
The page reads the records API (the same collection as the admin screen),
not the admin HTML.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import board
import ntfy

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = "127.0.0.1"
PORT = int(os.environ.get("BATON_DESK_PORT", "8734"))

_lock = threading.Lock()
_state = {"body": None, "etag": "", "error": ""}
_listeners = []


def _etag(payload):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def refresh():
    try:
        payload = board.build_board()
        err = ""
    except Exception as exc:  # noqa: BLE001 — surface a sentence, never a secret
        payload = None
        err = str(exc)
    if payload is not None:
        payload["ntfy"] = ntfy.public_status(ntfy.state_path())
    with _lock:
        changed = False
        if payload is None:
            if _state["error"] != err or _state["body"] is not None:
                changed = True
            _state["body"] = None
            _state["error"] = err
            _state["etag"] = ""
        else:
            tag = _etag(payload)
            changed = tag != _state["etag"]
            _state["body"] = payload
            _state["error"] = ""
            _state["etag"] = tag
        listeners = list(_listeners)
    if changed:
        for listener in listeners:
            try:
                listener.put_nowait(b"event: board\ndata: refresh\n\n")
            except Exception:
                pass
    if payload is not None:
        try:
            ntfy.publish(payload, ntfy.state_path())
        except Exception as err:  # noqa: BLE001 — a push must not stop the poll
            print(f"ntfy push failed: {err.__class__.__name__}", flush=True)
    return changed


def _poll():
    while True:
        try:
            refresh()
        except Exception:
            pass
        time.sleep(8)


def _watch_realtime():
    """Ask rPocketBase for a short-lived ticket and wake the poller on events.

    Polling remains the source of the page. A dropped SSE stream just means
    the next poll paints the change, which is how relay.py already behaves.
    """
    while True:
        try:
            _realtime_once()
        except Exception:
            time.sleep(20)


def _realtime_once():
    cfg = board.load_config()
    url = (cfg.get("RPB_URL") or "").rstrip("/")
    token = board.bearer(cfg.get("RELAY_KEY") or cfg.get("RPB_API_KEY") or "")
    if not url or not token:
        time.sleep(30)
        return
    ctx = board.ssl_context(url, cfg.get("RPB_CA_FILE") or "")
    body = json.dumps({"collections": "session_messages"}).encode()
    req = urllib.request.Request(
        url + "/api/realtime/ticket", data=body, method="POST",
        headers={"Authorization": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, context=ctx, timeout=20) as res:
        ticket = json.loads(res.read().decode()).get("ticket") or ""
    if not ticket:
        time.sleep(30)
        return
    stream = urllib.request.Request(
        url + "/api/realtime?ticket=" + urllib.parse.quote(ticket, safe="")
        + "&collections=session_messages",
    )
    # Tickets last about three minutes. Leave before that and open another.
    deadline = time.monotonic() + 150
    with urllib.request.urlopen(stream, context=ctx, timeout=160) as res:
        while time.monotonic() < deadline:
            line = res.readline()
            if not line:
                break
            if line.startswith(b"data:") and b"refresh" not in line and b"clientId" not in line:
                refresh()


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0 keeps the event stream open until the client leaves.
    # HTTP/1.1 would buffer it while it waited for a content length.
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt, *args):
        return

    def _page(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        with _lock:
            payload = _state["body"]
        if not payload:
            return html
        blob = json.dumps(payload).replace("<", "\\u003c")
        inject = f"<script>window.__BOARD__ = {blob};</script>\n"
        return html.replace("<!-- board -->", inject, 1)

    def _send(self, code, body, content_type, extra=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, self._page(), "text/html; charset=utf-8")
            return
        if path == "/app.css":
            self._send(200, (STATIC / "app.css").read_bytes(), "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._send(200, (STATIC / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        if path == "/api/board":
            with _lock:
                payload, err = _state["body"], _state["error"]
            if payload is None and not err:
                refresh()
                with _lock:
                    payload, err = _state["body"], _state["error"]
            if payload is None:
                self._send(502, json.dumps({"ok": False, "error": err}), "application/json")
                return
            self._send(200, json.dumps(payload), "application/json")
            return
        if path == "/api/events":
            self._stream()
            return
        self._send(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/api/ntfy":
            self._send(404, "Not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1000:
            self._send(400, json.dumps({"ok": False, "error": "Expected a small JSON body."}), "application/json")
            return
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            body = None
        station = body.get("station") if isinstance(body, dict) else None
        if (
            not isinstance(body, dict)
            or station not in board.PEOPLE
            or not isinstance(body.get("enabled"), bool)
        ):
            self._send(400, json.dumps({
                "ok": False,
                "error": "Expected {\"station\": \"linux|mac|ian\", \"enabled\": true or false}.",
            }), "application/json")
            return
        status = ntfy.set_enabled(ntfy.state_path(), station, body["enabled"])
        with _lock:
            if _state["body"] is not None:
                _state["body"]["ntfy"] = status
                _state["etag"] = _etag(_state["body"])
            listeners = list(_listeners)
        for listener in listeners:
            try:
                listener.put_nowait(b"event: board\ndata: refresh\n\n")
            except Exception:
                pass
        self._send(200, json.dumps({"ok": True, "ntfy": status}), "application/json")

    def _stream(self):
        q = queue.Queue()
        with _lock:
            _listeners.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b"event: board\ndata: hello\n\n")
            self.wfile.flush()
            while True:
                try:
                    chunk = q.get(timeout=15)
                except queue.Empty:
                    chunk = b": ping\n\n"
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _lock:
                if q in _listeners:
                    _listeners.remove(q)


def main():
    refresh()
    threading.Thread(target=_poll, name="poll", daemon=True).start()
    if os.environ.get("BATON_DESK_FIXTURE") != "1":
        threading.Thread(target=_watch_realtime, name="realtime", daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Baton desk  http://{HOST}:{PORT}", flush=True)
    print("Loopback only. Ctrl-C stops it.", flush=True)
    url, _token = ntfy.settings()
    if os.environ.get("BATON_DESK_FIXTURE") == "1":
        print("ntfy off (fixture)", flush=True)
    elif url:
        status = ntfy.public_status(ntfy.state_path())
        on = [name for name, enabled in status["stations"].items() if enabled]
        print(f"ntfy ready. On: {', '.join(on) or 'none'}", flush=True)
    else:
        print("ntfy off (BATON_DESK_NTFY_URL is not set)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)


if __name__ == "__main__":
    main()
