const WHO = {
  linux: { name: "Linux", place: "The PC" },
  mac: { name: "Mac", place: "Kitchen mini" },
  ian: { name: "Ian", place: "You" },
  none: { name: "Settled", place: "Nobody is waiting" },
};

const state = {
  board: null,
  filter: "all",
  query: "",
  selected: "",
  drafts: {},        // thread -> { subject, body, baton } Ian hasn't sent yet
  reply: null,       // the one reply box, kept across redraws so typing survives them
  busy: false,
  keep: "",          // the thread Ian just read or answered: stays in the Unread view until he moves on
  paused: false,     // Ian stopped the automatic refresh; his own clicks still update
  missed: false,     // something changed while paused
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function clear(node) {
  node.replaceChildren();
}

function ago(iso) {
  const then = Date.parse(iso);
  if (!then) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  const abs = Math.abs(seconds);
  const unit = abs < 60 ? [abs, "second"]
    : abs < 3600 ? [Math.round(abs / 60), "minute"]
    : abs < 86400 ? [Math.round(abs / 3600), "hour"]
    : [Math.round(abs / 86400), "day"];
  const word = unit[0] === 1 ? unit[1] : unit[1] + "s";
  return seconds >= 0 ? `${unit[0]} ${word} ago` : `in ${unit[0]} ${word}`;
}

function when(iso, withSeconds) {
  const then = typeof iso === "number" ? iso : Date.parse(iso);
  if (!then) return "";
  const opts = {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  };
  if (withSeconds) opts.second = "2-digit";
  return new Date(then).toLocaleString(undefined, opts);
}

function holdClock(hold) {
  const precise = hold.end - hold.start < 3600000;
  const start = when(hold.start, precise);
  const end = hold.open ? "now" : when(hold.end, precise);
  return { start, range: `${start} to ${end}` };
}

function span(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 1) return "under a second";
  const unit = seconds < 60 ? [seconds, "second"]
    : seconds < 3600 ? [Math.round(seconds / 60), "minute"]
    : seconds < 86400 ? [Math.round(seconds / 3600), "hour"]
    : [Math.round(seconds / 86400), "day"];
  const word = unit[0] === 1 ? unit[1] : unit[1] + "s";
  return `${unit[0]} ${word}`;
}

function holdsOf(messages) {
  const holds = [];
  messages.forEach((note, index) => {
    const start = Date.parse(note.at);
    if (!start) return;
    const baton = note.baton || "none";
    const last = holds[holds.length - 1];
    if (last && last.baton === baton) {
      last.count += 1;
      return;
    }
    holds.push({
      baton,
      start,
      index,
      count: 1,
      subject: (note.subject || "").trim(),
      repo: repoOf(note.issue),
    });
  });
  const now = Date.now();
  for (let i = 0; i < holds.length; i++) {
    const next = holds[i + 1];
    holds[i].end = next ? next.start : Math.max(now, holds[i].start);
    holds[i].open = !next;
  }
  return holds;
}

function holdPhrase(hold) {
  const dur = span(hold.end - hold.start);
  if (hold.baton === "none") {
    return hold.open ? `Nobody has held it for ${dur}` : `Nobody held it for ${dur}`;
  }
  const name = personName(hold.baton);
  return hold.open ? `${name} has held it ${dur}` : `${name} held it ${dur}`;
}

function renderMarkdown(text, repo) {
  const root = el("div", "body");
  const lines = String(text).replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  const listMarker = (line) => {
    const ordered = /^(\d+)\.\s+(.*)$/.exec(line);
    if (ordered) return { kind: "ol", start: Number(ordered[1]), content: ordered[2] };
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    if (bullet) return { kind: "ul", content: bullet[1] };
    return null;
  };
  while (i < lines.length) {
    if (!lines[i].trim()) { i++; continue; }
    if (lines[i].startsWith("```")) {
      const fence = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) fence.push(lines[i++]);
      if (i < lines.length) i++;
      const pre = el("pre");
      const code = el("code");
      appendIssueText(code, fence.join("\n"), repo);
      pre.append(code);
      root.append(pre);
      continue;
    }
    const marker = listMarker(lines[i]);
    if (marker) {
      const list = el(marker.kind);
      if (marker.kind === "ol" && marker.start !== 1) list.start = marker.start;
      while (i < lines.length) {
        const item = listMarker(lines[i]);
        if (!item || item.kind !== marker.kind) break;
        let content = item.content;
        i++;
        while (i < lines.length && /^[ \t]{2,}\S/.test(lines[i])) {
          content += "\n" + lines[i].trim();
          i++;
        }
        const li = el("li");
        appendInline(li, content, repo);
        list.append(li);
      }
      root.append(list);
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !listMarker(lines[i]) && !lines[i].startsWith("```")) {
      para.push(lines[i]);
      i++;
    }
    const p = el("p");
    appendInline(p, para.join("\n"), repo);
    root.append(p);
  }
  return root;
}

function appendInline(parent, text, repo) {
  let i = 0;
  while (i < text.length) {
    if (text.startsWith("**", i) || text.startsWith("__", i)) {
      const marker = text.slice(i, i + 2);
      const end = text.indexOf(marker, i + 2);
      if (end > i + 2) {
        const strong = el("strong");
        appendInline(strong, text.slice(i + 2, end), repo);
        parent.append(strong);
        i = end + 2;
        continue;
      }
    }
    if (text[i] === "`") {
      const end = text.indexOf("`", i + 1);
      if (end > i) {
        const code = el("code");
        appendIssueText(code, text.slice(i + 1, end), repo);
        parent.append(code);
        i = end + 1;
        continue;
      }
    }
    if (text[i] === "[") {
      const link = /^\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/.exec(text.slice(i));
      if (link) {
        const anchor = el("a", "", link[1]);
        anchor.href = link[2];
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        parent.append(anchor);
        i += link[0].length;
        continue;
      }
    }
    if ((text[i] === "*" && text[i + 1] !== "*") || (text[i] === "_" && text[i + 1] !== "_" && edgeBefore(text, i))) {
      const end = closeSpan(text, i, text[i]);
      if (end > i + 1 && (text[i] !== "_" || edgeAfter(text, end))) {
        const em = el("em");
        appendInline(em, text.slice(i + 1, end), repo);
        parent.append(em);
        i = end + 1;
        continue;
      }
    }
    if (text[i] === "\n") {
      parent.append(el("br"));
      i++;
      continue;
    }
    let j = i + 1;
    while (j < text.length && !inlineToken(text, j)) j++;
    appendIssueText(parent, text.slice(i, j), repo);
    i = j;
  }
}

function closeSpan(text, open, mark) {
  if (open + 1 >= text.length || /\s/.test(text[open + 1])) return -1;
  for (let k = open + 1; k < text.length; k++) {
    if (text[k] !== mark) continue;
    if (text[k + 1] === mark) { k++; continue; }
    if (k === open + 1 || /\s/.test(text[k - 1])) continue;
    return k;
  }
  return -1;
}

function edgeBefore(text, index) {
  return index === 0 || !/\w/.test(text[index - 1]);
}

function edgeAfter(text, index) {
  return index + 1 >= text.length || !/\w/.test(text[index + 1]);
}

function inlineToken(text, index) {
  const ch = text[index];
  return ch === "\n" || ch === "`" || ch === "[" || ch === "*" || ch === "_";
}

function showNote(index, block = "nearest") {
  const scroller = $("transcript");
  if (!scroller) return;
  for (const node of scroller.querySelectorAll(".note.here")) node.classList.remove("here");
  const note = document.getElementById("note-" + index);
  if (!note) return;
  note.classList.add("here");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  note.scrollIntoView({ block, inline: "nearest", behavior: reduce ? "auto" : "smooth" });
}

// Every repo the relay talks about is in the RinseRepeatLabs org except TimeFlow, so
// `tester-pool#16`, `rPocketBase#12` or a repo added next month links without a list.
const OWNERS = { TimeFlow: "imcmurray" };
const DEFAULT_OWNER = "RinseRepeatLabs";
const DEFAULT_REPO = "TimeFlow";

// The repo a note's own `issue` field names, so a bare #16 in it means that repo's #16.
function repoOf(issue) {
  const match = /^(?:[A-Za-z0-9_.-]+\/)?([A-Za-z0-9_.-]+)#\d+$/.exec(String(issue || "").trim());
  return match ? match[1] : "";
}

function parseIssue(issue, repo) {
  const raw = String(issue || "").trim();
  const normalized = /^#(\d+)$/.test(raw) ? `${repo || DEFAULT_REPO}${raw}` : raw;
  const match = /^(?:([A-Za-z0-9_.-]+)\/)?([A-Za-z0-9_.-]+)#(\d+)$/.exec(normalized);
  if (!match) return null;
  return { owner: match[1] || OWNERS[match[2]] || DEFAULT_OWNER, repo: match[2], number: match[3] };
}

function issueHref(issue, repo) {
  const parsed = parseIssue(issue, repo);
  if (!parsed || !parsed.owner) return "";
  return `https://github.com/${parsed.owner}/${parsed.repo}/issues/${parsed.number}`;
}

// Always shown as repo#N: two repos share the relay, and #16 alone doesn't say which.
function issueLabel(issue, repo) {
  const parsed = parseIssue(issue, repo);
  return parsed ? `${parsed.repo}#${parsed.number}` : String(issue || "");
}

function issueLink(issue, repo) {
  const href = issueHref(issue, repo);
  if (!href) return null;
  const link = el("a", "", issueLabel(issue, repo));
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.addEventListener("click", (event) => event.stopPropagation());
  return link;
}

function appendIssueText(parent, text, repo) {
  const re = /(^|[^A-Za-z0-9_/])((?:[A-Za-z0-9_.-]+\/)?[A-Za-z0-9_.-]+#\d+|#\d+)/g;
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    const start = match.index + match[1].length;
    if (start > last) parent.append(document.createTextNode(text.slice(last, start)));
    const token = match[2];
    const link = issueLink(token, repo);
    parent.append(link || document.createTextNode(issueLabel(token, repo)));
    last = start + token.length;
    re.lastIndex = last;
  }
  if (last < text.length) parent.append(document.createTextNode(text.slice(last)));
}

// The repo a thread's latest note was about, for the lines that show only its subject.
function threadRepo(thread) {
  const issues = (thread && thread.issues) || [];
  return issues.length ? repoOf(issues[issues.length - 1]) : "";
}

// ── Ian writing from the desk ───────────────────────────────────────────────

async function post(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) throw new Error(body.error || `The desk said ${res.status}.`);
  return body;
}

function markReadButton(ids, label) {
  const button = el("button", "mark-read", label);
  button.type = "button";
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    button.disabled = true;
    button.textContent = "Marking…";
    state.keep = state.selected;
    try {
      await post("/api/read", { ids });
      await load();
    } catch (err) {
      button.disabled = false;
      button.textContent = label;
      button.title = err.message;
      flash(err.message);
    }
  });
  return button;
}

// Whoever handed the thread to Ian gets it back by default; otherwise it stays put.
function defaultBaton(thread) {
  if (thread.baton !== "ian") return thread.baton || "none";
  const from = [...thread.messages].reverse().find((note) => note.sender && note.sender !== "ian");
  return from ? from.sender : "linux";
}

const BATON_CHOICES = [
  ["mac", "Mac"],
  ["linux", "Linux"],
  ["ian", "Me (keep it)"],
  ["none", "Nobody (set it down)"],
];

function replyBox(thread) {
  if (!state.reply) state.reply = buildReplyBox();
  const box = state.reply;
  if (box.dataset.thread !== thread.name) {
    saveDraft();
    const draft = state.drafts[thread.name] || {};
    box.dataset.thread = thread.name;
    box.elements.subject.value = draft.subject || "";
    box.elements.body.value = draft.body || "";
    box.elements.baton.value = draft.baton || defaultBaton(thread);
    box.querySelector(".reply-error").textContent = "";
  }
  return box;
}

function saveDraft() {
  const box = state.reply;
  if (!box || !box.dataset.thread) return;
  const { subject, body, baton } = box.elements;
  if (subject.value || body.value) {
    state.drafts[box.dataset.thread] = { subject: subject.value, body: body.value, baton: baton.value };
  } else {
    delete state.drafts[box.dataset.thread];
  }
}

function buildReplyBox() {
  const form = el("form", "reply");
  form.noValidate = true;
  form.append(el("h3", "", "Reply as Ian"));
  const subject = el("input");
  subject.name = "subject";
  subject.maxLength = 200;
  subject.placeholder = "Subject: the one line they'll see first";
  subject.setAttribute("aria-label", "Subject");
  const body = el("textarea");
  body.name = "body";
  body.rows = 4;
  body.placeholder = "Anything more (Markdown). Optional.";
  body.setAttribute("aria-label", "Note");
  const row = el("div", "reply-row");
  const label = el("label", "reply-baton", "Baton to ");
  const baton = el("select");
  baton.name = "baton";
  for (const [value, text] of BATON_CHOICES) {
    const option = el("option", "", text);
    option.value = value;
    baton.append(option);
  }
  label.append(baton);
  const send = el("button", "reply-send", "Send");
  send.type = "submit";
  row.append(label, send);
  form.append(subject, body, row, el("p", "reply-error"));
  for (const field of [subject, body, baton]) field.addEventListener("input", saveDraft);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = form.querySelector(".reply-error");
    if (!subject.value.trim()) {
      error.textContent = "A reply needs a subject line.";
      subject.focus();
      return;
    }
    send.disabled = true;
    send.textContent = "Sending…";
    state.keep = form.dataset.thread;
    error.textContent = "";
    try {
      await post("/api/reply", {
        thread: form.dataset.thread, subject: subject.value, body: body.value, baton: baton.value,
      });
      delete state.drafts[form.dataset.thread];
      subject.value = "";
      body.value = "";
      form.dataset.thread = "";   // the next render re-reads the default baton
      await load();
    } catch (err) {
      error.textContent = err.message;
    } finally {
      send.disabled = false;
      send.textContent = "Send";
    }
  });
  return form;
}

// A redraw detaches and re-attaches the reply box, which drops focus. Put it back.
function rememberFocus() {
  const active = document.activeElement;
  if (!state.reply || !active || !state.reply.contains(active)) return null;
  return { node: active, start: active.selectionStart, end: active.selectionEnd };
}

function restoreFocus(focus) {
  if (!focus || !document.contains(focus.node)) return;
  focus.node.focus({ preventScroll: true });
  if (typeof focus.start === "number" && focus.node.setSelectionRange) {
    focus.node.setSelectionRange(focus.start, focus.end);
  }
}

function flash(message) {
  const meta = $("meta");
  if (meta) meta.textContent = message;
}

function personName(id) {
  return (WHO[id] && WHO[id].name) || id || "Settled";
}

function filteredThreads() {
  const board = state.board;
  if (!board) return [];
  const q = state.query.trim().toLowerCase();
  return board.threads.filter((thread) => {
    if (state.filter.startsWith("unread-")) {
      // The thread Ian just read stays listed, so marking it doesn't jump the page away.
      const who = state.filter.slice("unread-".length);
      if (thread.name !== state.keep && !thread.unread.some((note) => note.recipient === who)) return false;
    } else if (state.filter !== "all" && thread.baton !== state.filter) {
      return false;
    }
    if (!q) return true;
    const hay = [
      thread.name, thread.subject, thread.issues.join(" "),
      ...thread.messages.map((note) => `${note.subject}\n${note.body}`),
    ].join("\n").toLowerCase();
    return hay.includes(q);
  });
}

function selectThread(name) {
  state.selected = name;
  if (name !== state.keep) state.keep = "";
  if (name) history.replaceState(null, "", "#" + encodeURIComponent(name));
  render();
  const node = document.getElementById("transcript");
  if (node) node.scrollTop = 0;
  // Choosing a thread lands on the newest note still waiting on Ian, the first one he sees
  // with Mark read. Only here, on his click: a background refresh never moves the page.
  const thread = state.board && (state.board.threads || []).find((row) => row.name === name);
  const index = thread ? thread.messages.findLastIndex((note) => !note.read && note.recipient === "ian") : -1;
  if (index >= 0) showNote(index, "start");
}

function renderMeta() {
  const board = state.board;
  const meta = $("meta");
  clear(meta);
  if (!board) {
    meta.textContent = "Reading the relay…";
    return;
  }
  if (board.error) {
    meta.textContent = board.error;
    return;
  }
  const health = board.health || {};
  const bits = [
    `${board.message_count} note${board.message_count === 1 ? "" : "s"}`,
    `${board.threads.length} thread${board.threads.length === 1 ? "" : "s"}`,
    health.version ? `${health.name || "rPocketBase"} ${health.version}` : (health.name || "rPocketBase"),
    board.host ? board.host : "",
    board.fetched_at ? `read ${ago(board.fetched_at)}` : "",
  ].filter(Boolean);
  meta.textContent = bits.join("  ·  ");
  if (board.fixture) meta.append("  ·  sample data");
  if (state.paused) meta.append(state.missed ? "  ·  paused, something new is waiting" : "  ·  paused");
  meta.append(" ", pauseButton());
}

function pauseButton() {
  const button = el("button", "pause", state.paused ? "Resume" : "Pause");
  button.type = "button";
  button.setAttribute("aria-pressed", state.paused ? "true" : "false");
  button.title = state.paused
    ? "Start refreshing again, and catch up now"
    : "Stop the page refreshing. Your own Mark read and Reply still show.";
  button.addEventListener("click", () => {
    state.paused = !state.paused;
    if (!state.paused) {
      state.missed = false;
      load();
    } else {
      renderMeta();
    }
  });
  return button;
}

// The timer and the server's push both come through here; Ian's own actions call load().
function autoLoad() {
  if (state.paused) {
    if (!state.missed) {
      state.missed = true;
      renderMeta();
    }
    return;
  }
  load();
}

function stationMood(station) {
  if (station.holding.length) {
    // holding is newest first, so the first thread is the one most recently handed over.
    const more = station.holding.length - 1;
    return {
      kind: "working",
      label: "Working on",
      thread: station.holding[0].thread,
      more: more > 0 ? `+${more} more` : "",
    };
  }
  const ons = [];
  for (const item of station.waiting) {
    if (WHO[item.on] && item.on !== "none" && !ons.includes(item.on)) ons.push(item.on);
  }
  if (!ons.length) return { kind: "idle", label: "Idle" };
  if (ons.length === 1) return { kind: "waiting", label: `Waiting on ${personName(ons[0])}` };
  return { kind: "waiting", label: "Waiting" };
}

function blockingIds(stations) {
  const ids = new Set();
  for (const station of stations) {
    for (const item of station.waiting) {
      if (WHO[item.on] && item.on !== "none") ids.add(item.on);
    }
  }
  return ids;
}

function renderStations() {
  const root = $("stations");
  clear(root);
  const board = state.board;
  if (!board || board.error) return;
  const blocking = blockingIds(board.stations);
  for (const station of board.stations) {
    const who = WHO[station.id];
    const mood = stationMood(station);
    const card = el("section", `station ${mood.kind}`);
    if (blocking.has(station.id)) card.classList.add("blocking", `tint-${station.id}`);
    const head = el("div", "station-head");
    const seal = el("div", `seal ${station.id}`, String(station.holding.length));
    seal.title = `${station.holding.length} thread${station.holding.length === 1 ? "" : "s"} waiting on ${who.name}`;
    const titles = el("div", "station-titles");
    const h = el("h2", "", who.name);
    if (station.id === board.me) {
      const here = el("span", "here", " · this machine");
      h.append(here);
    }
    const moodLine = el("p", "station-state");
    moodLine.setAttribute("aria-label", [mood.label, mood.thread, mood.more].filter(Boolean).join(" "));
    moodLine.append(el("span", "station-label", mood.label));
    if (mood.thread) {
      const name = el("button", "station-thread", mood.thread);
      name.type = "button";
      name.title = `Open ${mood.thread}`;
      name.addEventListener("click", () => selectThread(mood.thread));
      moodLine.append(name);
      if (mood.more) moodLine.append(el("span", "station-more", mood.more));
    }
    titles.append(h, el("p", "place", who.place), moodLine);
    head.append(seal, titles);
    const corner = el("div", "station-corner");
    if (station.unread) corner.append(unreadBadge(station));
    const badge = ntfyBadge(station.id);
    if (badge) corner.append(badge);
    if (corner.childNodes.length) head.append(corner);
    card.append(head);

    card.append(el("p", "pile-label", "Has the baton"));
    card.append(pile(
      station.holding,
      (item) => item.subject || item.thread,
      (item) => ago(item.at),
      "Nothing on their desk.",
    ));
    card.append(el("p", "pile-label", "Waiting on"));
    if (!station.waiting.length) {
      card.append(el("p", "empty-line", "Nothing handed off."));
    } else {
      card.append(pile(
        station.waiting,
        (item) => item.subject || item.thread,
        (item) => `${personName(item.on)} · ${ago(item.at)}`,
      ));
    }
    root.append(card);
  }
}

// How many notes to this station it hasn't read. A click lists those threads.
function unreadBadge(station) {
  const who = WHO[station.id];
  const button = el("button", `unread-badge ${station.id}`, `${station.unread} unread`);
  button.type = "button";
  button.title = `Show the threads with notes ${who.name === "Ian" ? "you haven't" : who.name + " hasn't"} read`;
  button.addEventListener("click", () => setFilter(`unread-${station.id}`));
  return button;
}

function setFilter(filter) {
  state.filter = filter;
  state.keep = "";
  for (const node of $("filters").querySelectorAll("button")) {
    node.setAttribute("aria-pressed", node.dataset.filter === filter ? "true" : "false");
  }
  render();
}

// The index's Unread filter carries Ian's count, so the number is there before the click.
function renderUnreadFilter() {
  const button = document.querySelector('#filters [data-filter="unread-ian"]');
  if (!button || !state.board || state.board.error) return;
  const ian = (state.board.stations || []).find((station) => station.id === "ian");
  const count = ian ? ian.unread : 0;
  button.textContent = count ? `Unread ${count}` : "Unread";
  button.classList.toggle("has-unread", count > 0);
}

function ntfyBadge(stationId) {
  const info = state.board && state.board.ntfy;
  const stations = info && info.stations;
  if (!stations || !Object.prototype.hasOwnProperty.call(stations, stationId)) return null;
  const enabled = !!stations[stationId];
  const button = el("button", `ntfy-badge ${enabled ? "on" : "off"}`, enabled ? "ntfy on" : "ntfy off");
  button.type = "button";
  button.setAttribute("aria-pressed", enabled ? "true" : "false");
  if (!info.configured) {
    button.disabled = true;
    button.title = "Set BATON_DESK_NTFY_URL to enable notifications.";
    return button;
  }
  button.title = enabled ? "Notifications are on for this station. Click to turn them off." : "Notifications are off for this station. Click to turn them on.";
  button.addEventListener("click", () => toggleNtfy(stationId, !enabled));
  return button;
}

async function toggleNtfy(stationId, enabled) {
  const info = state.board && state.board.ntfy;
  if (!info || !info.configured || !info.stations || info.stations[stationId] === enabled) return;
  const previous = !!info.stations[stationId];
  info.stations[stationId] = enabled;
  renderStations();
  try {
    const res = await fetch("/api/ntfy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ station: stationId, enabled }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "Could not change notifications.");
    if (state.board) state.board.ntfy = body.ntfy;
  } catch (err) {
    if (state.board && state.board.ntfy && state.board.ntfy.stations) {
      state.board.ntfy.stations[stationId] = previous;
    }
  }
  renderStations();
}

function pile(items, title, sub, empty) {
  if (!items.length) {
    return el("p", "empty-line", empty || "Nothing here.");
  }
  const list = el("ul", "pile");
  for (const item of items) {
    const li = el("li");
    const button = el("button");
    button.type = "button";
    const subject = el("span", "subject");
    appendIssueText(subject, title(item), repoOf(item.issue));
    button.append(subject);
    button.append(el("span", "when", sub(item)));
    button.addEventListener("click", () => selectThread(item.thread));
    li.append(button);
    list.append(li);
  }
  return list;
}

function renderThreads() {
  const root = $("threads");
  clear(root);
  const threads = filteredThreads();
  if (!state.board) return;
  if (!threads.length) {
    root.append(el("p", "empty-line", "No thread in this view."));
    return;
  }
  for (const thread of threads) {
    const button = el("button", "thread");
    button.type = "button";
    button.setAttribute("role", "listitem");
    button.setAttribute("aria-current", thread.name === state.selected ? "true" : "false");
    const name = el("span", "thread-name");
    name.append(el("span", "", thread.name));
    const pip = el("span", "baton-pip", personName(thread.baton));
    if (thread.unread.length) pip.textContent += " · unread";
    name.append(pip);
    button.append(name);
    const sub = el("span", "sub");
    appendIssueText(sub, thread.subject || "No subject", threadRepo(thread));
    button.append(sub);
    if (thread.at) button.append(el("span", "when", ago(thread.at)));
    button.addEventListener("click", () => selectThread(thread.name));
    root.append(button);
  }
}

function renderTranscript() {
  const root = $("transcript");
  const focus = rememberFocus();
  clear(root);
  const board = state.board;
  if (!board) {
    root.append(el("p", "quiet", "Reading the relay…"));
    return;
  }
  if (board.error) {
    root.append(el("p", "", board.error));
    return;
  }
  const thread = board.threads.find((row) => row.name === state.selected) || filteredThreads()[0];
  if (!thread) {
    root.append(el("p", "quiet", "No notes yet."));
    return;
  }
  if (thread.name !== state.selected) state.selected = thread.name;
  const holds = holdsOf(thread.messages);
  const talk = el("div", "talk");
  talk.append(el("h2", "", thread.name));
  const stamp = el("p", `stamp ${thread.baton}`);
  const current = holds.length ? holds[holds.length - 1] : null;
  if (!current) {
    stamp.textContent = thread.baton === "none"
      ? "Settled. Nobody holds the baton."
      : `Waiting on ${personName(thread.baton)}`;
  } else if (thread.baton === "none") {
    stamp.textContent = `Settled. ${holdPhrase(current)}.`;
  } else {
    stamp.textContent = `Waiting on ${personName(thread.baton)} · ${span(current.end - current.start)}`;
  }
  talk.append(stamp);
  const count = el("p", "quiet countline", `${thread.count} note${thread.count === 1 ? "" : "s"}`);
  if (thread.issues.length) {
    count.append(" · ");
    thread.issues.forEach((issue, index) => {
      if (index) count.append(", ");
      const link = issueLink(issue);
      count.append(link || document.createTextNode(issueLabel(issue)));
    });
  }
  const mine = thread.messages.filter((note) => !note.read && note.recipient === "ian" && note.id);
  if (mine.length > 1) {
    count.append(" · ");
    count.append(markReadButton(mine.map((note) => note.id), `Mark all ${mine.length} read`));
  }
  talk.append(count);

  // Walked oldest-first, because each handoff line is about the note before it, and shown
  // newest-first, like the thread index.
  const notes = el("div", "notes");
  let previous = "";
  thread.messages.forEach((note, index) => {
    const baton = note.baton || "none";
    const block = el("article", `note ${note.sender || "none"}`);
    block.id = "note-" + index;
    const head = el("header");
    head.append(el("span", "", `${personName(note.sender)} → ${personName(note.recipient)}`));
    const time = el("time", "", when(note.at));
    if (note.at) time.dateTime = note.at;
    head.append(time);
    if (!note.read) {
      head.append(el("span", "unread-tag", "Unread"));
      if (note.recipient === "ian" && note.id) head.append(markReadButton([note.id], "Mark read"));
    }
    const repo = repoOf(note.issue) || threadRepo(thread);
    if (note.issue) {
      const link = issueLink(note.issue);
      head.append(link || el("span", "", issueLabel(note.issue)));
    }
    block.append(head);
    const title = el("h3");
    appendIssueText(title, note.subject || "No subject", repo);
    block.append(title);
    if ((note.body || "").trim()) block.append(renderMarkdown(note.body, repo));
    if (baton !== previous) {
      const started = holds.find((hold) => hold.index === index);
      const prev = started ? holds[holds.indexOf(started) - 1] : null;
      let handed = baton === "none" ? "Baton set down." : `Baton to ${personName(baton)}.`;
      if (prev) handed += ` ${holdPhrase(prev)}.`;
      block.append(el("p", "handed", handed));
    }
    previous = baton;
    notes.prepend(block);
  });
  talk.append(replyBox(thread), notes, mountSplitter(talk), renderTimeline(holds));
  root.append(talk);
  restoreFocus(focus);
}

const NOTES_WIDTH_KEY = "baton-desk-notes-width";

function mountSplitter(talk) {
  const splitter = el("div", "splitter");
  splitter.tabIndex = 0;
  splitter.setAttribute("role", "separator");
  splitter.setAttribute("aria-orientation", "vertical");
  splitter.setAttribute("aria-label", "Drag to resize the notes and the handoffs");
  let saved = 0;
  try { saved = Number(localStorage.getItem(NOTES_WIDTH_KEY)) || 0; } catch (_) { saved = 0; }
  if (saved >= 240) talk.style.setProperty("--notes-width", saved + "px");

  const remember = (width) => {
    talk.style.setProperty("--notes-width", width + "px");
    try { localStorage.setItem(NOTES_WIDTH_KEY, String(width)); } catch (_) {}
  };
  const clamp = (width) => {
    const bounds = talk.getBoundingClientRect();
    const max = Math.max(240, bounds.width - 232);
    return Math.round(Math.min(Math.max(width, 240), max));
  };
  splitter.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    try { splitter.setPointerCapture(event.pointerId); } catch (_) {}
    const origin = talk.getBoundingClientRect().left;
    const move = (ev) => remember(clamp(ev.clientX - origin));
    const up = () => {
      splitter.removeEventListener("pointermove", move);
      splitter.removeEventListener("pointerup", up);
      splitter.removeEventListener("pointercancel", up);
    };
    splitter.addEventListener("pointermove", move);
    splitter.addEventListener("pointerup", up);
    splitter.addEventListener("pointercancel", up);
  });
  splitter.addEventListener("keydown", (event) => {
    const step = event.shiftKey ? 80 : 24;
    const current = parseFloat(getComputedStyle(talk).gridTemplateColumns) || 0;
    let width = current;
    if (event.key === "ArrowLeft") width -= step;
    else if (event.key === "ArrowRight") width += step;
    else if (event.key === "Home") width = 240;
    else if (event.key === "End") width = talk.getBoundingClientRect().width - 232;
    else return;
    event.preventDefault();
    remember(clamp(width));
  });
  splitter.addEventListener("dblclick", () => {
    talk.style.removeProperty("--notes-width");
    try { localStorage.removeItem(NOTES_WIDTH_KEY); } catch (_) {}
  });
  return splitter;
}

function ageLabel(ms) {
  if (ms < 1000) return "now";
  return span(ms);
}

let laneObserver = null;

function renderTimeline(holds) {
  const aside = el("aside", "lane-time");
  aside.setAttribute("aria-label", "Baton handoffs");
  aside.append(el("p", "pile-label", "Handoffs"));
  if (!holds.length) {
    aside.append(el("p", "quiet", "These notes have no times."));
    return aside;
  }
  // Newest hold on the left, so "now" is the left edge and age grows to the right.
  const recentFirst = holds.slice().reverse();
  const scroll = el("div", "lane-scroll");
  const badges = el("div", "lane-badges");
  badges.setAttribute("role", "list");
  const scale = el("div", "lane-scale");
  const list = el("ol", "lane-list");
  recentFirst.forEach((hold, index) => {
    const phrase = holdPhrase(hold);
    const clock = holdClock(hold);
    const label = hold.subject ? `${phrase}. ${hold.subject}. ${clock.range}.` : `${phrase}. ${clock.range}.`;
    const weight = Math.max(1, Math.round(Math.sqrt(Math.max(hold.end - hold.start, 1))));
    const who = hold.baton === "none" ? "Set down" : personName(hold.baton);
    const seg = el("button", `seg lane-slot ${hold.baton}${hold.open ? " open" : ""}`);
    seg.type = "button";
    seg.dataset.weight = String(weight);
    seg.title = label;
    seg.setAttribute("aria-label", label);
    seg.append(el("span", "seg-dur", span(hold.end - hold.start)));
    seg.append(el("span", "seg-who", who));
    seg.addEventListener("click", () => showNote(hold.index));
    badges.append(seg);

    const newerEdge = Math.max(0, Date.now() - hold.end);
    const tick = el("div", "lane-tick lane-slot");
    tick.dataset.weight = String(weight);
    tick.dataset.age = String(Math.round(newerEdge));
    const mark = el("span", "", ageLabel(newerEdge));
    mark.title = hold.open ? "now" : when(hold.end);
    tick.append(mark);
    if (index === recentFirst.length - 1) {
      const oldest = ageLabel(Date.now() - hold.start);
      const end = el("span", "lane-age-end", oldest);
      end.title = when(hold.start);
      tick.append(end);
      tick.classList.add("end");
      tick.dataset.endAge = String(Math.round(Date.now() - hold.start));
    }
    scale.append(tick);

    const row = el("li");
    const jump = el("button");
    jump.type = "button";
    jump.title = label;
    const pip = el("span", `mark ${hold.baton}`);
    pip.setAttribute("aria-hidden", "true");
    const subject = el("span", "lane-subject");
    appendIssueText(subject, hold.subject || "", hold.repo);
    jump.append(pip, el("span", "lane-phrase", phrase), subject, el("span", "lane-when", clock.start));
    jump.addEventListener("click", () => showNote(hold.index));
    row.append(jump);
    list.append(row);
  });
  scroll.append(badges, scale);

  const scrub = el("label", "lane-scrub");
  scrub.append(el("span", "visually-hidden", "Drag through the handoffs. Now is on the left."));
  const input = el("input");
  input.type = "range";
  input.min = "0";
  input.max = "0";
  input.value = "0";
  input.setAttribute("aria-label", "Drag through the handoffs. Now is on the left.");
  scrub.append(input);

  const sync = () => {
    const min = 112;
    const gap = 3;
    const slots = [...badges.querySelectorAll(".seg")];
    const ticks = [...scale.querySelectorAll(".lane-tick")];
    const count = slots.length;
    const basis = count * min + Math.max(0, count - 1) * gap;
    const view = scroll.clientWidth;
    const extra = Math.max(0, view - basis);
    const weightSum = slots.reduce((sum, slot) => sum + Number(slot.dataset.weight), 0) || 1;
    slots.forEach((slot, index) => {
      const width = min + extra * (Number(slot.dataset.weight) / weightSum);
      const px = width + "px";
      slot.style.flex = "none";
      slot.style.width = px;
      if (ticks[index]) {
        ticks[index].style.flex = "none";
        ticks[index].style.width = px;
      }
    });
    const track = basis + extra;
    badges.style.width = track + "px";
    scale.style.width = track + "px";
    const max = Math.max(0, Math.round(scroll.scrollWidth - scroll.clientWidth));
    input.max = String(max);
    input.disabled = max < 1;
    if (document.activeElement !== input) input.value = String(Math.round(scroll.scrollLeft));
  };
  input.addEventListener("input", () => {
    scroll.scrollLeft = Number(input.value);
  });
  scroll.addEventListener("scroll", sync);
  if (laneObserver) laneObserver.disconnect();
  laneObserver = new ResizeObserver(sync);
  laneObserver.observe(scroll);
  requestAnimationFrame(sync);

  aside.append(scroll, scrub, list);
  return aside;
}

function bead(on, bad) {
  const node = el("span", "bead" + (on ? " on" : "") + (bad ? " bad" : ""));
  return node;
}

function renderProtocol() {
  const root = $("protocol");
  clear(root);
  const protocol = state.board && state.board.protocol;
  if (!protocol) return;

  const head = el("div", "river-head");
  const titles = el("div");
  titles.append(el("p", "eyebrow", "Coordinator"));
  titles.append(el("h2", "", "Protocol v1"));
  const aside = el("p", "aside");
  if (!protocol.live) {
    aside.textContent = "The lanes are drawn and waiting. build_requests and round_plans are not on this rPocketBase yet, so there is nothing to report. When protocol v1 is imported, each build takes its place on the track.";
  } else {
    aside.textContent = "A row is the record of a build. The relay note is only the doorbell. Ready means testers can install it. It does not open a round.";
  }
  head.append(titles, aside);
  root.append(head);

  const lanes = el("div", "lanes");
  for (const lane of protocol.lanes) {
    const card = el("section", "lane");
    card.append(el("h3", "", laneLabel(lane.id)));
    card.append(el("p", "owner", `Built by ${personName(lane.owner)}`));
    const track = el("div", "track");
    const statuses = new Set(lane.rows.map((row) => row.status));
    for (const status of protocol.flow) {
      track.append(bead(statuses.has(status), false));
    }
    track.append(el("span", "bead-label", "or"));
    for (const status of protocol.side) {
      track.append(bead(statuses.has(status), true));
    }
    card.append(track);
    if (!lane.rows.length) {
      card.append(el("p", "owner", protocol.live ? "No build on this lane." : "Empty until the collection exists."));
    }
    for (const row of lane.rows) card.append(buildCard(row));
    lanes.append(card);
  }
  root.append(lanes);

  const plans = el("div", "plans");
  plans.append(el("p", "eyebrow", "Test plans"));
  if (protocol.plans_http === 404 || protocol.builds_http === 404) {
    const note = el("p", "aside");
    note.textContent = protocol.builds_http === 404
      ? "The records API answered 404 for the protocol collections. The board will fill these in on the next read after setup imports them."
      : "One of the protocol collections is not readable with the keys on this machine yet.";
    plans.append(note);
  }
  const row = el("div", "plan-row");
  if (protocol.plans.length) {
    for (const plan of protocol.plans) row.append(planCard(plan));
  } else if (protocol.plans_http === 200) {
    row.append(el("p", "aside", "No round plans yet."));
  }
  plans.append(row);
  root.append(plans);
}

function laneLabel(id) {
  if (id === "ios") return "iOS";
  if (id === "android") return "Android";
  return id ? id.charAt(0).toUpperCase() + id.slice(1) : "Lane";
}

function buildCard(row) {
  const card = el("div", "build");
  const title = [row.app_name, row.version].filter(Boolean).join(" ");
  const line = el("div");
  line.append(el("strong", "", title || row.status || "Build"));
  card.append(line);
  const bits = [row.status, row.beta ? "beta" : "", row.store_build ? `build ${row.store_build}` : ""]
    .filter(Boolean);
  card.append(el("p", "", bits.join(" · ")));
  if (row.baton) card.append(el("p", "", `Baton: ${personName(row.baton)}`));
  if (row.detail) card.append(el("p", "", row.detail));
  if (row.thread) {
    const jump = el("button", "as-link", `Thread ${row.thread}`);
    jump.type = "button";
    jump.addEventListener("click", () => selectThread(row.thread));
    const holder = el("p");
    holder.append(jump);
    card.append(holder);
  }
  if (row.protocol_version && row.protocol_version !== "1") {
    card.append(el("p", "warn", `Protocol ${row.protocol_version}. This desk reads v1.`));
  }
  return card;
}

function planCard(plan) {
  const card = el("div", "plan");
  card.append(el("strong", "", plan.name || plan.app_name || "Plan"));
  const bits = [laneLabel(plan.lane), plan.status, plan.distribution].filter(Boolean);
  card.append(el("p", "", bits.join(" · ")));
  if (plan.last_version || plan.last_store_build) {
    card.append(el("p", "", `Last draft ${plan.last_version} ${plan.last_store_build}`.trim()));
  }
  if (plan.detail) card.append(el("p", "", plan.detail));
  return card;
}

function render() {
  renderMeta();
  renderUnreadFilter();
  renderStations();
  if (state.board && !state.board.error) {
    const threads = filteredThreads();
    if (!threads.some((thread) => thread.name === state.selected)) {
      state.selected = threads.length ? threads[0].name : "";
    }
  }
  renderThreads();
  renderTranscript();
  renderProtocol();
}

async function load() {
  try {
    const res = await fetch("/api/board", { cache: "no-store" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || "The board could not read the relay.");
    const previous = state.selected;
    const same = state.board && !state.board.error && state.board.fetched_at === body.fetched_at;
    const ntfyWas = JSON.stringify(state.board && state.board.ntfy ? state.board.ntfy.stations : null);
    state.board = body;
    if (same) {
      renderMeta();
      const ntfyNow = JSON.stringify(body.ntfy ? body.ntfy.stations : null);
      if (ntfyNow !== ntfyWas) renderStations();
      return;
    }
    if (!previous && location.hash) {
      state.selected = decodeURIComponent(location.hash.slice(1));
    } else if (previous && body.threads.some((thread) => thread.name === previous)) {
      state.selected = previous;
    }
    render();
  } catch (err) {
    state.board = { error: err.message || "The board could not read the relay.", threads: [], stations: [] };
    render();
  }
}

function bind() {
  $("filters").addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    setFilter(button.dataset.filter);
  });
  $("search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderThreads();
    renderTranscript();
  });
  window.addEventListener("hashchange", () => {
    state.selected = decodeURIComponent(location.hash.slice(1));
    render();
  });
}

bind();
if (window.__BOARD__) {
  state.board = window.__BOARD__;
  if (location.hash) state.selected = decodeURIComponent(location.hash.slice(1));
  render();
}
load();
setInterval(autoLoad, 10000);
try {
  const events = new EventSource("/api/events");
  events.addEventListener("board", () => autoLoad());
} catch (_) {
  /* polling still refreshes the desk */
}
