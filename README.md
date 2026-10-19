# Baton desk

A local page for the TimeFlow session relay, and for coordinator protocol v1 when that schema is on rPocketBase.

It reads the records API (`/api/collections/session_messages/records`), the same data as the admin screen at `http://192.168.1.141:8090/_/#/c/session_messages`. It does not scrape the admin HTML. The API key stays in this process. The browser only talks to loopback.

```bash
python3 server.py
```

Open **http://127.0.0.1:8734**. Ctrl-C stops it.

Configuration is the file `relay.py` already uses, `~/.config/rrl-testers/env`:

| Variable | Used for |
|---|---|
| `RPB_URL` | The records API. The checked-in default in that file is `https://192.168.1.141:8443`. |
| `RPB_CA_FILE` | dockge3's certificate. TLS is verified. |
| `RELAY_KEY` or `RPB_API_KEY` | `session_messages`. |
| `RELEASE_COORD_KEY`, `ROUND_COORD_KEY` | `build_requests` and `round_plans`, once `testers.py setup --coord-keys` has minted them. |

`RELAY_ME` marks which station is "this machine". On this PC that is Linux.

## What the page shows

- **Three stations.** A thread's baton is the `baton` on its latest note: who the work waits on. "Has the baton" is that queue. "Waiting on" is a thread this person took part in whose baton now sits with someone else.
- **The thread**, oldest note first, with the subject, the body, the issue link, and where the baton moved.
- **Protocol v1**, underneath. Android, Web, Linux, and iOS are drawn as tracks (`requested` through `ready`, plus `failed` and `blocked`). While those collections 404, the tracks stay empty on purpose. After the protocol branch is merged and imported, the same page fills them from the records API. No second deploy.

The page polls every 10 seconds. It also listens for rPocketBase realtime events on `session_messages` (`POST /api/realtime/ticket`, then the SSE stream) and refreshes when one arrives. A dropped stream does not stall the desk. The next poll still paints.

Notes can carry tester names, beta codes, and device rows. That is why this binds to `127.0.0.1` only, and why it is not a public site.

## Checks

```bash
python3 tests/test_board.py
```

`BATON_DESK_FIXTURE=1 python3 server.py` serves made-up threads and does not call rPocketBase. `BATON_DESK_PORT` changes the port.
