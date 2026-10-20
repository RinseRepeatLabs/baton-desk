// End-to-end check of Mark read and Reply against a throwaway rPocketBase.
//
//   ~/Development/tester-pool/dev/verify/relay.sh --keep       # prints URL= and RELAY_KEY=
//   HOME=$(mktemp -d) RPB_URL=$URL RELAY_KEY=$KEY BATON_DESK_PORT=8799 python3 server.py &
//   node tests/e2e_reply.mjs $URL $KEY http://127.0.0.1:8799 <chrome> /tmp/desk.png
//
// The sandboxed HOME keeps ntfy off, so a run never pings a phone. Playwright comes from
// whichever project has it (see tester-pool's dev/verify/drive.mjs); <chrome> is a
// Chromium binary, e.g. one under ~/.cache/ms-playwright.

const { execFileSync } = await import("node:child_process");
const { chromium } = await import("/home/ianm/Development/ShadowWhisper/node_modules/playwright/index.mjs");
const [URL, KEY, DESK, CHROME, SHOT] = process.argv.slice(2);
const RELAY = "/home/ianm/Development/tester-pool/relay.py";
const T = `needs-ian-${Date.now()}`;
let fails = 0, n = 0;
const check = (ok, label) => { n++; if (!ok) fails++; console.log((ok ? "ok    " : "FAIL  ") + label); };
const relay = (me, ...args) => execFileSync("python3", [RELAY, ...args], {
  env: { ...process.env, RPB_URL: URL, RELAY_KEY: KEY, RELAY_ME: me, HOME: "/nonexistent" }, input: "" }).toString();
const api = async (path, opts = {}) => (await fetch(URL + path, { ...opts, headers: { Authorization: KEY, ...(opts.headers || {}) } })).json();
const notes = async (thread) => (await api(`/api/collections/session_messages/records?perPage=200&sort=at&filter=${encodeURIComponent(`thread="${thread}"`)}`)).items;

// Seed: a question for Ian, and a note for the Mac that Ian must not be able to mark read.
relay("mac", "send", "ian", "--thread", T, "--baton", "ian", "--issue", "tester-pool#20", "--subject", "waiting on you: ship #20?");
relay("mac", "send", "ian", "--thread", T, "--baton", "ian", "--subject", "and one more thing");
relay("linux", "send", "mac", "--thread", "other", "--baton", "mac", "--subject", "for the Mac only");
const macNote = (await notes("other"))[0];

// The server's own guard, before any page.
const post = (headers, body) => fetch(DESK + "/api/read", { method: "POST", headers, body }).then((r) => r.status);
check(await post({ "Content-Type": "text/plain" }, JSON.stringify({ ids: [macNote.id] })) === 403, "a text/plain POST (what a hostile form can send) is refused");
check(await post({ "Content-Type": "application/json", Origin: "http://evil.example" }, "{}") === 403, "a POST from another origin is refused");
// fetch() won't forge Host, so ask curl, which will: this is the DNS-rebinding case.
const forged = execFileSync("curl", ["-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", "-H", "Host: evil.example",
  "-H", "Content-Type: application/json", "-d", "{}", DESK + "/api/read"]).toString();
check(forged === "403", "a POST naming another host is refused");
const r = await fetch(DESK + "/api/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [macNote.id] }) });
check(r.status === 403 && /stays unread/.test((await r.json()).error), "a note for the Mac can't be marked read from the desk");
check(!(await notes("other"))[0].read_at, "and it really is still unread");

const b = await chromium.launch({ executablePath: CHROME });
const p = await b.newPage({ viewport: { width: 1400, height: 1100 } });
await p.goto(DESK + "/#" + T + "");
await p.waitForSelector(".note");
check(await p.locator(".note .mark-read").count() === 2, "each unread note to Ian has a Mark read button");
check(await p.locator(".countline .mark-read").textContent() === "Mark all 2 read", "and the thread offers Mark all 2 read");
check(await p.locator(".reply select").inputValue() === "mac", "the baton defaults back to the Mac, who handed it over");

// Unread counts: Ian has 2 notes waiting, the Mac 1.
const ianCard = p.locator(".station").filter({ hasText: "You" });
check(await ianCard.locator(".unread-badge").textContent() === "2 unread", "Ian's station says 2 unread");
check(await p.locator(".station").filter({ hasText: "Kitchen mini" }).locator(".unread-badge").textContent() === "1 unread",
      "the Mac's station counts the note it hasn't read");
check(await p.locator('#filters [data-filter="unread-ian"]').textContent() === "Unread 2", "the index's Unread filter carries the count");
await p.locator('#filters [data-filter="unread-ian"]').click();
const listed = await p.$$eval("#threads .thread .thread-name > span:first-child", (els) => els.map((e) => e.textContent));
check(listed.length === 1 && listed[0] === T, "Unread lists only the thread with Ian's unread notes");
await p.locator(".station").filter({ hasText: "Kitchen mini" }).locator(".unread-badge").click();
const macListed = await p.$$eval("#threads .thread .thread-name > span:first-child", (els) => els.map((e) => e.textContent));
check(macListed.includes("other") && !macListed.includes(T), "a station's count lists the threads it hasn't read");
await p.locator('#filters [data-filter="unread-ian"]').click();
await p.locator("#threads .thread").first().click();

await p.locator(".note .mark-read").first().click();
await p.waitForFunction(() => document.querySelectorAll(".note .mark-read").length === 1);
check((await notes(T))[0].read_at !== "", "Mark read sets read_at on that note");
check(!(await notes(T))[1].read_at, "and only that note");
await p.waitForFunction(() => document.querySelector('#filters [data-filter="unread-ian"]').textContent === "Unread 1");
check(await ianCard.locator(".unread-badge").textContent() === "1 unread", "the counts drop as notes are read");
await p.locator(".countline").count();
await p.locator(".note .mark-read").first().click();
await p.waitForFunction(() => !document.querySelector(".station .unread-badge.ian"));
check(await p.locator('#filters [data-filter="unread-ian"]').textContent() === "Unread", "at zero the badge goes and the filter is plain again");
check(await p.locator("#transcript h2").textContent() === T, "the open thread stays put in the Unread view once it's read");
await p.locator('#filters [data-filter="all"]').click();
// put one back to unread for the reply checks below, as a fresh note from the Mac
relay("mac", "send", "ian", "--thread", T, "--baton", "ian", "--subject", "and one more thing");
await p.waitForFunction(() => document.querySelectorAll(".note .mark-read").length === 1);

// Type a reply, then make the board redraw underneath it: text and focus must survive.
await p.locator(".reply input").fill("Yes, ship it");
await p.locator(".reply textarea").click();
await p.keyboard.type("Go ahead with #21 too");
const redraw = `redraw-${Date.now()}`;
relay("linux", "send", "mac", "--thread", redraw, "--baton", "mac", "--subject", "a redraw");
await p.waitForSelector(`text=${redraw}`, { timeout: 20000 });
check(await p.locator(".reply textarea").inputValue() === "Go ahead with #21 too", "a redraw mid-typing keeps the draft");
check(await p.evaluate(() => document.activeElement && document.activeElement.name) === "body", "and keeps the cursor in it");
await p.keyboard.type("!");
check(await p.locator(".reply textarea").inputValue() === "Go ahead with #21 too!", "typing carries on where it was");

await p.locator(".reply-send").click();
await p.waitForFunction(() => [...document.querySelectorAll(".note h3")].some((h) => h.textContent === "Yes, ship it"));
const last = (await notes(T)).at(-1);
check(last.sender === "ian" && last.recipient === "mac" && last.baton === "mac", "the reply is from Ian, to the Mac, baton to the Mac");
check(last.issue === "tester-pool#20", "it carries the thread's issue");
check((await notes(T)).filter((x) => x.recipient === "ian").every((x) => x.read_at), "replying marks Ian's remaining notes read");
check(await p.locator(".reply input").inputValue() === "", "the box clears after sending");
const bodyLinks = await p.$$eval(".note .body a", (as) => as.map((a) => a.textContent + " " + a.href));
check(bodyLinks.some((l) => l.startsWith("tester-pool#21 https://github.com/RinseRepeatLabs/tester-pool/issues/21")), "#21 in the reply resolves to tester-pool");

await p.locator(".reply-send").click();
check(/subject/.test(await p.locator(".reply-error").textContent()), "an empty subject is caught before sending");

await p.locator(".talk").screenshot({ path: SHOT });
const phone = await b.newPage({ viewport: { width: 390, height: 900 } });
await phone.goto(DESK + "/#" + T + "");
await phone.waitForSelector(".reply");
check(!(await phone.evaluate(() => document.documentElement.scrollWidth > innerWidth)), "no sideways scroll on a phone");
await phone.locator(".reply").screenshot({ path: SHOT.replace(".png", "-phone.png") });
await b.close();
console.log(`\n${n - fails}/${n} checks passed`);
process.exit(fails ? 1 : 0);
