// Shared helpers for the Keel web app: escaping, formatting, icons, the API.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// m:ss for a clock, m:ss.s for event times.
export const clock = (ms, tenths = false) => {
  if (ms == null || !isFinite(ms)) return "–";
  const neg = ms < 0, s = Math.abs(ms) / 1000, m = Math.floor(s / 60), r = s - m * 60;
  const sec = tenths ? r.toFixed(1).padStart(4, "0") : String(Math.floor(r)).padStart(2, "0");
  return `${neg ? "-" : ""}${m}:${sec}`;
};
export const duration = (ms) => {
  if (ms == null) return "–";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`;
};
export const pct = (x) => (x == null ? "–" : `${Math.round(x * 1000) / 10}`);
export const secs = (x) => (x == null ? "–" : x.toFixed(1));
export const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
export const when = (unix) => new Date(unix * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export async function api(path, init) {
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).error || ""; } catch (e) { /* not JSON */ }
    throw new Error(detail || `${r.status} ${r.statusText}`);
  }
  return r.json();
}

export function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 1800);
}

export function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) { /* storage unavailable: settings are not remembered */ }
  return null;
}

// ------------------------------------------------------------------ states
// Keel's action states (keel/web/story.py) and how the app names them.
export const STATE = {
  planned: { label: "Planned", cls: "planned" },
  holding: { label: "Held", cls: "holding" },
  sent: { label: "Running", cls: "sent" },
  done: { label: "Executed", cls: "done" },
  failed: { label: "Failed", cls: "failed" },
  not_sent: { label: "Dropped", cls: "not_sent" },
  reused: { label: "Reused", cls: "reused" },
};
export const chip = (state, extra = "") => {
  const s = STATE[state] || { label: state || "–", cls: "" };
  return `<span class="chip ${s.cls} ${extra}">${esc(s.label)}</span>`;
};
export const argText = (args) => (args || []).map(([k, v]) => `${k.toLowerCase()}: ${v}`).join(", ");

export const PIPELINES = {
  cascaded: "Cascaded (speech-to-text, LLM, text-to-speech)",
  gpt_realtime: "OpenAI Realtime",
  gemini_realtime: "Gemini Live",
  open: "Open cascade (OpenAI-compatible endpoints)",
};
export const SOURCES = { benchmark: "Benchmark", live: "Live", show_and_fix: "Show & Fix", other: "Other" };
export const PROFILES = { fdb_v3: "Benchmark agent", show_and_fix: "Show & Fix" };

// ------------------------------------------------------------------ icons
const svg = (d, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
export const icons = {
  home: svg('<path d="M3 11.5 12 4l9 7.5"/><path d="M5.5 10v9.5h5v-5h3v5h5V10"/>'),
  live: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
  sessions: svg('<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>'),
  benchmark: svg('<path d="M4 20V11M10 20V5M16 20v-6M21 20H3"/>'),
  setup: svg('<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>'),
  mic: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
  micOff: svg('<path d="M15 9.5V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.6 2.5M5 11a7 7 0 0 0 11.2 5.6M19 11a7 7 0 0 1-.5 2.5M12 18v3M4 4l16 16"/>'),
  camera: svg('<rect x="3" y="7" width="13" height="11" rx="2"/><path d="m16 11 5-3v9l-5-3"/>'),
  end: svg('<path d="M4.5 15.5c4.4-4 10.6-4 15 0l-2 2.5-3-1.2v-2.3a9 9 0 0 0-5 0v2.3l-3 1.2z"/>'),
  play: svg('<path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/>'),
  pause: svg('<rect x="7" y="5" width="3.5" height="14" rx="1" fill="currentColor"/><rect x="13.5" y="5" width="3.5" height="14" rx="1" fill="currentColor"/>'),
  back: svg('<path d="M11 17 6 12l5-5M18 17l-5-5 5-5"/>'),
  fwd: svg('<path d="m13 17 5-5-5-5M6 17l5-5-5-5"/>'),
  restart: svg('<path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/>'),
  sound: svg('<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>'),
  soundOff: svg('<path d="M4 9.5h3.5L12 6v12l-4.5-3.5H4z"/><path d="m16 10 4 4M20 10l-4 4"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2.5 12h2M19.5 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>'),
  moon: svg('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'),
  check: svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  x: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
  alert: svg('<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5M12 17.2v.1"/>'),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.7v.1"/>'),
  hand: svg('<path d="M8 13V6.5a1.5 1.5 0 0 1 3 0V11M11 10.5V5a1.5 1.5 0 0 1 3 0v5.5M14 10.5V6.5a1.5 1.5 0 0 1 3 0v7a6.5 6.5 0 0 1-6.5 6.5A6 6 0 0 1 5 16.8l-1.6-3a1.5 1.5 0 0 1 2.6-1.5L8 15"/>'),
  send: svg('<path d="M4 12h12M11 6l6 6-6 6"/>'),
  bolt: svg('<path d="M13 3 5 13.5h6L10 21l8-10.5h-6z"/>'),
  repeat: svg('<path d="M17 3l3 3-3 3M4 11V9.5A3.5 3.5 0 0 1 7.5 6H20M7 21l-3-3 3-3M20 13v1.5a3.5 3.5 0 0 1-3.5 3.5H4"/>'),
  spark: svg('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>'),
  plan: svg('<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h5"/>'),
  arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  copy: svg('<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M15 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h3"/>'),
  refresh: svg('<path d="M20 12a8 8 0 1 1-2.4-5.7M20 4v4h-4"/>'),
  user: svg('<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>'),
  bot: svg('<rect x="4.5" y="8" width="15" height="11" rx="3"/><path d="M12 8V5M9 13h.01M15 13h.01"/><circle cx="12" cy="4" r="1"/>'),
  shield: svg('<path d="M12 3.5 5 6v5.5c0 4.3 3 7.7 7 9 4-1.3 7-4.7 7-9V6z"/><path d="m9 12 2 2 4-4"/>'),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>'),
  clockIc: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>'),
  wave: svg('<path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0"/>'),
  panel: svg('<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14.5 4.5v15"/>'),
  down: svg('<path d="M12 5v14M6 13l6 6 6-6"/>'),
};
export const keelMark = `<svg viewBox="0 0 32 32" width="16" height="16" aria-hidden="true"><path d="M7 20c3 3 15 3 18 0M16 6v13M16 8l6 9h-6" stroke="currentColor" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
