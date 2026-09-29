// A live conversation with the agent, in the browser, over LiveKit.
// Transcripts arrive on LiveKit's own "lk.transcription" text streams; Keel's
// events (keel/web/story.py) arrive on the topic the server names (keel.story).
// Times are put on this page's clock: t = ms since the page connected.

const LIVEKIT_CLIENT = "https://cdn.jsdelivr.net/npm/livekit-client@2.22.3/+esm";
const BARS = 90;
const BAR_MS = 66;

export class LiveSession {
  constructor(token, { onChange, onFrame, onEnd }) {
    this.token = token;
    this.onChange = onChange || (() => {});
    this.onFrame = onFrame || (() => {});
    this.onEnd = onEnd || (() => {});
    this.state = {
      room: token.room, connecting: true, agentJoined: false, waitedLong: false,
      micOn: true, camOn: false, elapsed: 0, pipeline: null,
      turns: [],        // transcripts: {kind: "user"|"agent", t, text, interim}
      story: [],        // Keel events other than actions, on the agent's clock
      actions: new Map(),
    };
    this.bars = Array.from({ length: BARS }, () => ({ u: 0, a: 0 }));
    // The agent's clock minus this page's: estimated as the smallest
    // (arrival here - time stamped there) seen so far, i.e. clock offset plus
    // the fastest delivery. Keel's times are shown on this page's clock with it.
    this.skew = null;
    this._segments = new Map();
    this._analysers = {};
    this._audioEls = [];
    this._finished = false;
    this._t0 = null;
    this._seq = 0;      // a stable key (_k) per transcript line and story event, for the chat
  }

  async connect() {
    const LK = await import(LIVEKIT_CLIENT);
    this.LK = LK;
    const { Room, RoomEvent, Track } = LK;
    const room = (this.room = new Room({ adaptiveStream: true, dynacast: true }));
    room.registerTextStreamHandler("lk.transcription", (reader, info) => this._transcript(reader, info));
    room.registerTextStreamHandler(this.token.story_topic, async (reader) => {
      try { this._story(JSON.parse(await reader.readAll())); } catch (e) { console.warn("story message", e); }
    });
    room.on(RoomEvent.ParticipantConnected, (p) => this._participant(p));
    room.on(RoomEvent.ParticipantAttributesChanged, (_changed, p) => this._participant(p));
    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== Track.Kind.Audio) return;
      const el = track.attach();
      el.style.display = "none";
      document.body.appendChild(el);
      this._audioEls.push(el);
      this._analyse("agent", track.mediaStreamTrack);
    });
    room.on(RoomEvent.Disconnected, () => this._finish());

    await room.connect(this.token.url, this.token.token);
    this._t0 = performance.now();
    await room.localParticipant.setMicrophoneEnabled(true);
    const mic = room.localParticipant.getTrackPublication(Track.Source.Microphone);
    if (mic?.track) this._analyse("mic", mic.track.mediaStreamTrack);
    await room.startAudio().catch(() => {});
    room.remoteParticipants.forEach((p) => this._participant(p));
    this.state.connecting = false;
    this._loop();
    this.onChange();
    this._waitTimer = setTimeout(() => {
      if (!this.state.agentJoined) { this.state.waitedLong = true; this.onChange(); }
    }, 15000);
  }

  // ---------------------------------------------------------------- controls
  async toggleMic() {
    if (!this.room) return;
    this.state.micOn = !this.state.micOn;
    await this.room.localParticipant.setMicrophoneEnabled(this.state.micOn);
    this.onChange();
  }

  async toggleCamera(videoEl) {
    if (!this.room) return;
    this.state.camOn = !this.state.camOn;
    await this.room.localParticipant.setCameraEnabled(this.state.camOn);
    this.onChange();
    this.attachPreview(videoEl);
  }

  attachPreview(videoEl) {
    if (!this.room || !videoEl || !this.state.camOn) return;
    const pub = this.room.localParticipant.getTrackPublication(this.LK.Track.Source.Camera);
    if (pub?.track) pub.track.attach(videoEl);
  }

  end() {
    if (this.room && !this._finished) this.room.disconnect();
    else this._finish();
  }

  // ---------------------------------------------------------------- model
  now() { return this._t0 == null ? 0 : performance.now() - this._t0; }

  // Story events for the console, on this page's clock. Transcripts come from
  // LiveKit (word by word); the story's own user/agent lines would repeat them.
  events() {
    const k = this.skew ?? 0;
    const shift = (e) => ({ ...e, t: e.t + k });
    const out = this.state.turns.slice();
    for (const e of this.state.story) out.push(shift(e));
    for (const a of this.state.actions.values()) out.push({ ...a, history: a.history.map(shift) });
    return out;
  }

  // ---------------------------------------------------------------- events
  _participant(p) {
    const isAgent = p.isAgent || p.kind === this.LK?.ParticipantKind?.AGENT;
    if (!isAgent) return;
    if (!this.state.agentJoined) { this.state.agentJoined = true; this.onChange(); }
  }

  async _transcript(reader, info) {
    const attrs = reader.info?.attributes || {};
    const segment = attrs["lk.segment_id"] || reader.info?.id;
    const mine = info.identity === this.room.localParticipant.identity;
    let turn = this._segments.get(segment);
    if (!turn) {
      turn = { kind: mine ? "user" : "agent", text: "", interim: true, t: this.now(), _k: this._seq++ };
      this._segments.set(segment, turn);
      this.state.turns.push(turn);
    }
    // A new stream for a segment carries its whole current text (interim user
    // transcripts); within one stream the text arrives in pieces (agent speech).
    let text = "";
    for await (const chunk of reader) {
      text += chunk;
      turn.text = text;
      this.onChange();
    }
    turn.interim = mine ? attrs["lk.transcription_final"] !== "true" : false;
    this.onChange();
  }

  _story(events) {
    const here = this.now();
    for (const ev of events) {
      const stamped = ev.t_updated ?? ev.t;
      if (typeof stamped === "number") {
        const d = here - stamped;
        this.skew = this.skew == null ? d : Math.min(this.skew, d);
      }
      if (ev.kind === "action") this.state.actions.set(ev.id, ev);
      else if (ev.kind === "config") this.state.pipeline = ev.pipeline;
      else if (ev.kind === "note" || ev.kind === "keel" || ev.kind === "state") this.state.story.push({ ...ev, _k: this._seq++ });
    }
    this.onChange();
  }

  // ---------------------------------------------------------------- levels
  _analyse(which, mediaStreamTrack) {
    try {
      this._ctx = this._ctx || new AudioContext();
      const source = this._ctx.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
      const analyser = this._ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      this._analysers[which] = { analyser, buf: new Uint8Array(analyser.fftSize) };
    } catch (e) {
      console.warn("level meter unavailable", e);
    }
  }

  _level(which) {
    const a = this._analysers[which];
    if (!a) return 0;
    a.analyser.getByteTimeDomainData(a.buf);
    let sum = 0;
    for (const v of a.buf) sum += ((v - 128) / 128) ** 2;
    return Math.min(1, Math.sqrt(sum / a.buf.length) * 4);
  }

  _loop() {
    if (this._finished) return;
    const s = this.state;
    s.elapsed = this.now();
    const u = s.micOn ? this._level("mic") : 0, a = this._level("agent");
    const cur = this.bars[this.bars.length - 1];
    cur.u = Math.max(cur.u, u);
    cur.a = Math.max(cur.a, a);
    if (!this._lastBar || s.elapsed - this._lastBar >= BAR_MS) {
      this._lastBar = s.elapsed;
      this.bars.shift();
      this.bars.push({ u: 0, a: 0 });
    }
    this.onFrame();
    this._raf = requestAnimationFrame(() => this._loop());
  }

  _finish() {
    if (this._finished) return;
    this._finished = true;
    cancelAnimationFrame(this._raf);
    clearTimeout(this._waitTimer);
    this._audioEls.forEach((el) => el.remove());
    this._ctx?.close().catch(() => {});
    this.onEnd();
  }
}
