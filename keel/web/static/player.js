// Replay of a recorded session in the conversation console. Time is the trace's
// own (ms since the agent's session started). When FDB-v3 still holds the
// scenario's recordings, the user's audio and the agent's recorded reply play in
// step, lined up by the offset the server derived (keel/web/results.py).

import { ConsoleView, derive, drawWave } from "./console.js";
import { clock, esc, icons } from "./ui.js";

const WINDOW_MS = 6000;   // the visualiser shows the last 6 s
const BARS = 90;
const STEP_MS = 20;       // envelope resolution

// Where the conversation is: from shortly before the first speech (or the start
// of the recording) to shortly after the last thing that happened.
export function replayDomain(events, audio, durations = {}) {
  const times = [], speech = [];
  for (const e of events) {
    if (e.kind === "action") (e.history || []).forEach((h) => times.push(h.t));
    else if (e.kind !== "config") times.push(e.t);
    if (e.kind === "user" || e.kind === "agent" || (e.kind === "state" && e.who === "user" && e.state === "speaking")) speech.push(e.t);
  }
  let start = speech.length ? Math.min(...speech) : times.length ? Math.min(...times) : 0;
  let end = times.length ? Math.max(...times) : 1000;
  if (audio?.available) {
    start = Math.min(start, audio.offset_ms);
    // Recordings run on in silence after the conversation; do not stretch to them.
  }
  start = Math.max(0, start - 1500);
  end = Math.max(end + 2500, start + 5000);
  return [start, end];
}

async function envelope(url) {
  const buf = await (await fetch(url)).arrayBuffer();
  const ctx = new OfflineAudioContext(1, 1, 16000);
  const audio = await ctx.decodeAudioData(buf);
  const data = audio.getChannelData(0), per = Math.round((audio.sampleRate * STEP_MS) / 1000);
  const n = Math.floor(data.length / per), rms = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let j = i * per; j < (i + 1) * per; j++) s += data[j] * data[j];
    rms[i] = Math.sqrt(s / per);
  }
  // Normalise to the loud end of this recording (98th percentile of frames above silence).
  const loud = Array.from(rms).filter((v) => v > 0.005).sort((a, b) => a - b);
  const ref = loud.length ? loud[Math.floor(loud.length * 0.98)] : 1;
  for (let i = 0; i < n; i++) rms[i] = Math.min(1, Math.pow(rms[i] / ref, 0.75));
  return { levels: rms, duration: audio.duration * 1000 };
}

export class Replay {
  constructor(root, data, { onEnd } = {}) {
    this.data = data;
    this.events = data.events;
    this.audio = data.audio && data.audio.available ? data.audio : null;
    this.domain = replayDomain(this.events, this.audio);
    this.now = this.domain[0];
    this.speed = 1;
    this.playing = false;
    this.sound = true;
    this.env = null;
    this.els = {};
    this.onEnd = onEnd;
    this.view = new ConsoleView(root, { mode: "replay", onSeek: (t) => this.seek(t) });
  }

  // title: the session's; headHtml: chips beside it; aboveHtml: the session's
  // details (verdict, audio note), shown in the drawer's Session tab.
  mount(headHtml, aboveHtml, title = "Replay") {
    this.view.mount({
      title: esc(title), headRight: headHtml,
      side: aboveHtml ? { label: "Session", html: aboveHtml } : null,
      dockTop: `<div class="scrub-row"><input type="range" class="scrub" id="r-scrub" min="0" max="1000" step="1" value="0" aria-label="Position in the conversation"></div>`,
      dockLeft: `
        <button class="rb sm" id="r-back" aria-label="Back 5 seconds" title="Back 5 s">${icons.back}</button>
        <button class="rb primary" id="r-play" aria-label="Play" title="Play (Space)">${icons.play}</button>
        <button class="rb sm" id="r-fwd" aria-label="Forward 5 seconds" title="Forward 5 s">${icons.fwd}</button>`,
      dockRight: `
        <span class="clock hide-sm" id="r-clock"></span>
        <div class="speed hide-sm" id="r-speed" aria-label="Speed">${[1, 1.5, 2].map((s) => `<button data-s="${s}" class="${s === 1 ? "active" : ""}">${s}×</button>`).join("")}</div>
        <button class="rb sm" id="r-sound" aria-label="Sound" title="Sound">${icons.sound}</button>`,
    });
    const $ = (id) => document.getElementById(id);
    $("r-play").onclick = () => (this.playing ? this.pause() : this.play());
    $("r-back").onclick = () => this.seek(this.now - 5000);
    $("r-fwd").onclick = () => this.seek(this.now + 5000);
    $("r-sound").onclick = () => this.toggleSound();
    $("r-speed").onclick = (e) => { const s = Number(e.target.closest("button")?.dataset.s); if (s) this.setSpeed(s); };
    const scrub = $("r-scrub");
    scrub.oninput = () => this.seek(this.domain[0] + (Number(scrub.value) / 1000) * (this.domain[1] - this.domain[0]));
    scrub.onpointerdown = () => { scrub.dataset.drag = "1"; };
    scrub.onpointerup = scrub.onpointercancel = () => { scrub.dataset.drag = ""; };
    if (!this.audio) { $("r-sound").disabled = true; $("r-sound").title = "No recorded audio"; }
    this._keys = (e) => {
      if (e.target.closest("input, select, textarea, button")) return;
      if (e.code === "Space") { e.preventDefault(); this.playing ? this.pause() : this.play(); }
      if (e.code === "ArrowLeft") this.seek(this.now - 5000);
      if (e.code === "ArrowRight") this.seek(this.now + 5000);
    };
    document.addEventListener("keydown", this._keys);
    this.render();
    if (this.audio) this.loadAudio();
    else this.view.setWaveNote("");
  }

  async loadAudio() {
    const a = this.audio;
    for (const which of ["user", "agent"]) {
      if (!a[which]) continue;
      const el = new Audio(a[which]);
      el.preload = "auto";
      el.preservesPitch = true;
      this.els[which] = el;
    }
    try {
      const [u, g] = await Promise.all([a.user ? envelope(a.user) : null, a.agent ? envelope(a.agent) : null]);
      this.env = { user: u?.levels || null, agent: g?.levels || null, offset: a.offset_ms, step: STEP_MS };
      this.view.setEnvelopes(this.env);
      this.render();
    } catch (e) {
      console.warn("recording not decoded", e);
      this.view.setWaveNote("The recording could not be decoded in this browser.");
    }
  }

  destroy() {
    this.pause();
    cancelAnimationFrame(this._raf);
    document.removeEventListener("keydown", this._keys);
    Object.values(this.els).forEach((el) => { el.pause(); el.src = ""; });
    this.view.destroy();
  }

  // ---------------------------------------------------------------- controls
  play() {
    if (this.now >= this.domain[1] - 50) this.now = this.domain[0];
    this.playing = true;
    this._last = performance.now();
    this._syncButtons();
    this._loop();
  }

  pause() {
    this.playing = false;
    Object.values(this.els).forEach((el) => el.pause());
    this._syncButtons();
    this.render();
  }

  seek(t) {
    this.now = Math.max(this.domain[0], Math.min(this.domain[1], t));
    Object.values(this.els).forEach((el) => { el.dataset.seek = "1"; });
    this.render();
  }

  setSpeed(s) {
    this.speed = s;
    document.querySelectorAll("#r-speed button").forEach((b) => b.classList.toggle("active", Number(b.dataset.s) === s));
  }

  toggleSound() {
    this.sound = !this.sound;
    if (!this.sound) Object.values(this.els).forEach((el) => el.pause());
    const b = document.getElementById("r-sound");
    if (b) { b.innerHTML = this.sound ? icons.sound : icons.soundOff; b.setAttribute("aria-label", this.sound ? "Mute" : "Unmute"); }
  }

  _syncButtons() {
    const b = document.getElementById("r-play");
    if (b) { b.innerHTML = this.playing ? icons.pause : icons.play; b.setAttribute("aria-label", this.playing ? "Pause" : "Play"); }
  }

  _syncAudio() {
    if (!this.audio) return;
    const at = (this.now - this.audio.offset_ms) / 1000;
    for (const el of Object.values(this.els)) {
      const inRange = at >= 0 && (!el.duration || at < el.duration - 0.05);
      if (this.playing && this.sound && inRange) {
        el.playbackRate = this.speed;
        if (el.dataset.seek === "1" || Math.abs(el.currentTime - at) > 0.25) { el.currentTime = at; el.dataset.seek = ""; }
        if (el.paused) el.play().catch(() => {});
      } else if (!el.paused) el.pause();
    }
  }

  _loop() {
    cancelAnimationFrame(this._raf);
    if (!this.playing) return;
    const t = performance.now();
    this.now += (t - this._last) * this.speed;
    this._last = t;
    if (this.now >= this.domain[1]) { this.now = this.domain[1]; this.pause(); this.onEnd?.(); return; }
    this._syncAudio();
    this.render();
    this._raf = requestAnimationFrame(() => this._loop());
  }

  // ---------------------------------------------------------------- drawing
  render() {
    const m = derive(this.events, this.now);
    this.view.update(m, this.domain);
    const c = document.getElementById("r-clock");
    if (c) c.textContent = `${clock(this.now - this.domain[0])} / ${clock(this.domain[1] - this.domain[0])}`;
    const sc = document.getElementById("r-scrub");
    if (sc && sc.dataset.drag !== "1") sc.value = String(Math.round((1000 * (this.now - this.domain[0])) / Math.max(1, this.domain[1] - this.domain[0])));
    const canvas = document.getElementById("c-wave");
    if (canvas) drawWave(canvas, this._bars(m));
  }

  _bars(m) {
    const bars = [], per = WINDOW_MS / BARS;
    const env = this.env;
    // Without a recording: when the trace says someone was speaking (voice activity), not audio.
    const speaking = (who, t) => {
      let s = null;
      for (const e of m.states) { if (e.who === who && e.t <= t) s = e.state; }
      return s === "speaking";
    };
    for (let i = 0; i < BARS; i++) {
      const ta = this.now - WINDOW_MS + i * per;
      if (env) {
        const lv = (arr) => {
          if (!arr) return 0;
          let v = 0;
          const i0 = Math.floor((ta - env.offset) / env.step), i1 = Math.floor((ta + per - env.offset) / env.step);
          for (let k = Math.max(0, i0); k <= Math.min(arr.length - 1, i1); k++) v = Math.max(v, arr[k]);
          return v;
        };
        bars.push({ u: lv(env.user), a: lv(env.agent) });
      } else {
        const wob = 0.45 + 0.4 * Math.abs(Math.sin(ta / 97) * Math.cos(ta / 211));
        bars.push({ u: speaking("user", ta) ? wob : 0, a: speaking("agent", ta) ? wob : 0 });
      }
    }
    return bars;
  }
}
