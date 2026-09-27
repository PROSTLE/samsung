// The conversation console: transcript, tool calls, Keel's decision log, the turn
// timeline and the voice visualiser. One view for both a live conversation and a
// replay: each passes the same kind of events (keel/web/story.py) and a time `now`.

import { argText, chip, clock, esc, icons, keelMark, STATE } from "./ui.js";

// ------------------------------------------------------------------ model
// What the console shows at time `now` (ms), from story events. Nothing here is
// estimated: an action's state at `now` is the last history entry at or before it.
export function derive(events, now) {
  const transcript = [], states = [], actions = [];
  for (const e of events) {
    if (e.kind === "action") {
      const all = e.history || [];
      const hist = all.filter((h) => h.t <= now);
      if (!hist.length) continue;
      const last = hist[hist.length - 1];
      const complete = hist.length === all.length;
      actions.push({ ...e, history: hist, state: last.state, status: last.status, complete,
                     reason: complete && last.state !== "holding" && last.state !== "planned" ? e.reason : null });
    } else if (e.t <= now) {
      if (e.kind === "state") states.push(e);
      else if (e.kind === "user" || e.kind === "agent" || e.kind === "keel" || e.kind === "note") transcript.push(e);
    }
  }
  actions.sort((a, b) => a.history[0].t - b.history[0].t);
  const decisions = [];
  actions.forEach((a, i) => a.history.forEach((h, j) => {
    const final = a.complete && j === a.history.length - 1;
    decisions.push({ t: h.t, n: i + 1, a, state: h.state, status: h.status, reason: final ? a.reason : null });
  }));
  decisions.sort((x, y) => x.t - y.t || x.n - y.n);
  const lastState = (who) => { for (let i = states.length - 1; i >= 0; i--) if (states[i].who === who) return states[i].state; return null; };
  return { now, transcript, states, actions, decisions, user: lastState("user"), agent: lastState("agent") };
}

function spans(states, who, now, on) {
  const out = [];
  let cur = null;
  for (const s of states) {
    if (s.who !== who) continue;
    if (cur && cur.state !== s.state) { cur.t1 = s.t; out.push(cur); cur = null; }
    if (!cur && on(s.state)) cur = { t0: s.t, state: s.state };
  }
  if (cur) { cur.t1 = now; cur.open = true; out.push(cur); }
  return out;
}

// What Keel was doing over time: holding while any planned call waits, executing
// while any call has gone out and not returned.
function keelSegments(actions, now) {
  const times = [...new Set(actions.flatMap((a) => a.history.map((h) => h.t)))].sort((a, b) => a - b);
  const stateAt = (a, t) => { let s = null; for (const h of a.history) if (h.t <= t) s = h.state; return s; };
  const segs = [];
  times.forEach((t, i) => {
    const st = actions.map((a) => stateAt(a, t));
    const kind = st.some((s) => s === "holding" || s === "planned") ? "holding" : st.includes("sent") ? "running" : null;
    const t1 = i + 1 < times.length ? times[i + 1] : now;
    if (!kind || t1 <= t) return;
    const prev = segs[segs.length - 1];
    if (prev && prev.kind === kind && prev.t1 === t) prev.t1 = t1; else segs.push({ t0: t, t1, kind });
  });
  return segs;
}

// Interval packing: parallel calls get their own row.
function packRows(actions, now) {
  const rows = [];
  return actions.map((a) => {
    const t0 = a.history[0].t, t1 = a.complete ? a.history[a.history.length - 1].t : now;
    let r = rows.findIndex((end) => end < t0 - 50);
    if (r < 0) { r = rows.length; rows.push(t1); } else rows[r] = t1;
    return { a, t0, t1, row: r };
  });
}

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

// ------------------------------------------------------------------ view
export class ConsoleView {
  // opts: { mode: "live" | "replay", onSeek(t) }
  constructor(root, opts) {
    this.root = root;
    this.opts = opts;
    this.filter = "all";
    this.zoom = opts.mode === "live" ? "follow" : "fit";
    this.open = new Set();
    this.sig = {};
    this.model = null;
    this.domain = [0, 30000];
    this.envelopes = null;
  }

  mount({ title, status = "", headRight = "", controls = "", above = "" }) {
    this.root.innerHTML = `
      ${above}
      <div class="console">
        <section class="card fixed c-stage">
          <div class="card-head"><h2>${title}</h2><span id="c-status">${status}</span><div class="right">${headRight}</div></div>
          <div class="stage">
            <div class="stage-status" id="c-speaking"></div>
            <div class="wave"><canvas id="c-wave"></canvas><div class="overlay" id="c-wave-note"></div></div>
            <div class="controls" id="c-controls">${controls}</div>
          </div>
        </section>
        <section class="card fixed c-transcript">
          <div class="card-head"><h2>Transcript</h2><div class="right" id="c-tr-right"></div></div>
          <div class="scroll" id="c-tr"><div class="transcript" id="c-tr-list"></div></div>
        </section>
        <section class="card fixed c-tools">
          <div class="card-head"><h2>Tool calls</h2><div class="right muted small" id="c-tools-n"></div></div>
          <div class="scroll"><div class="tools" id="c-tools"></div></div>
        </section>
        <section class="card fixed2 row2-left c-log">
          <div class="card-head"><h2>Keel decision log</h2>
            <div class="right"><div class="seg" id="c-filter" role="tablist" aria-label="Filter decisions"></div></div></div>
          <div class="scroll" id="c-log-scroll"><div class="log" id="c-log"></div></div>
        </section>
        <section class="card fixed2 row2-right c-timeline">
          <div class="card-head"><h2>Turn timeline</h2>
            <div class="right"><div class="seg" id="c-zoom" aria-label="Timeline zoom">
              <button data-z="fit">Whole conversation</button><button data-z="follow">Follow</button></div></div></div>
          <div class="timeline-wrap" id="c-tl"></div>
          <div class="tl-legend">
            <span><i style="background:var(--user)"></i>You</span>
            <span><i style="background:var(--agent)"></i>Agent speaking</span>
            <span><i style="background:var(--agent-soft);border:1px solid var(--agent)"></i>Agent thinking</span>
            <span><i style="background:var(--held)"></i>Held</span>
            <span><i style="background:var(--running)"></i>Running</span>
            <span><i style="background:var(--done)"></i>Executed</span>
            <span><i style="background:var(--dropped)"></i>Dropped</span>
          </div>
        </section>
      </div>`;
    const $ = (id) => this.root.querySelector(`#${id}`);
    this.$ = $;
    $("c-zoom").onclick = (e) => {
      const z = e.target.closest("button")?.dataset.z;
      if (z) { this.zoom = z; this.renderTimeline(true); }
    };
    $("c-filter").onclick = (e) => {
      const f = e.target.closest("button")?.dataset.f;
      if (f) { this.filter = f; this.sig.log = null; this.renderLog(); }
    };
    $("c-log").onclick = (e) => {
      const row = e.target.closest(".log-row");
      if (!row) return;
      this.focusTool(row.dataset.id);
      if (this.opts.onSeek) this.opts.onSeek(Number(row.dataset.t));
    };
    $("c-tools").onclick = (e) => {
      const card = e.target.closest(".tool");
      if (!card) return;
      const id = card.dataset.id;
      this.open.has(id) ? this.open.delete(id) : this.open.add(id);
      card.classList.toggle("open", this.open.has(id));
    };
    $("c-tl").onclick = (e) => {
      if (!this.opts.onSeek || !this._x) return;
      const svg = $("c-tl").querySelector("svg");
      const r = svg.getBoundingClientRect();
      const t = this._x.inv(e.clientX - r.left);
      if (t != null) this.opts.onSeek(t);
    };
    this._ro = new ResizeObserver(() => { this.renderTimeline(true); });
    this._ro.observe($("c-tl"));
  }

  destroy() { this._ro?.disconnect(); }

  setStatus(html) { const el = this.$("c-status"); if (el && el.innerHTML !== html) el.innerHTML = html; }
  setWaveNote(text) { const el = this.$("c-wave-note"); if (el) el.textContent = text || ""; }

  // domain: [t0, t1] of the whole conversation, in the same ms as the events.
  update(model, domain) {
    this.model = model;
    if (domain) this.domain = domain;
    this.renderSpeaking();
    this.renderTranscript();
    this.renderTools();
    this.renderLog();
    this.renderTimeline();
  }

  focusTool(id) {
    this.root.querySelectorAll(".tool").forEach((c) => c.classList.toggle("focus", c.dataset.id === id));
    const card = this.root.querySelector(`.tool[data-id="${CSS.escape(id)}"]`);
    card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  renderSpeaking() {
    const m = this.model;
    const agentWord = { speaking: "speaking", thinking: "thinking", listening: "listening", initializing: "starting" }[m.agent];
    const parts = [];
    if (m.user === "speaking") parts.push(`<span class="chip accent"><span class="dot"></span>You are speaking</span>`);
    if (agentWord) parts.push(`<span class="chip" style="background:var(--agent-soft);color:var(--agent)"><span class="dot"></span>Agent ${agentWord}</span>`);
    const holding = m.actions.filter((a) => a.state === "holding" || a.state === "planned").length;
    const running = m.actions.filter((a) => a.state === "sent").length;
    if (holding) parts.push(`<span class="chip holding">${keelMark} Keel is holding ${holding} call${holding === 1 ? "" : "s"}</span>`);
    else if (running) parts.push(`<span class="chip sent">${keelMark} ${running} call${running === 1 ? "" : "s"} running</span>`);
    const html = parts.join("") || `<span class="muted">Waiting for speech</span>`;
    const el = this.$("c-speaking");
    if (el.innerHTML !== html) el.innerHTML = html;
  }

  renderTranscript() {
    const items = this.model.transcript;
    const last = items[items.length - 1];
    const sig = `${items.length}|${last ? last.text.length : 0}|${last?.interim ? 1 : 0}`;
    if (sig === this.sig.tr) return;
    this.sig.tr = sig;
    const scroller = this.$("c-tr");
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60;
    const html = items.map((e) => {
      if (e.kind === "note") return `<div class="note-row">${icons.info}<span>${esc(e.text)}</span></div>`;
      const who = { user: ["You", icons.user], agent: ["Agent", icons.bot], keel: ["Keel", keelMark] }[e.kind];
      return `<div class="msg ${e.kind}"><div class="avatar">${who[1]}</div><div>
        <div class="meta"><b>${who[0]}</b><span>${clock(e.t)}</span>${e.kind === "keel" ? `<span>said while a call waited</span>` : ""}</div>
        <div class="bubble ${e.interim ? "interim" : ""}">${esc(e.text)}</div></div></div>`;
    }).join("");
    this.$("c-tr-list").innerHTML = html || `<p class="empty">${this.opts.mode === "live" ? "Start speaking once the agent has joined. What you say and what it answers appear here." : "Nothing has been said yet at this point of the replay."}</p>`;
    if (atBottom || this.opts.mode === "replay") scroller.scrollTop = scroller.scrollHeight;
  }

  renderTools() {
    const acts = this.model.actions;
    const sig = acts.map((a) => `${a.id}:${a.state}:${a.status}:${a.reason}`).join("|");
    if (sig === this.sig.tools) return;
    this.sig.tools = sig;
    this.$("c-tools-n").textContent = acts.length ? `${acts.length} planned` : "";
    this.$("c-tools").innerHTML = acts.map((a, i) => {
      const hist = a.history.map((h) => `<span><b>${esc((STATE[h.state] || {}).label || h.state)}</b> ${clock(h.t, true)}</span>`).join("");
      const line = a.reason || a.status;
      return `<article class="tool ${this.open.has(a.id) ? "open" : ""}" data-id="${esc(a.id)}" title="Click for its history">
        <div class="num">${i + 1}</div>
        <div>
          <div class="top"><span class="name">${esc(a.tool)}</span>${a.state === "sent" ? `<span class="dots"><i></i><i></i><i></i></span>` : ""}${chip(a.state)}</div>
          ${a.args?.length ? `<div class="args">${a.args.map(([k, v]) => `${esc(k.toLowerCase())}: <b>${esc(v)}</b>`).join(", ")}</div>` : ""}
          ${line ? `<div class="why">${esc(line)}</div>` : ""}
          <div class="hist">${hist}</div>
        </div></article>`;
    }).join("") || `<p class="empty">Every call the model plans appears here, with what Keel did with it: held while you were still talking, run once you had finished, or dropped because you changed your mind.</p>`;
  }

  renderLog() {
    const all = this.model.decisions;
    const groups = { all: () => true, holding: (d) => d.state === "holding", not_sent: (d) => d.state === "not_sent",
                     done: (d) => d.state === "done" || d.state === "reused" };
    const counts = Object.fromEntries(Object.entries(groups).map(([k, f]) => [k, all.filter(f).length]));
    const fsig = `${this.filter}|${Object.values(counts).join(",")}`;
    if (fsig !== this.sig.filter) {
      this.sig.filter = fsig;
      const names = { all: "All", holding: "Held", not_sent: "Dropped", done: "Executed" };
      this.$("c-filter").innerHTML = Object.keys(groups).map((k) =>
        `<button data-f="${k}" class="${k === this.filter ? "active" : ""}">${names[k]}<span class="n">${counts[k]}</span></button>`).join("");
    }
    const shown = all.filter(groups[this.filter]);
    const sig = `${this.filter}|${shown.length}|${shown.map((d) => d.state + d.status).join("")}`;
    if (sig === this.sig.log) return;
    this.sig.log = sig;
    const look = {
      planned: ["planned", icons.plan], holding: ["holding", icons.hand], sent: ["sent", icons.send],
      done: ["done", icons.check], failed: ["failed", icons.x], not_sent: ["not_sent", icons.x], reused: ["reused", icons.repeat],
    };
    const call = (d) => `<code>${esc(d.a.tool)}(${esc(argText(d.a.args))})</code>`;
    const text = (d) => ({
      planned: () => `Planned ${call(d)}`,
      holding: () => `Held: ${esc(d.status.replace(/^Waiting:?\s*/i, "").replace(/^for /, "waiting for "))}`,
      sent: () => `Sent <code>${esc(d.a.tool)}</code>`,
      done: () => `Executed <code>${esc(d.a.tool)}</code>${d.reason ? ` <span class="why">→ ${esc(d.reason)}</span>` : ""}`,
      failed: () => `Failed <code>${esc(d.a.tool)}</code>${d.reason ? ` <span class="why">${esc(d.reason)}</span>` : ""}`,
      not_sent: () => `Dropped ${call(d)}${d.reason ? ` <span class="why">${esc(d.reason)}</span>` : ""}`,
      reused: () => `Reused the earlier result of <code>${esc(d.a.tool)}</code>`,
    }[d.state] || (() => esc(d.status)))();
    const scroller = this.$("c-log-scroll");
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
    this.$("c-log").innerHTML = shown.map((d) => {
      const [cls, ic] = look[d.state] || ["planned", icons.plan];
      return `<div class="log-row" data-id="${esc(d.a.id)}" data-t="${d.t}" title="Jump to ${clock(d.t, true)}">
        <span class="t">${clock(d.t, true)}</span>
        <span class="ic chip ${cls}" style="padding:0;height:22px;width:22px;justify-content:center">${ic}</span>
        <span class="tx">${text(d)}</span></div>`;
    }).join("") || `<p class="empty">${all.length ? "Nothing of this kind yet." : "Keel's decisions on each call appear here as they happen."}</p>`;
    if (atBottom) scroller.scrollTop = scroller.scrollHeight;
  }

  // Levels from recorded audio, for the timeline lanes (replay only).
  setEnvelopes(env) { this.envelopes = env; this.renderTimeline(true); }

  renderTimeline(force = false) {
    const m = this.model;
    const host = this.$("c-tl");
    if (!m || !host) return;
    const now = m.now;
    const key = `${Math.round(now / 40)}|${this.zoom}|${host.clientWidth}|${m.decisions.length}|${m.states.length}`;
    if (!force && key === this.sig.tl) return;
    this.sig.tl = key;
    const W = Math.max(320, host.clientWidth - 0), H = Math.max(180, host.clientHeight);
    let [t0, t1] = this.domain;
    if (this.zoom === "follow") { t1 = Math.max(now + 4000, t0 + 30000); t0 = Math.max(this.domain[0], t1 - 30000); }
    if (t1 - t0 < 1000) t1 = t0 + 1000;
    const left = 78, right = 10, top = 14, axis = 22;
    const packed = packRows(m.actions, now);
    const toolRows = Math.max(1, ...packed.map((p) => p.row + 1));
    const laneH = 30, toolH = Math.max(laneH, toolRows * 14 + 12);
    const lanes = [["You", laneH], ["Agent", laneH], ["Tool calls", toolH], ["Keel", laneH]];
    const avail = H - top - axis;
    const scale = Math.min(1.25, avail / lanes.reduce((s, [, h]) => s + h, 0));
    let y = top;
    const Y = {};
    for (const [name, h] of lanes) { Y[name] = { y, h: h * scale }; y += h * scale; }
    const x = (t) => left + ((W - left - right) * (t - t0)) / (t1 - t0);
    this._x = { inv: (px) => (px < left - 4 ? null : Math.max(this.domain[0], Math.min(this.domain[1], t0 + ((px - left) * (t1 - t0)) / (W - left - right)))) };
    const clampW = (a, b) => Math.max(2, x(b) - x(a));
    const c = { user: css("--user"), agent: css("--agent"), agentSoft: css("--agent-soft"), held: css("--held"), run: css("--running"),
                done: css("--done"), drop: css("--dropped"), reused: css("--reused"), grid: css("--border"), ink3: css("--ink-3"), ink2: css("--ink-2"), keelSoft: css("--held-bg"), runSoft: css("--running-bg") };
    let g = "";
    // grid + axis
    const span = t1 - t0;
    const step = [1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000].find((s) => span / s <= 8) || 300000;
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      g += `<line x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${y}" stroke="${c.grid}"/>`;
      g += `<text x="${x(t)}" y="${y + 15}" text-anchor="middle" fill="${c.ink3}" font-size="11">${clock(t)}</text>`;
    }
    for (const [name] of lanes) {
      const l = Y[name];
      g += `<text x="0" y="${l.y + l.h / 2 + 4}" fill="${c.ink2}" font-size="12" font-weight="500">${name}</text>`;
      g += `<line x1="${left}" x2="${W - right}" y1="${l.y + l.h}" y2="${l.y + l.h}" stroke="${c.grid}" stroke-dasharray="2 3"/>`;
    }
    const env = this.envelopes;
    const wavePath = (arr, lane) => {
      if (!arr) return "";
      const mid = lane.y + lane.h / 2, amp = lane.h / 2 - 3;
      const up = [], down = [];
      for (let px = left; px <= W - right; px += 2) {
        const ta = t0 + ((px - left) * span) / (W - left - right), tb = ta + (2 * span) / (W - left - right);
        if (ta > now) break;
        // The column's mean level: its loudest sample would merge running speech into a block.
        const i0 = Math.floor((ta - env.offset) / env.step), i1 = Math.max(i0 + 1, Math.floor((tb - env.offset) / env.step));
        let v = 0, k = 0;
        for (let i = Math.max(0, i0); i < Math.min(arr.length, i1); i++) { v += arr[i]; k++; }
        v = k ? (v / k) * 0.9 : 0;
        up.push(`${px},${(mid - v * amp).toFixed(1)}`);
        down.unshift(`${px},${(mid + v * amp).toFixed(1)}`);
      }
      return up.length ? `M${up.join("L")}L${down.join("L")}Z` : "";
    };
    // You
    const lu = Y["You"];
    if (env?.user) g += `<path d="${wavePath(env.user, lu)}" fill="${c.user}" opacity="0.85"/>`;
    for (const s of spans(m.states, "user", now, (st) => st === "speaking")) {
      g += `<rect x="${x(s.t0)}" y="${lu.y + (env?.user ? lu.h - 5 : 7)}" width="${clampW(s.t0, s.t1)}" height="${env?.user ? 3 : lu.h - 14}" rx="${env?.user ? 1.5 : 5}" fill="${c.user}" opacity="${env?.user ? 0.5 : 0.9}"><title>You were speaking ${clock(s.t0, true)}–${clock(s.t1, true)}</title></rect>`;
    }
    for (const e of m.transcript.filter((e) => e.kind === "user")) {
      g += `<circle cx="${x(e.t)}" cy="${lu.y + 5}" r="3" fill="${c.user}"><title>${esc(e.text)}</title></circle>`;
    }
    // Agent
    const la = Y["Agent"];
    if (env?.agent) g += `<path d="${wavePath(env.agent, la)}" fill="${c.agent}" opacity="0.85"/>`;
    for (const s of spans(m.states, "agent", now, (st) => st === "speaking" || st === "thinking")) {
      const sp = s.state === "speaking";
      g += `<rect x="${x(s.t0)}" y="${la.y + (env?.agent ? la.h - 5 : 7)}" width="${clampW(s.t0, s.t1)}" height="${env?.agent ? 3 : la.h - 14}" rx="${env?.agent ? 1.5 : 5}" fill="${sp ? c.agent : c.agentSoft}" stroke="${sp ? "none" : c.agent}" stroke-width="${sp ? 0 : 1}" opacity="${env?.agent ? 0.5 : 1}"><title>Agent ${s.state} ${clock(s.t0, true)}–${clock(s.t1, true)}</title></rect>`;
    }
    for (const e of m.transcript.filter((e) => e.kind === "agent" || e.kind === "keel")) {
      g += `<circle cx="${x(e.t)}" cy="${la.y + 5}" r="3" fill="${e.kind === "keel" ? c.held : c.agent}"><title>${esc(e.text)}</title></circle>`;
    }
    // Tool calls
    const lt = Y["Tool calls"];
    const rowH = Math.min(14 * scale, (lt.h - 8) / toolRows);
    for (const p of packed) {
      const a = p.a, h = a.history, yy = lt.y + 5 + p.row * rowH, bh = Math.max(6, rowH - 5);
      const tSent = h.find((s) => s.state === "sent")?.t;
      const tEnd = a.complete ? h[h.length - 1].t : now;
      const name = `${a.tool}(${argText(a.args)})`;
      const holdEnd = tSent ?? tEnd;
      g += `<rect x="${x(p.t0)}" y="${yy}" width="${clampW(p.t0, holdEnd)}" height="${bh}" rx="${bh / 2}" fill="${c.held}"><title>${esc(name)}: held ${clock(p.t0, true)}–${clock(holdEnd, true)}</title></rect>`;
      if (tSent != null) g += `<rect x="${x(tSent)}" y="${yy}" width="${clampW(tSent, tEnd)}" height="${bh}" rx="${bh / 2}" fill="${c.run}"><title>${esc(name)}: running</title></rect>`;
      const endColor = { done: c.done, not_sent: c.drop, failed: c.drop, reused: c.reused }[a.state];
      if (a.complete && endColor) {
        const cx = x(tEnd), cy = yy + bh / 2;
        g += a.state === "not_sent" || a.state === "failed"
          ? `<g stroke="${endColor}" stroke-width="2.2" stroke-linecap="round"><line x1="${cx - 4}" y1="${cy - 4}" x2="${cx + 4}" y2="${cy + 4}"/><line x1="${cx + 4}" y1="${cy - 4}" x2="${cx - 4}" y2="${cy + 4}"/><title>${esc(name)}: ${esc((STATE[a.state] || {}).label)}</title></g>`
          : `<circle cx="${cx}" cy="${cy}" r="${Math.max(3.5, bh / 2 + 1)}" fill="${endColor}"><title>${esc(name)}: ${esc((STATE[a.state] || {}).label)}${a.reason ? ` (${esc(a.reason)})` : ""}</title></circle>`;
      }
    }
    // Keel
    const lk = Y["Keel"];
    for (const s of keelSegments(m.actions, now)) {
      const w = clampW(s.t0, s.t1), label = s.kind === "holding" ? "Holding" : "Executing";
      g += `<rect x="${x(s.t0)}" y="${lk.y + 5}" width="${w}" height="${lk.h - 10}" rx="6" fill="${s.kind === "holding" ? c.keelSoft : c.runSoft}" stroke="${s.kind === "holding" ? c.held : c.run}" stroke-width="1"><title>Keel ${label.toLowerCase()} ${clock(s.t0, true)}–${clock(s.t1, true)}</title></rect>`;
      if (w > 58) g += `<text x="${x(s.t0) + 7}" y="${lk.y + lk.h / 2 + 4}" font-size="11" font-weight="600" fill="${s.kind === "holding" ? c.held : c.run}">${label}</text>`;
    }
    // playhead
    if (now >= t0 && now <= t1) {
      g += `<line x1="${x(now)}" x2="${x(now)}" y1="${top - 8}" y2="${y}" stroke="${css("--ink")}" stroke-width="1.5"/>`;
      g += `<circle cx="${x(now)}" cy="${top - 8}" r="3.5" fill="${css("--ink")}"/>`;
    }
    host.innerHTML = `<svg viewBox="0 0 ${W} ${y + axis}" width="${W}" height="${y + axis}" role="img" aria-label="Turn timeline">${g}</svg>`;
  }
}

// ------------------------------------------------------------------ visualiser
// bars: [{u, a}] levels 0..1, oldest first. Mirrored around the centre line,
// coloured by who is louder.
export function drawWave(canvas, bars) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const user = css("--user"), agent = css("--agent"), base = css("--border-strong");
  const n = bars.length, gap = 3, bw = Math.max(2, (w - gap * (n - 1)) / n), mid = h / 2;
  ctx.fillStyle = base;
  ctx.fillRect(0, mid - 0.5, w, 1);
  for (let i = 0; i < n; i++) {
    const { u = 0, a = 0 } = bars[i] || {};
    const v = Math.max(u, a);
    if (v < 0.015) continue;
    const bh = Math.max(3, v * (h - 16));
    ctx.globalAlpha = 0.35 + 0.65 * (i / n);
    ctx.fillStyle = u >= a ? user : agent;
    const x = i * (bw + gap);
    const r = Math.min(bw / 2, 3);
    roundRect(ctx, x, mid - bh / 2, bw, bh, r);
  }
  ctx.globalAlpha = 1;
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.fill();
}
