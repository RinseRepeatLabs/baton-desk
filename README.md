# Baton desk

RinseRepeatLabs builds TimeFlow with more than one agent at a time. A session on the Linux PC, a session on the kitchen Mac, and Ian each take a turn on their own machine. The thing they share is a baton. The notes of that handoff are records in rPocketBase, the lab's records server.

Baton desk is the page that makes the coordination visible. It shows which agent is working, which agents are waiting, and which station is holding everyone else up.

## What the baton is

A thread is one conversation. The `baton` on its latest note names who the work waits on: `linux`, `mac`, `ian`, or `none`.

That one field is the whole coordination.

- **Working.** The station whose name matches the baton holds the thread.
- **Waiting.** A station that took part, while the baton sits with someone else.
- **The hold-up.** The station other people are waiting on. The desk paints that station's color on the left edge only: Linux green, Mac blue, Ian gold. Every other station stays a plain panel.
- **Settled.** The baton is `none`. The thread is finished passing.

rPocketBase already stored the notes. The desk reads those records and draws the process, so the lab can see the development as a handoff across agents rather than three separate inboxes.

## What the page shows

### Stations

Linux, Mac, and Ian sit side by side. Each card lists the threads that station holds and the threads it is waiting on. The colored left edge appears only on the station that is causing the wait.

### The thread

Notes run oldest first. Subjects and bodies render as markdown. An issue number is a link: `#61` and `TimeFlow#61` open the TimeFlow issue, and `owner/repo#61` opens that repository. Clicking the number leaves the selected thread alone.

### The handoff

Beside the notes, a strip starts at now on the left. Each block is one hold, in that agent's color, labeled with how long they held it: seconds, minutes, hours, or days. Under the blocks, a line counts how long ago that moment was, and the value grows as you move right. Drag the slider to pan back through older holds. Each row under a block includes the note's title. Drag the handle between the notes and the strip to give either side more room. Each thread in the list shows how long ago its latest note was.

### Protocol v1

Under the threads, four lanes: Android, Web, Linux, and iOS. A build moves from `requested` through `ready`, with `failed` and `blocked` beside the main flow. The desk draws those lanes from `build_requests` and `round_plans` on the same rPocketBase server. While those collections are absent, the lanes stay empty. When the protocol records are imported, this same page fills them.

## How it uses rPocketBase

The desk calls the records API:

```
GET /api/collections/session_messages/records
```

That is the same data the admin UI shows. A small Python process on the machine reads it and serves the page.

- The API key stays in that process. The browser talks only to `127.0.0.1`.
- TLS is verified with the CA file named by `RPB_CA_FILE`. The desk refuses to skip certificate checks.
- A realtime ticket, then an SSE stream, wakes the page when a note arrives. The server also polls about every 8 seconds, and the page polls about every 10. If the stream drops, the next poll still paints.
- The first response includes the current board, so the page draws without a second round trip.

Notes can carry tester names, beta codes, and device rows. The server binds to loopback because of that. The source is public. A running desk stays on the machine that holds the key.

The page uses a compact dark theme, and a light theme when the system prefers light. It follows `prefers-color-scheme` and uses the system UI font.

## Run

Python 3 is enough. The server uses the standard library only.

```bash
python3 server.py
```

Open http://127.0.0.1:8734. Ctrl-C stops it.

Configuration is the env file the relay already uses, `~/.config/rrl-testers/env`. The same names work as environment variables and override the file.

| Variable | Used for |
|---|---|
| `RPB_URL` | Base URL of the records API. |
| `RPB_CA_FILE` | CA file for that URL. |
| `RELAY_KEY` or `RPB_API_KEY` | Read `session_messages`. |
| `RELEASE_COORD_KEY`, `ROUND_COORD_KEY` | Read `build_requests` and `round_plans`, once those keys exist. |
| `RELAY_ME` | Which station is this machine: `linux`, `mac`, or `ian`. |

`BATON_DESK_PORT` changes the port. The default is `8734`.

```bash
BATON_DESK_FIXTURE=1 python3 server.py
```

The fixture serves made-up threads and does not call rPocketBase. Use it to look at the layout.

## Checks

```bash
python3 tests/test_board.py
```
