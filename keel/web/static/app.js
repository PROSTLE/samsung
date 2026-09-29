// Keel web app: Overview, Live demo, Sessions (with replay), Benchmark, Setup.
// Plain ES modules, no build step. Every number and status on these pages comes
// from the server's API (keel/web/server.py), which reads traces, FDB-v3's
// reports, the config profiles and the environment.

import { ConsoleView, derive, drawWave } from "./console.js";
import { Replay } from "./player.js";
import {
  PIPELINES, PROFILES, SOURCES, api, clock, duration, esc, icons, keelMark, pct, plural, secs, store, toast, when,
} from "./ui.js";

const view = document.getElementById("view");
let page = null;        // the current page's cleanup
let live = null;        // the running LiveSession, if any
let info = null;        // /api/system, loaded once and refreshed on Setup

const NAV = [
  ["overview", "#/", "Overview", icons.home],
  ["live", "#/live", "Live demo", icons.live],
  ["sessions", "#/sessions", "Sessions", icons.sessions],
  ["benchmark", "#/benchmark", "Benchmark", icons.benchmark],
  ["setup", "#/setup", "Setup", icons.setup],
];

// ------------------------------------------------------------------ shell
function renderNav(active) {
  document.getElementById("nav").innerHTML = NAV.map(([key, href, label, icon]) =>
    `<a href="${href}" class="${key === active ? "active" : ""}" ${key === active ? 'aria-current="page"' : ""}>${icon}<span>${label}</span>${
      key === "live" && live ? `<span class="chip done live badge"><span class="dot"></span>Live</span>` : ""}</a>`).join("");
}

function crumbs(...parts) {
  document.getElementById("crumbs").innerHTML = parts.map((p, i) =>
    i === parts.length - 1 ? `<b>${esc(p[0])}</b>` : `<a href="${p[1]}">${esc(p[0])}</a><span>/</span>`).join("");
}

function renderSideFoot() {
  const foot = document.getElementById("side-foot");
  if (!info) { foot.innerHTML = ""; return; }
  const k = info.keys;
  const fdb = info.profiles.find((p) => p.name === "fdb_v3");
  const dot = (ok) => `<span class="chip ${ok ? "ok" : "bad"}" style="height:18px;padding:0 6px"><span class="dot"></span></span>`;
  foot.innerHTML = `
    <div class="side-status">
      <div class="row">${dot(k.livekit)}<b>LiveKit</b><span class="right">${k.livekit ? "keys set" : "no keys"}</span></div>
      <div class="row">${dot(k.gemini || k.openai || k.groq)}<b>Models</b><span class="right">${[k.gemini && "Gemini", k.openai && "OpenAI", k.groq && "Groq"].filter(Boolean).join(", ") || "no keys"}</span></div>
      <div class="row">${dot(info.fdb.present)}<b>FDB-v3</b><span class="right">${info.fdb.present ? "checkout found" : "not found"}</span></div>
      ${fdb ? `<div class="row muted small" style="margin-top:2px">Benchmark pipeline: ${esc(fdb.pipeline)}</div>` : ""}
    </div>`;
}

function themeButton() {
  const btn = document.getElementById("theme");
  const dark = () => document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  const paint = () => { btn.innerHTML = dark() ? icons.sun : icons.moon; };
  btn.onclick = () => {
    const next = dark() ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store("keel-theme", next);
    paint();
    window.dispatchEvent(new Event("themechange"));
  };
  paint();
}

document.getElementById("menu").onclick = () => document.getElementById("shell").classList.toggle("nav-open");
document.getElementById("nav").addEventListener("click", () => document.getElementById("shell").classList.remove("nav-open"));

// ------------------------------------------------------------------ router
const routes = [
  [/^#\/?$/, "overview", () => renderOverview()],
  [/^#\/live$/, "live", () => renderLive()],
  [/^#\/sessions$/, "sessions", () => renderSessions()],
  // #/replay/<session>?t=<seconds> opens the replay at that moment of the trace.
  [/^#\/replay\/([^?]+)(?:\?t=([\d.]+))?$/, "sessions", (m) => renderReplay(decodeURIComponent(m[1]), m[2] ? Number(m[2]) * 1000 : null)],
  [/^#\/benchmark$/, "benchmark", () => renderBenchmark()],
  [/^#\/benchmark\/(.+)$/, "benchmark", (m) => renderBenchmark(decodeURIComponent(m[1]))],
  [/^#\/setup$/, "setup", () => renderSetup()],
  // earlier addresses
  [/^#\/results(?:\/(.+))?$/, null, (m) => location.replace(m[1] ? `#/benchmark/${m[1]}` : "#/benchmark")],
  [/^#\/sessions\/(.+)$/, null, (m) => location.replace(`#/replay/${m[1]}`)],
];

async function route() {
  const hash = location.hash || "#/";
  if (live && hash !== "#/live") {
    if (!confirm("Leave the live conversation? It will end.")) { history.replaceState(null, "", "#/live"); return; }
    // End it quietly: its "conversation ended" view must not replace the page being opened.
    const ending = live;
    live = null;
    liveView?.destroy();
    liveView = null;
    ending.onEnd = () => {};
    ending.end();
  }
  page?.destroy?.();
  page = null;
  view.classList.remove("full");
  for (const [re, nav, fn] of routes) {
    const m = hash.match(re);
    if (!m) continue;
    if (nav) renderNav(nav);
    try { await fn(m); } catch (e) { console.error(e); view.innerHTML = errorBox(e); }
    view.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    return;
  }
  location.replace("#/");
}
window.addEventListener("hashchange", route);

const errorBox = (e) => `<div class="card card-body" style="margin-top:12px"><h2 style="font-size:16px">Something went wrong</h2><p class="soft" style="margin-top:6px">${esc(e.message || e)}</p></div>`;
const loading = (what) => { view.innerHTML = `<div class="stack" style="margin-top:12px"><div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:260px"></div></div>`; crumbs([what]); };
const copyable = (cmd) => `<div class="codeblock">${esc(cmd)}<button class="btn btn-ghost btn-sm copy" data-copy="${esc(cmd)}" aria-label="Copy">${icons.copy}</button></div>`;
view.addEventListener("click", (e) => {
  const b = e.target.closest("[data-copy]");
  if (!b) return;
  navigator.clipboard?.writeText(b.dataset.copy).then(() => toast("Copied"), () => toast("Copy failed"));
});

// ------------------------------------------------------------------ overview
const heroArt = `
<svg viewBox="0 0 600 320" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent-soft)" stop-opacity="0.9"/><stop offset="1" stop-color="var(--surface)" stop-opacity="0"/></linearGradient>
    <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity="0.22"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0.04"/></linearGradient>
  </defs>
  <rect width="600" height="320" fill="url(#sky)"/>
  <circle cx="470" cy="92" r="38" fill="var(--keel)" opacity="0.18"/>
  <path d="M0 150 C 90 120, 160 170, 250 140 S 420 110, 600 145 V320 H0Z" fill="var(--accent)" opacity="0.07"/>
  <path d="M0 196 C 110 176, 190 214, 300 192 S 480 170, 600 196 V320 H0Z" fill="url(#sea)"/>
  <g transform="translate(300 150)">
    <path d="M0 42 L0 -64 L58 42 Z" fill="var(--surface)" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>
    <path d="M-6 42 L-6 -40 L-44 42 Z" fill="var(--accent-soft)" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>
    <path d="M-62 48 H 78 L 62 64 H -46 Z" fill="var(--accent)"/>
    <path d="M6 64 L 2 98 L 16 98 L 20 64" fill="var(--accent)" opacity="0.55"/>
  </g>
  <path d="M0 214 C 110 196, 190 232, 300 212 S 480 192, 600 214" fill="none" stroke="var(--accent)" stroke-opacity="0.35" stroke-width="2"/>
  <path d="M0 246 C 120 230, 200 262, 320 244 S 500 228, 600 246" fill="none" stroke="var(--accent)" stroke-opacity="0.18" stroke-width="2"/>
  <g transform="translate(150 70)">
    <rect x="-16" y="-24" width="150" height="48" rx="24" fill="var(--surface)" stroke="var(--border)"/>
    ${[6, 14, 22, 12, 26, 16, 8, 20, 12, 6, 16, 10].map((h, i) => `<rect x="${i * 10}" y="${-h / 2}" width="5" height="${h}" rx="2.5" fill="${i < 7 ? "var(--user)" : "var(--agent)"}"/>`).join("")}
  </g>
</svg>`;

async function renderOverview() {
  crumbs(["Overview"]);
  loading("Overview");
  const [o, sys] = await Promise.all([api("/api/overview"), info ? Promise.resolve(info) : api("/api/system")]);
  info = sys; renderSideFoot(); crumbs(["Overview"]);
  const fence = (info.profiles.find((p) => p.name === "fdb_v3") || info.profiles[0] || {}).fence || {};
  const run = o.latest_run;
  const judgeNone = (run?.judge || "").startsWith("none");
  const statCard = (ic, color, k, v, unit, f) => `
    <div class="card stat"><div class="k"><span class="ico" style="background:var(--${color}-bg, var(--surface-3));color:var(--${color}, var(--ink-2))">${ic}</span>${k}</div>
      <div class="v">${v}${unit ? `<small>${unit}</small>` : ""}</div><div class="f">${f}</div></div>`;
  const bySource = Object.entries(o.by_source).map(([s, n]) => `${n} ${(SOURCES[s] || s).toLowerCase()}`).join(" · ");
  view.innerHTML = `
    <section class="hero">
      <div class="hero-copy">
        <span class="chip accent" style="align-self:flex-start">${keelMark} Samsung PRISM GenAI Hackathon · Theme 05</span>
        <h1>An interruption-safe execution layer for <em>voice agents.</em></h1>
        <p class="lede">Keel sits between a LiveKit voice agent's LLM and its tools and decides whether and when a tool call may run.
          It holds each call until you have finished speaking, drops the ones you take back, and never runs the same action twice.
          Evaluated on Full-Duplex-Bench v3.</p>
        <div class="cta">
          <a class="btn btn-primary btn-lg" href="#/live">${icons.play} Try the live demo</a>
          ${o.demo_session ? `<a class="btn btn-lg" href="#/replay/${encodeURIComponent(o.demo_session)}">Replay a session ${icons.arrow}</a>` : ""}
        </div>
      </div>
      <div class="hero-art">${heroArt}<div class="caption">Listen. Understand.<br>Decide. Safely.</div></div>
    </section>

    <div class="stats" style="margin-top:16px">
      ${statCard(icons.sessions, "accent", "Sessions recorded", o.sessions, "", bySource || "None yet: run the benchmark or talk to the agent")}
      ${statCard(icons.plan, "running", "Calls planned by the model", o.actions, "", `${o.executed} executed · ${o.reused} reused · ${o.failed} failed`)}
      ${statCard(icons.x, "dropped", "Dropped before they ran", o.not_sent, "", "Taken back or overtaken by new speech; never reached a tool")}
      ${statCard(icons.hand, "held", "Median hold before a call runs", o.hold_ms_median == null ? "–" : Math.round(o.hold_ms_median), o.hold_ms_median == null ? "" : "ms", `${plural(o.held, "call")} waited for the user to finish`)}
    </div>

    <div class="page-head" style="margin:28px 0 12px"><div><h1 style="font-size:19px">How Keel decides</h1>
      <p class="lede">The same three rules for every tool, with the timings from the benchmark profile (config/fdb_v3.toml).</p></div></div>
    <div class="steps3">
      <div class="card step"><span class="chip holding" style="align-self:flex-start">${icons.hand.replace("<svg", '<svg width="13" height="13"')} Held</span>
        <h3>Held until you have finished</h3>
        <p>A call the model plans while you are talking waits until your turn has ended and ${fence.quiet_ms ?? "–"} ms of quiet have passed${fence.transcript_wait_ms ? `, and until everything you have already said has been transcribed (at most ${(fence.transcript_wait_ms / 1000).toFixed(0)} s)` : ""}${fence.repair_wait_ms ? `. After a turn that is only "oh, wait", it waits up to ${(fence.repair_wait_ms / 1000).toFixed(0)} s for the correction` : ""}.</p></div>
      <div class="card step"><span class="chip not_sent" style="align-self:flex-start">${icons.x.replace("<svg", '<svg width="13" height="13"')} Dropped</span>
        <h3>Dropped when you take it back</h3>
        <p>If you keep speaking or correct yourself before it goes out, the held call is dropped and the model is told why, so it acts on what you meant. A dropped call never reaches the tool.</p></div>
      <div class="card step"><span class="chip done" style="align-self:flex-start">${icons.check.replace("<svg", '<svg width="13" height="13"')} Executed</span>
        <h3>Run once, and reported truthfully</h3>
        <p>An identical call later in the conversation reuses the first result. A write whose outcome cannot be confirmed is reported as unknown, never as done.</p></div>
    </div>

    <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-top:16px">
      <section class="card">
        <div class="card-head"><h2>Recent sessions</h2><div class="right"><a class="btn btn-ghost btn-sm" href="#/sessions">All sessions ${icons.arrow}</a></div></div>
        <div class="list">${o.recent.map(sessionListRow).join("") || `<p class="empty">No sessions yet.</p>`}</div>
      </section>
      <section class="card">
        <div class="card-head"><h2>Latest benchmark run</h2><div class="right">${run ? `<a class="btn btn-ghost btn-sm" href="#/benchmark/${encodeURIComponent(run.id)}">Details ${icons.arrow}</a>` : ""}</div></div>
        ${run ? `
          <div class="card-body stack" style="gap:14px">
            <div class="row-gap"><span class="chip accent">${esc(run.provider || "?")}</span><span class="muted small">${esc(runLabel(run))}</span></div>
            <div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr));gap:12px">
              ${miniKpi("Pass rate, strict", pct(run.pass_rate), "%", `${run.passed ?? "–"} of ${run.scenarios ?? "–"} scenarios`)}
              ${miniKpi("Tool selection", pct(run.tool_selection), "%", "Right tools, no extras")}
              ${miniKpi("Arguments", pct(run.argument_accuracy), "%", judgeNone ? "Exact match (no judge)" : "LLM judge")}
              ${miniKpi("First response, median", secs(run.latency_median_s), "s", "After the user stopped")}
            </div>
            ${judgeNone ? `<div class="notice">${icons.info}<span>Scored without FDB-v3's gpt-4o judge, so arguments must match exactly. The organisers' re-run uses the judge.</span></div>` : ""}
          </div>` : `<div class="card-body"><p class="soft">No completed run yet. <span class="mono">scripts/reproduce_fdb_v3.sh</span> runs FDB-v3 against the agent; every run appears on the Benchmark page with FDB-v3's own scores.</p></div>`}
      </section>
    </div>`;
  bindRows();
}

const miniKpi = (k, v, unit, f) => `<div style="border:1px solid var(--border);border-radius:12px;padding:12px 14px">
  <div class="muted small">${k}</div><div style="font-size:22px;font-weight:700;letter-spacing:-.02em">${v}<small class="muted" style="font-size:12px;margin-left:2px">${v === "–" ? "" : unit}</small></div><div class="muted small">${f}</div></div>`;

function sessionListRow(r) {
  return `<div class="list-row" data-href="#/replay/${encodeURIComponent(r.id)}" tabindex="0" role="link">
    <span class="avatar" style="background:var(--surface-3);color:var(--ink-2)">${r.source === "benchmark" ? icons.benchmark : r.source === "show_and_fix" ? icons.camera : icons.live}</span>
    <div class="main-col"><div class="title">${esc(r.title || r.first_words || (r.status === "live" ? "In progress" : "No speech recorded"))}</div>
      <div class="sub">${esc(SOURCES[r.source] || r.source)} · ${r.pipeline ? esc(r.pipeline) + " · " : ""}${when(r.modified)}</div></div>
    <div class="row-gap">${r.not_sent ? `<span class="chip not_sent">${r.not_sent} dropped</span>` : ""}${r.executed ? `<span class="chip done">${r.executed} executed</span>` : ""}</div>
  </div>`;
}

function bindRows() {
  view.querySelectorAll("[data-href]").forEach((el) => {
    el.onclick = () => (location.hash = el.dataset.href);
    el.onkeydown = (e) => { if (e.key === "Enter") location.hash = el.dataset.href; };
  });
}

const runLabel = (x) => `${x.id.slice(0, 4)}-${x.id.slice(4, 6)}-${x.id.slice(6, 8)} ${x.id.slice(9, 11)}:${x.id.slice(11, 13)} UTC · ${x.scenarios ? plural(x.scenarios, "scenario") : "no scenarios"}${{ running: " · running", stopped: " · stopped, no reports", invalid: " · invalid (see its note)" }[x.status] || ""}`;

// ------------------------------------------------------------------ live
let liveExamples = [];  // the chosen agent's "things to try", also shown in the empty chat
const exampleCard = (x) => `<div><span>“${esc(x.say)}”</span><small>${esc(x.expect)}</small></div>`;

async function renderLive() {
  crumbs(["Live demo"]);
  if (live) return mountLive();
  const [cfg, examples, sys] = await Promise.all([
    api("/api/config").catch(() => ({ live_available: false, keys: {} })),
    api("/api/examples").catch(() => ({})),
    info ? Promise.resolve(info) : api("/api/system").catch(() => null),
  ]);
  if (sys) { info = sys; renderSideFoot(); }
  const profiles = Object.keys(examples).filter((k) => !k.startsWith("_"));
  const demo = await api("/api/overview").then((o) => o.demo_session).catch(() => null);
  view.innerHTML = `
    <div class="live-landing">
      <span class="ll-mark">${keelMark}</span>
      <h1>Talk to the agent</h1>
      <p class="lede">Ask for something, then change your mind halfway through. Each call the model plans shows up in the conversation, with what Keel did with it.</p>
      ${cfg.live_available ? "" : `<div class="notice warn">${icons.alert}<span>LiveKit keys are not set. Add <code>LIVEKIT_URL</code>, <code>LIVEKIT_API_KEY</code> and <code>LIVEKIT_API_SECRET</code> to <code>.env</code> (a free LiveKit Cloud project) and restart this app.</span></div>`}
      <button class="btn btn-primary btn-lg ll-start" id="start" ${cfg.live_available ? "" : "disabled"}>${icons.mic} Start conversation</button>
      <div class="small muted">Uses your microphone. The agent joins within a few seconds.</div>
      ${profiles.length ? `<div class="ll-try">
        <div class="ll-try-head"><span class="eyebrow">Things to try</span>
          ${profiles.length > 1 ? `<div class="seg" id="ex-tabs">${profiles.map((p, i) => `<button data-p="${esc(p)}" class="${i === 0 ? "active" : ""}">${esc(PROFILES[p] || p)}</button>`).join("")}</div>` : ""}</div>
        <div class="try" id="ex-list"></div></div>` : ""}
      <details class="ll-help"><summary>No agent running?</summary>
        <div class="stack" style="gap:8px;margin-top:12px;text-align:left">
          <div class="small soft">Benchmark agent (FDB-v3's tools), free with a Gemini API key:</div>
          ${copyable("KEEL_PIPELINE=gemini_realtime python -m keel.livekit.agent dev")}
          <div class="small soft">Show &amp; Fix (camera), free with a Gemini API key:</div>
          ${copyable("KEEL_PIPELINE=gemini_realtime python -m extension.show_and_fix.agent dev")}
          <div class="small soft">From Windows, the agent and this app together in WSL: <code>wsl bash scripts/keel_live_wsl.sh --pipeline gemini_realtime</code> (add <code>--show-and-fix</code> for the camera agent, or use <code>--pipeline open</code> for open-weight models with no key).</div>
          ${demo ? `<a class="btn" style="align-self:flex-start" href="#/replay/${encodeURIComponent(demo)}">${icons.play} Replay a recorded session instead</a>` : ""}
        </div></details>
    </div>`;
  const showEx = (p) => {
    liveExamples = examples[p] || [];
    document.getElementById("ex-list").innerHTML = liveExamples.map(exampleCard).join("") || `<p class="empty">No examples for this agent.</p>`;
    document.querySelectorAll("#ex-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.p === p));
  };
  if (profiles.length) showEx(profiles[0]);
  document.getElementById("ex-tabs")?.addEventListener("click", (e) => { const p = e.target.closest("button")?.dataset.p; if (p) showEx(p); });
  const start = document.getElementById("start");
  if (start) start.onclick = startLive;
}

async function startLive() {
  const btn = document.getElementById("start");
  if (btn) { btn.disabled = true; btn.textContent = "Connecting…"; }
  try {
    const { LiveSession } = await import("./live.js");
    const token = await api("/api/token", { method: "POST" });
    live = new LiveSession(token, {
      onChange: () => scheduleLive(),
      onFrame: () => liveFrame(),
      onEnd: () => { const ended = live; live = null; page = null; renderNav("live"); liveView?.destroy(); liveView = null; renderEnded(ended); },
    });
    renderNav("live");
    mountLive();
    await live.connect();
  } catch (e) {
    live = null;
    renderNav("live");
    view.innerHTML = errorBox(e);
  }
}

let liveView = null, livePending = false;
function mountLive() {
  liveView = new ConsoleView(view, { mode: "live" });
  liveView.mount({
    title: "Live conversation",
    headRight: `<span class="mono muted small hide-sm">${esc(live.state.room)}</span>`,
    empty: `
      <span class="ll-mark">${keelMark}</span>
      <h3>Say something</h3>
      <p>Ask for something, then change your mind halfway through. Each call the model plans shows up here, with what Keel did with it.</p>
      ${liveExamples.length ? `<div class="try">${liveExamples.map(exampleCard).join("")}</div>` : ""}`,
    dockLeft: `<button class="rb" id="l-cam" aria-label="Turn the camera on" title="Camera">${icons.camera}</button>`,
    dockRight: `
      <button class="rb primary" id="l-mic" aria-label="Mute" title="Mute">${icons.mic}</button>
      <button class="rb danger" id="l-end" aria-label="End the conversation" title="End">${icons.end}</button>`,
  });
  view.querySelector(".chat-main").insertAdjacentHTML("beforeend",
    `<video id="l-preview" class="cam-preview" autoplay muted playsinline hidden></video>`);
  document.getElementById("l-mic").onclick = () => live.toggleMic();
  document.getElementById("l-cam").onclick = () => live.toggleCamera(document.getElementById("l-preview"));
  document.getElementById("l-end").onclick = () => live.end();
  page = { redraw: () => { if (liveView) { liveView.sig = {}; updateLive(); } } };
  scheduleLive();
}

function scheduleLive() {
  if (livePending) return;
  livePending = true;
  requestAnimationFrame(() => { livePending = false; updateLive(); });
}

function updateLive() {
  if (!live || !liveView) return;
  const s = live.state, now = live.now();
  liveView.update(derive(live.events(), now), [0, Math.max(now, 30000)]);
  // The clock is filled in each frame (liveFrame), so the status markup stays the
  // same from one update to the next and is not rebuilt.
  const status = s.connecting ? `<span class="chip">Connecting…</span>`
    : !s.agentJoined ? `<span class="chip holding">${s.waitedLong ? "The agent has not joined. Is it running?" : "Waiting for the agent to join"}</span>`
    : `<span class="chip done live"><span class="dot"></span>Live</span><span class="clock" id="l-clock"></span>${s.pipeline ? `<span class="muted small hide-sm">${esc(PIPELINES[s.pipeline] || s.pipeline)}</span>` : ""}`;
  liveView.setStatus(status);
  const c = document.getElementById("l-clock");
  if (c) c.textContent = clock(now);
  const mic =document.getElementById("l-mic");
  const micState = s.micOn ? "on" : "off";
  if (mic && mic.dataset.s !== micState) {
    mic.dataset.s = micState;
    mic.innerHTML = s.micOn ? icons.mic : icons.micOff;
    mic.classList.toggle("off", !s.micOn);
    mic.setAttribute("aria-label", s.micOn ? "Mute" : "Unmute");
    mic.title = s.micOn ? "Mute" : "Unmute";
  }
  const cam = document.getElementById("l-cam");
  if (cam && cam.classList.contains("on") !== s.camOn) {
    cam.classList.toggle("on", s.camOn);
    cam.setAttribute("aria-label", s.camOn ? "Turn the camera off" : "Turn the camera on");
  }
  const prev = document.getElementById("l-preview");
  if (prev) { prev.hidden = !s.camOn; if (s.camOn && !prev.srcObject) live.attachPreview(prev); }
}

let lastTimeline = 0;
function liveFrame() {
  if (!live || !liveView) return;
  const canvas = document.getElementById("c-wave");
  if (canvas) drawWave(canvas, live.bars);
  const c = document.getElementById("l-clock");
  if (c) c.textContent = clock(live.now());
  const t = performance.now();
  if (t - lastTimeline > 250) { lastTimeline = t; updateLive(); }
}

function renderEnded(ended) {
  const room = ended?.state.room;
  // The agent writes the session's trace; without it there is nothing to replay.
  const joined = !!ended?.state.agentJoined;
  const acts = ended ? [...ended.state.actions.values()] : [];
  const n = (st) => acts.filter((a) => a.state === st).length;
  crumbs(["Live demo"]);
  view.innerHTML = `
    <div class="card card-body" style="margin-top:12px;max-width:720px">
      <div class="eyebrow">Conversation ended</div>
      <h1 style="font-size:24px;margin-top:6px">${!joined ? "The agent never joined" : acts.length ? `${plural(acts.length, "call")} planned` : "No calls were planned"}</h1>
      <div class="row-gap" style="margin-top:10px">${n("done") ? `<span class="chip done">${n("done")} executed</span>` : ""}${n("not_sent") ? `<span class="chip not_sent">${n("not_sent")} dropped</span>` : ""}${n("reused") ? `<span class="chip reused">${n("reused")} reused</span>` : ""}</div>
      <p class="soft" style="margin-top:12px">${joined
        ? "The agent saved the whole conversation as a session: replay it to see every decision on the timeline."
        : "No agent answered in this room, so nothing was recorded. Start the app with an agent (see Setup, “This app plus an agent”) and try again."}</p>
      <div class="row-gap" style="margin-top:16px">
        ${room && joined ? `<a class="btn btn-primary" href="#/replay/${encodeURIComponent(room)}">${icons.play} Replay this session</a>` : ""}
        <button class="btn" id="again">Start another</button></div>
    </div>`;
  document.getElementById("again").onclick = () => renderLive();
}

// ------------------------------------------------------------------ sessions
let sessionFilter = { source: "all", q: "" };
async function renderSessions() {
  loading("Sessions");
  const rows = await api("/api/sessions");
  crumbs(["Sessions"]);
  const counts = rows.reduce((c, r) => ({ ...c, [r.source]: (c[r.source] || 0) + 1 }), {});
  const sources = ["all", ...Object.keys(SOURCES).filter((s) => counts[s])];
  view.innerHTML = `
    <div class="page-head"><div><h1>Sessions</h1>
      <p class="lede">Every conversation the agents recorded, benchmark scenarios and live ones. Open one to replay it: what was said, each call the model planned, and what Keel did with it.</p></div>
      <div class="row-gap">
        <input class="input" id="q" type="search" placeholder="Search what was said" value="${esc(sessionFilter.q)}" aria-label="Search sessions" style="width:240px">
        <div class="seg" id="src">${sources.map((s) => `<button data-s="${s}" class="${s === sessionFilter.source ? "active" : ""}">${s === "all" ? "All" : esc(SOURCES[s])}<span class="n">${s === "all" ? rows.length : counts[s]}</span></button>`).join("")}</div>
      </div></div>
    <section class="card table-wrap"><table class="table">
      <thead><tr><th>Session</th><th class="hide-sm">Source</th><th class="num">Calls</th><th class="hide-sm">Keel</th><th class="num hide-sm">Length</th><th class="num hide-sm">Recorded</th></tr></thead>
      <tbody id="rows"></tbody></table></section>`;
  const paint = () => {
    const q = sessionFilter.q.trim().toLowerCase();
    const shown = rows.filter((r) => (sessionFilter.source === "all" || r.source === sessionFilter.source)
      && (!q || `${r.title || ""} ${r.first_words || ""} ${r.id} ${r.scenario || ""}`.toLowerCase().includes(q)));
    document.getElementById("rows").innerHTML = shown.map((r) => `
      <tr class="link" data-href="#/replay/${encodeURIComponent(r.id)}" tabindex="0">
        <td style="max-width:520px"><div class="title clamp2">${esc(r.title || r.first_words || (r.status === "live" ? "In progress" : "No speech recorded"))}</div>
          <div class="sub">${r.title && r.first_words ? `<span class="clamp2">${esc(r.first_words)}</span>` : ""}<span class="mono">${esc(r.id)}</span>${r.pipeline ? ` · ${esc(r.pipeline)}` : ""}</div></td>
        <td class="hide-sm"><span class="chip">${esc(SOURCES[r.source] || r.source)}</span>${r.status === "live" ? ` <span class="chip done live"><span class="dot"></span>Live</span>` : r.status === "incomplete" ? ` <span class="chip holding" title="The trace has no closing record: the agent was stopped mid-session">Cut short</span>` : ""}</td>
        <td class="num">${r.actions}</td>
        <td class="hide-sm"><div class="row-gap">${r.held ? `<span class="chip holding">${r.held} held</span>` : ""}${r.executed ? `<span class="chip done">${r.executed} executed</span>` : ""}${r.not_sent ? `<span class="chip not_sent">${r.not_sent} dropped</span>` : ""}${r.reused ? `<span class="chip reused">${r.reused} reused</span>` : ""}${!r.actions ? `<span class="muted small">no calls</span>` : ""}</div></td>
        <td class="num hide-sm">${r.duration_ms == null ? `<span class="muted">${duration(r.last_t)}+</span>` : duration(r.duration_ms)}</td>
        <td class="num hide-sm muted">${when(r.modified)}</td>
      </tr>`).join("") || `<tr><td colspan="6" class="empty">${rows.length ? "No session matches." : "No sessions yet. Run the benchmark or start a live conversation."}</td></tr>`;
    bindRows();
  };
  paint();
  document.getElementById("q").oninput = (e) => { sessionFilter.q = e.target.value; paint(); };
  document.getElementById("src").onclick = (e) => {
    const s = e.target.closest("button")?.dataset.s;
    if (!s) return;
    sessionFilter.source = s;
    document.querySelectorAll("#src button").forEach((b) => b.classList.toggle("active", b.dataset.s === s));
    paint();
  };
}

// ------------------------------------------------------------------ replay
async function renderReplay(id, at = null) {
  loading("Replay");
  const data = await api(`/api/sessions/${encodeURIComponent(id)}`);
  const s = data.summary;
  const title = s.title || s.first_words || id;
  crumbs(["Sessions", "#/sessions"], [s.title || id]);
  const v = data.verdict, a = data.audio;
  const meta = [
    SOURCES[s.source] || s.source,
    s.scenario && s.title ? `scenario <span class="mono">${esc(s.scenario)}</span>` : null,
    s.pipeline ? esc(PIPELINES[s.pipeline] || s.pipeline) : null,
    s.duration_ms != null ? duration(s.duration_ms) : "cut short (no closing record)",
  ].filter(Boolean).join(" · ");
  const argRows = (v?.arguments || []).map((x) => {
    const keys = [...new Set([...Object.keys(x.expected || {}), ...Object.keys(x.actual || {})])];
    return keys.map((k) => {
      const ok = JSON.stringify((x.expected || {})[k]) === JSON.stringify((x.actual || {})[k]);
      return `<tr><td class="mono">${esc(x.tool)}.${esc(k)}</td><td>${esc(JSON.stringify((x.expected || {})[k] ?? null))}</td><td>${esc(JSON.stringify((x.actual || {})[k] ?? null))}</td><td>${ok ? `<span class="chip pass">match</span>` : `<span class="chip fail">differs</span>`}</td></tr>`;
    }).join("");
  }).join("");
  const above = `
    <div class="page-head" style="margin-top:4px"><div style="min-width:0"><h1 class="clamp2" style="font-size:22px">${esc(title)}</h1>
      <p class="lede small">${meta}</p></div>
      <div class="row-gap">${s.run ? `<a class="btn" href="#/benchmark/${encodeURIComponent(s.run)}">${icons.benchmark} Benchmark run</a>` : ""}</div></div>
    ${v ? `<section class="card" style="margin-bottom:16px"><div class="card-head"><h2>FDB-v3 verdict</h2>
        <div class="right">${v.passed ? `<span class="chip pass">Pass</span>` : `<span class="chip fail">Fail</span>`}${(v.disfluency || []).map((d) => `<span class="chip">${esc(d.toLowerCase().replace(/_/g, " "))}</span>`).join("")}</div></div>
        <div class="card-body stack" style="gap:12px">
          <div class="row-gap small"><span class="soft">Expected calls:</span>${v.expected_tools.map((t) => `<code>${esc(t)}</code>`).join(", ") || "none"}
            <span class="soft" style="margin-left:12px">Made:</span>${v.actual_tools.map((t) => `<code>${esc(t)}</code>`).join(", ") || "none"}
            ${v.unexpected.length ? `<span class="chip fail">unexpected: ${esc(v.unexpected.join(", "))}</span>` : ""}${v.missing.length ? `<span class="chip fail">missing: ${esc(v.missing.join(", "))}</span>` : ""}</div>
          ${argRows ? `<div class="table-wrap"><table class="table"><thead><tr><th>Argument</th><th>Expected</th><th>Given</th><th></th></tr></thead><tbody>${argRows}</tbody></table></div>` : ""}
          ${v.failure ? `<div class="small soft">${esc(v.failure)}</div>` : ""}
        </div></section>` : ""}
    <div class="notice" style="margin-bottom:16px">${icons.sound}<span>${a?.available
      ? `Plays FDB-v3's own recordings: the benchmark's input audio on your side, and what the agent said in the room on its side, lined up with Keel's trace ${a.anchor === "clock" ? "by the wall clock both recorded" : "by the first executed call, which both recorded (within 10 ms)"}.`
      : a ? `No audio: ${esc(a.why)}.` : `No recording for this session on this machine; the bars show when the trace says someone was speaking. Benchmark sessions play with sound while the FDB-v3 checkout (KEEL_FDB_DIR) still holds their audio.`}
      Space plays and pauses; the arrow keys skip 5 s; drag the bar above the controls, or click the timeline or a decision, to jump.</span></div>`;
  const replay = new Replay(view, data);
  const verdictChip = v ? (v.passed ? `<span class="chip pass">Pass</span>` : `<span class="chip fail">Fail</span>`) : "";
  replay.mount(`${verdictChip}${a?.available ? `<span class="chip done hide-sm">${icons.sound.replace("<svg", '<svg width="12" height="12"')} Recorded audio</span>`
    : `<span class="chip hide-sm">${icons.soundOff.replace("<svg", '<svg width="12" height="12"')} No audio</span>`}`, above, title);
  if (at != null) replay.seek(at);
  page = { destroy: () => replay.destroy(), redraw: () => { replay.view.sig = {}; replay.render(); } };
}

// ------------------------------------------------------------------ benchmark
async function renderBenchmark(runId) {
  loading("Benchmark");
  const runs = await api("/api/runs");
  crumbs(["Benchmark"]);
  if (!runs.length) {
    view.innerHTML = `<div class="page-head"><div><h1>Benchmark</h1>
      <p class="lede">No benchmark runs yet. <span class="mono">scripts/reproduce_fdb_v3.sh</span> runs Full-Duplex-Bench v3 against the agent; each run appears here with FDB-v3's own scores.</p></div></div>
      ${copyable("wsl bash scripts/reproduce_fdb_v3_wsl.sh --pipeline gemini_realtime --example travel_10")}`;
    return;
  }
  const chosen = runId || (runs.find((r) => r.complete && (r.scenarios || 0) > 1) || runs.find((r) => r.complete) || runs[0]).id;
  const r = await api(`/api/runs/${encodeURIComponent(chosen)}`);
  crumbs(["Benchmark", "#/benchmark"], [runLabel(r)]);
  const judgeNone = (r.judge || "").startsWith("none");
  const bars = (obj) => Object.entries(obj || {}).filter(([, v]) => v != null).map(([k, v]) => `
    <div class="bar-row"><span>${esc(k.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase()))}</span>
      <div class="bar"><i style="width:${Math.round(v * 100)}%;background:${v >= 0.5 ? "var(--done)" : v > 0 ? "var(--held)" : "var(--dropped)"}"></i></div><span class="num">${pct(v)}%</span></div>`).join("");
  const kpi = (k, v, unit, f) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}${v === "–" ? "" : `<small>${unit}</small>`}</div><div class="f">${f}</div></div>`;
  view.innerHTML = `
    <div class="page-head"><div><h1>Full-Duplex-Bench v3</h1>
      <p class="lede">FDB-v3's own scripts and scores, on its 100 recorded conversations with real disfluencies, for one run of this agent.</p></div>
      <select class="input" id="run" aria-label="Choose a run">${runs.map((x) => `<option value="${esc(x.id)}" ${x.id === chosen ? "selected" : ""}>${esc(`${runLabel(x)} · ${x.provider || "?"}`)}</option>`).join("")}</select></div>
    <section class="card kpis">
      ${kpi("Pass rate, strict", pct(r.pass_rate), "%", `${r.passed ?? "–"} of ${r.scenarios ?? "–"} scenarios`)}
      ${kpi("Tool selection", pct(r.tool_selection), "%", "Right tools, no extras")}
      ${kpi("Arguments", pct(r.argument_accuracy), "%", judgeNone ? "Exact match" : "Judged by the LLM judge")}
      ${kpi("Turn-taking", pct(r.turn_take_rate), "%", "Responded when it should")}
      ${kpi("First response, median", secs(r.latency_median_s), "s", "After the user stopped")}
    </section>
    <div class="stack" style="margin-top:12px;gap:10px">
      ${judgeNone ? `<div class="notice">${icons.info}<span>Scored without FDB-v3's gpt-4o judge (no OpenAI credit), with its rule-based scoring: arguments must match exactly, so a date given as 2026-10-07 where the benchmark expects "October 7" counts as wrong. The organisers' re-run uses the judge.</span></div>` : ""}
      ${r.status === "running" ? `<div class="notice warn">${icons.alert}<span>This run is still going: its scores appear once FDB-v3's evaluation has run.</span></div>` : ""}
      ${r.status === "stopped" ? `<div class="notice warn">${icons.alert}<span>This run stopped before FDB-v3's evaluation, so it has no scores. Its logs (agent.log, inference.log) in <span class="mono">results/fdb_v3/${esc(r.id)}/</span> say why.</span></div>` : ""}
      ${r.status === "invalid" ? `<div class="notice warn">${icons.alert}<span>This run is not a measurement of the agent: ${esc(r.invalid_note || "see NOTE.md in its folder")}</span></div>` : ""}
    </div>
    <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);margin-top:16px">
      <section class="card"><div class="card-head"><h2>By kind of speech</h2></div><div class="card-body bars">${bars(r.by_disfluency) || `<p class="empty">Not reported for this run.</p>`}</div></section>
      <section class="card"><div class="card-head"><h2>By domain</h2></div><div class="card-body bars">${bars(r.by_domain) || `<p class="empty">Not reported for this run.</p>`}</div></section>
    </div>
    <section class="card table-wrap" style="margin-top:16px">
      <div class="card-head"><h2>Scenarios</h2><div class="right"><div class="seg" id="sf">
        ${["all", "fail", "pass"].map((f) => `<button data-f="${f}" class="${f === "all" ? "active" : ""}">${{ all: "All", fail: "Failed", pass: "Passed" }[f]}</button>`).join("")}</div></div></div>
      <table class="table"><thead><tr><th>Scenario</th><th class="hide-sm">Domain</th><th>Result</th><th class="hide-sm">Why it failed</th><th class="num">First response</th></tr></thead>
      <tbody id="scen"></tbody></table></section>
    <section class="card" style="margin-top:16px"><div class="card-head"><h2>Run</h2></div>
      <div class="card-body"><dl class="kv">
        <dt>Command</dt><dd class="mono">${esc(r.command || "–")}</dd>
        <dt>Keel</dt><dd class="mono">${esc(r.keel_commit || "–")}</dd>
        <dt>FDB-v3</dt><dd class="mono">${esc(r.fdb_v3_commit || "–")}</dd>
        <dt>Judge</dt><dd>${esc(r.judge || "gpt-4o (FDB-v3 default)")}</dd>
      </dl></div></section>`;
  const paint = (f) => {
    const rows = (r.scenarios_detail || []).filter((x) => f === "all" || (f === "pass") === !!x.passed);
    document.getElementById("scen").innerHTML = rows.map((x) => `
      <tr class="${x.session ? "link" : ""}" ${x.session ? `data-href="#/replay/${encodeURIComponent(x.session)}" tabindex="0"` : ""}>
        <td><div class="title">${esc(x.title || x.scenario)}</div><div class="sub mono">${esc(x.scenario)}${x.session ? ` · replay ${icons.arrow.replace("<svg", '<svg width="12" height="12" style="display:inline;vertical-align:-2px"')}` : ""}</div></td>
        <td class="hide-sm">${esc((x.domain || "").replace(/_/g, " "))}</td>
        <td>${x.passed ? `<span class="chip pass">Pass</span>` : `<span class="chip fail">Fail</span>`}</td>
        <td class="soft hide-sm">${esc(x.failure || "")}</td>
        <td class="num">${x.latency_s != null ? `${x.latency_s.toFixed(1)} s` : "–"}</td></tr>`).join("") || `<tr><td colspan="5" class="empty">No scenario results${f === "all" ? " yet" : " of this kind"}.</td></tr>`;
    bindRows();
  };
  paint("all");
  document.getElementById("sf").onclick = (e) => {
    const f = e.target.closest("button")?.dataset.f;
    if (!f) return;
    document.querySelectorAll("#sf button").forEach((b) => b.classList.toggle("active", b.dataset.f === f));
    paint(f);
  };
  document.getElementById("run").onchange = (e) => (location.hash = `#/benchmark/${encodeURIComponent(e.target.value)}`);
}

// ------------------------------------------------------------------ setup
// A pipeline's provider: a local server needs no key; a hosted one needs its key set.
function providerChip(name) {
  const p = (info.providers || []).find((x) => x.name === name);
  if (!p) return `<span class="chip">${esc(name)}</span>`;
  if (p.local) return `<span class="chip">${esc(name)}: local server</span>`;
  return `<span class="chip ${p.key_set ? "ok" : "bad"}">${esc(name)}: ${esc(p.key_env)} ${p.key_set ? "set" : "missing"}</span>`;
}

async function renderSetup() {
  loading("Setup");
  info = await api("/api/system");
  renderSideFoot();
  crumbs(["Setup"]);
  const k = info.keys, f = info.fdb;
  const check = (id, ok, title, detail, test) => `
    <div class="check"><span class="ic ${ok === true ? "ok" : ok === false ? "bad" : "warn"}" id="ic-${id}">${ok === true ? icons.check : ok === false ? icons.x : icons.alert}</span>
      <div class="what"><b>${title}</b><div class="d" id="d-${id}">${detail}</div></div>
      ${test ? `<button class="btn btn-sm" data-check="${test}" id="b-${id}">${icons.refresh} Test</button>` : "<span></span>"}</div>`;
  const labels = { read_only: ["done", "Read-only"], state_changing: ["not_sent", "Changes state"], unknown: ["holding", "Unknown, treated as a write"] };
  view.innerHTML = `
    <div class="page-head"><div><h1>Setup</h1>
      <p class="lede">What this machine has: keys (only whether they are set, never their values), the agent profiles in <span class="mono">config/</span>, the FDB-v3 checkout, and each tool with the label Keel's compiler gives it. <b>Test</b> makes one real request and shows the answer.</p></div></div>
    <div class="grid cols-2" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr)">
      <section class="card"><div class="card-head"><h2>Status</h2></div><div class="checks">
        ${check("livekit", k.livekit, "LiveKit Cloud", k.livekit ? "URL, key and secret are set" : "Set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env", k.livekit ? "livekit" : null)}
        ${check("gemini", k.gemini ? null : false, "Gemini API (free tier)", k.gemini ? "GOOGLE_API_KEY is set; Test opens one Gemini Live session" : "Set GOOGLE_API_KEY for the free gemini_realtime pipeline", k.gemini ? "gemini" : null)}
        ${check("openai", k.openai ? null : false, "OpenAI", k.openai ? "OPENAI_API_KEY is set; Test makes a one-token request (needs credit)" : "Not set: the cascaded and gpt_realtime pipelines and the gpt-4o judge need it", k.openai ? "openai" : null)}
        ${check("open", null, "Open pipeline (open-weight models)", "No key needed. Test sends one request to each of its speech-to-text, LLM and text-to-speech endpoints; start them first with <code>scripts/open_models.sh start</code>", "open")}
        ${k.gemini ? check("vision", null, "Show &amp; Fix display reader", "Test reads one generated image with the reader the free gemini_realtime pipeline uses", "vision") : ""}
        ${check("fdb", f.present && f.template_present ? true : false, "FDB-v3 checkout", f.present ? `${esc(f.dir)}<br>commit <span class="mono">${esc((f.commit || "?").slice(0, 12))}</span> · ${f.recordings} recordings · ${f.scenario_results} scenario results` : `Not found at ${esc(f.dir)}. scripts/reproduce_fdb_v3.sh clones it; or set KEEL_FDB_DIR.`, null)}
        ${check("keel", info.keel_commit ? true : null, "This checkout", `Keel commit <span class="mono">${esc((info.keel_commit || "unknown").slice(0, 12))}</span> · Python ${esc(info.python)}`, null)}
      </div></section>
      <section class="card"><div class="card-head"><h2>Run it</h2></div><div class="card-body stack" style="gap:10px">
        <div class="small soft">Benchmark smoke test, free (Gemini Live, rule-based scoring):</div>
        ${copyable("wsl bash scripts/reproduce_fdb_v3_wsl.sh --pipeline gemini_realtime --judge none --example travel_10")}
        <div class="small soft">Same, on open-weight models only (no model key at all):</div>
        ${copyable("wsl bash scripts/reproduce_fdb_v3_wsl.sh --pipeline open --judge none --example travel_10")}
        <div class="small soft">Full benchmark, as the organisers run it (OpenAI keys):</div>
        ${copyable("scripts/reproduce_fdb_v3.sh")}
        <div class="small soft">This app plus an agent, from Windows:</div>
        ${copyable("wsl bash scripts/keel_live_wsl.sh --pipeline gemini_realtime")}
        <div class="small soft">Tests:</div>
        ${copyable("python -m pytest")}
      </div></section>
    </div>
    <section class="card table-wrap" style="margin-top:16px">
      <div class="card-head"><h2>Model providers</h2><span class="muted small">config/keel.toml [providers]: OpenAI-compatible endpoints any stage can use</span></div>
      <table class="table"><thead><tr><th>Name</th><th>Endpoint</th><th>Key</th></tr></thead><tbody>
        ${(info.providers || []).map((p) => `<tr><td><b>${esc(p.name)}</b></td><td class="mono small">${esc(p.base_url)}</td>
          <td>${p.local ? `<span class="chip">local server, no key</span>` : `<span class="chip ${p.key_set ? "ok" : "bad"}">${esc(p.key_env)} ${p.key_set ? "set" : "not set"}</span>`}</td></tr>`).join("")}
      </tbody></table></section>
    ${info.profiles.map((p) => `
      <section class="card" style="margin-top:16px">
        <div class="card-head"><h2>${esc(PROFILES[p.name] || p.name)}</h2><span class="mono muted small">config/${esc(p.name)}.toml</span>
          <div class="right"><span class="chip accent">default pipeline: ${esc(p.pipeline)}</span></div></div>
        <div class="card-body stack">
          <div class="table-wrap"><table class="table"><thead><tr><th>Pipeline</th><th>Models</th><th>Needs</th></tr></thead><tbody>
            ${Object.entries(p.pipelines).map(([name, m]) => `<tr><td><b>${esc(name)}</b><div class="sub">${esc(PIPELINES[name] || "")}</div></td>
              <td class="mono small">${Object.entries(m).filter(([kk]) => kk !== "providers").map(([kk, vv]) => `${esc(kk)}: ${esc(vv)}`).join("<br>")}</td>
              <td><div class="row-gap">${(m.providers || []).map(providerChip).join("")}</div></td></tr>`).join("")}
          </tbody></table></div>
          <dl class="kv">
            <dt>Quiet after your turn</dt><dd>${p.fence.quiet_ms} ms before a held call may run</dd>
            <dt>Reads wait too</dt><dd>${p.fence.hold_reads ? "yes: every call waits behind the fence" : "no: read-only calls start at once; only writes wait"}</dd>
            <dt>Announced correction</dt><dd>${p.fence.repair_wait_ms ? `after a turn of only editing terms ("oh, wait"), calls wait up to ${p.fence.repair_wait_ms} ms for the correction` : "off"}</dd>
            <dt>Words still coming</dt><dd>${p.fence.transcript_wait_ms ? `calls wait for the transcript of speech that has ended, up to ${p.fence.transcript_wait_ms} ms (cascades only)` : "off"}</dd>
            <dt>Keel may say</dt><dd>${p.speak_purposes.length ? esc(p.speak_purposes.join(", ")) : "nothing"}</dd>
            ${p.vision_model ? `<dt>Reads the display with</dt><dd class="mono">${esc(p.vision_model)}</dd>` : ""}
            <dt>Traces</dt><dd class="mono">${esc(p.trace_dir)}/</dd>
          </dl>
          <div>
            <div class="eyebrow" style="margin-bottom:8px">Tools, and how Keel treats them</div>
            ${p.tools.length ? `<div class="table-wrap"><table class="table"><thead><tr><th>Tool</th><th>Keel's label</th><th class="hide-sm">Decided by</th><th class="hide-sm">Parameters</th></tr></thead><tbody>
              ${p.tools.map((t) => `<tr><td><span class="mono" style="font-weight:600">${esc(t.name)}</span><div class="sub">${esc(t.description)}</div></td>
                <td><span class="chip ${labels[t.safety]?.[0] || ""}">${esc(labels[t.safety]?.[1] || t.safety)}</span></td>
                <td class="hide-sm small" title="${esc(t.evidence.join("\n"))}">${esc({ hint: "the tool's own hint", classifier: `classifier (${t.confidence})`, fallback: "no confident answer" }[t.method] || t.method)}</td>
                <td class="hide-sm mono small">${esc(t.parameters.join(", "))}</td></tr>`).join("")}
            </tbody></table></div>` : `<p class="soft small">${esc(p.tools_note || "No tools.")}</p>`}
          </div>
        </div>
      </section>`).join("")}`;
  view.querySelectorAll("[data-check]").forEach((b) => {
    b.onclick = async () => {
      const name = b.dataset.check;
      b.disabled = true; b.innerHTML = `${icons.refresh} Testing…`;
      try {
        const res = await api(`/api/check/${name}${name === "vision" ? "?pipeline=gemini_realtime" : ""}`, { method: "POST" });
        document.getElementById(`d-${name}`).innerHTML = `${esc(res.detail)}${res.ms != null ? ` <span class="muted">(${res.ms} ms)</span>` : ""}`;
        const ic = document.getElementById(`ic-${name}`);
        ic.className = `ic ${res.ok ? "ok" : "bad"}`;
        ic.innerHTML = res.ok ? icons.check : icons.x;
      } catch (e) {
        document.getElementById(`d-${name}`).textContent = e.message;
      }
      b.disabled = false; b.innerHTML = `${icons.refresh} Test again`;
    };
  });
}

// ------------------------------------------------------------------ start
themeButton();
// Canvas and SVG colours are read from the theme when drawn: redraw on a change.
window.addEventListener("themechange", () => page?.redraw?.());
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => page?.redraw?.());
api("/api/system").then((s) => { info = s; renderSideFoot(); }).catch(() => {});
route();
