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

function when(iso) {
  const then = Date.parse(iso);
  if (!then) return "";
  return new Date(then).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function issueHref(issue) {
  const match = /^(?:([A-Za-z0-9_.-]+)\/)?([A-Za-z0-9_.-]+)#(\d+)$/.exec(issue || "");
  if (!match) return "";
  const owner = match[1] || (match[2] === "TimeFlow" ? "imcmurray" : "");
  if (!owner) return "";
  return `https://github.com/${owner}/${match[2]}/issues/${match[3]}`;
}

function personName(id) {
  return (WHO[id] && WHO[id].name) || id || "Settled";
}

function filteredThreads() {
  const board = state.board;
  if (!board) return [];
  const q = state.query.trim().toLowerCase();
  return board.threads.filter((thread) => {
    if (state.filter !== "all" && thread.baton !== state.filter) return false;
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
  if (name) history.replaceState(null, "", "#" + encodeURIComponent(name));
  render();
  const node = document.getElementById("transcript");
  if (node) node.scrollTop = 0;
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
}

function renderStations() {
  const root = $("stations");
  clear(root);
  const board = state.board;
  if (!board || board.error) return;
  for (const station of board.stations) {
    const who = WHO[station.id];
    const card = el("section", "station");
    const head = el("div", "station-head");
    const seal = el("div", `seal ${station.id}`, String(station.holding.length));
    seal.title = `${station.holding.length} thread${station.holding.length === 1 ? "" : "s"} waiting on ${who.name}`;
    const titles = el("div");
    const h = el("h2", "", who.name);
    if (station.id === board.me) {
      const here = el("span", "here", " · this machine");
      h.append(here);
    }
    titles.append(h, el("p", "place", who.place));
    head.append(seal, titles);
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

function pile(items, title, sub, empty) {
  if (!items.length) {
    return el("p", "empty-line", empty || "Nothing here.");
  }
  const list = el("ul", "pile");
  for (const item of items) {
    const li = el("li");
    const button = el("button");
    button.type = "button";
    button.append(el("span", "subject", title(item)));
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
    button.append(el("span", "sub", thread.subject || "No subject"));
    button.addEventListener("click", () => selectThread(thread.name));
    root.append(button);
  }
}

function renderTranscript() {
  const root = $("transcript");
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
  root.append(el("h2", "", thread.name));
  const stamp = el("p", `stamp ${thread.baton}`, `Waiting on ${personName(thread.baton)}`);
  if (thread.baton === "none") stamp.textContent = "Settled. Nobody holds the baton.";
  root.append(stamp);
  const count = el("p", "quiet", `${thread.count} note${thread.count === 1 ? "" : "s"}`);
  if (thread.issues.length) {
    count.append(" · ");
    thread.issues.forEach((issue, index) => {
      if (index) count.append(", ");
      const href = issueHref(issue);
      if (href) {
        const link = el("a", "", issue);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener";
        count.append(link);
      } else {
        count.append(issue);
      }
    });
  }
  root.append(count);

  let previous = "";
  for (const note of thread.messages) {
    const block = el("article", `note ${note.sender || "none"}`);
    const head = el("header");
    head.append(el("span", "", `${personName(note.sender)} → ${personName(note.recipient)}`));
    head.append(el("time", "", when(note.at)));
    if (!note.read) head.append(el("span", "unread-tag", "Unread"));
    if (note.issue) {
      const href = issueHref(note.issue);
      if (href) {
        const link = el("a", "", note.issue);
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener";
        head.append(link);
      } else {
        head.append(el("span", "", note.issue));
      }
    }
    block.append(head);
    block.append(el("h3", "", note.subject || "No subject"));
    if ((note.body || "").trim()) block.append(el("p", "body", note.body));
    if (note.baton !== previous) {
      const handed = note.baton === "none"
        ? "Baton set down."
        : `Baton to ${personName(note.baton)}.`;
      block.append(el("p", "handed", handed));
    }
    previous = note.baton;
    root.append(block);
  }
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
  renderStations();
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
    state.board = body;
    if (same) {
      renderMeta();
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
    state.filter = button.dataset.filter;
    for (const node of $("filters").querySelectorAll("button")) {
      node.setAttribute("aria-pressed", node === button ? "true" : "false");
    }
    render();
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
setInterval(load, 10000);
try {
  const events = new EventSource("/api/events");
  events.addEventListener("board", () => load());
} catch (_) {
  /* polling still refreshes the desk */
}
