/* AI DJ Lovelace card — served automatically by the ai_dj integration. */

const CARD_VERSION = "0.5.0";

class AiDjCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._mode = null; // "idle" | "active"
    this._busy = false;
    this._likedKey = null; // optimistic-like marker for the current track
    this._currentKey = null;
    this._toastTimer = null;
    this._volumeDragging = false;
    this._volumeReleaseTimer = null;
  }

  static getStubConfig() {
    return { entity: "sensor.ai_dj" };
  }

  setConfig(config) {
    this._config = { entity: "sensor.ai_dj", ...config };
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    const sensor = hass.states[this._config.entity];
    if (!sensor) {
      this._renderMissing();
      return;
    }
    const mode = sensor.state === "active" ? "active" : "idle";
    if (mode !== this._mode) {
      this._mode = mode;
      this._buildView(mode);
    }
    this._update(sensor);
  }

  // ------------------------------------------------------------ service calls

  async _call(service, data = {}, loading = null) {
    if (this._busy) return;
    this._setBusy(true, loading);
    try {
      await this._hass.callService("ai_dj", service, data);
    } catch (err) {
      this._showError(err.message || String(err));
    } finally {
      this._setBusy(false);
    }
  }

  _start() {
    const prompt = this.shadowRoot.getElementById("prompt").value.trim();
    const player = this.shadowRoot.getElementById("player").value;
    if (!prompt) {
      this._toast("Tell the DJ what you're in the mood for first");
      return;
    }
    if (!player) {
      this._toast("Pick a speaker first");
      return;
    }
    this._call(
      "start",
      { prompt, player },
      "Reading the room and cueing up your first tracks…"
    );
  }

  _wish() {
    const input = this.shadowRoot.getElementById("wish");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    this._toast("Sent to the DJ ♫");
    this._call("wish", { text }, "Working that into the mix…");
  }

  _like() {
    // Optimistic: fill the heart and confirm immediately; the sensor catches up.
    const btn = this.shadowRoot.getElementById("like");
    if (btn) btn.classList.add("liked");
    this._likedKey = this._currentKey;
    this._toast("♥ Liked — more like this");
    this._call("like");
  }

  _toggleAnnounce() {
    const attrs = this._hass.states[this._config.entity].attributes;
    const currentlyOn = attrs.announce_enabled !== false;
    this._hass.callService("ai_dj", "set_announce", { enabled: !currentlyOn });
  }

  _playPause() {
    const attrs = this._hass.states[this._config.entity].attributes;
    if (attrs.player) {
      this._hass.callService("media_player", "media_play_pause", {
        entity_id: attrs.player,
      });
    }
  }

  _onVolumeInput(e) {
    // Fires continuously while dragging - just update the live label.
    this._volumeDragging = true;
    this._text("volume-value", `${e.target.value}%`);
  }

  _onVolumeChange(e) {
    // Fires once on release/commit - this is when we actually call HA.
    const attrs = this._hass.states[this._config.entity].attributes;
    if (!attrs.player) return;
    this._hass.callService("media_player", "volume_set", {
      entity_id: attrs.player,
      volume_level: Number(e.target.value) / 100,
    });
    // Give the new state a moment to round-trip before syncing from hass
    // again, so the slider doesn't snap back before the echo arrives.
    clearTimeout(this._volumeReleaseTimer);
    this._volumeReleaseTimer = setTimeout(() => {
      this._volumeDragging = false;
    }, 1500);
  }

  // ------------------------------------------------------------------ views

  _buildView(mode) {
    this.shadowRoot.innerHTML = `
      <style>${AiDjCard.styles}</style>
      <ha-card>
        <div class="header">
          <div class="badge">♪</div>
          <div class="title">AI DJ</div>
          <div class="status ${mode}" id="status">${
            mode === "active" ? "On air" : "Idle"
          }</div>
        </div>
        ${mode === "idle" ? AiDjCard.idleTemplate : AiDjCard.activeTemplate}
        <div class="error hidden" id="error"></div>
        <div class="loading hidden" id="loading">
          <div class="spinner"></div>
          <span id="loading-text">Working…</span>
        </div>
        <div class="toast hidden" id="toast"></div>
      </ha-card>`;

    const on = (id, handler) => {
      const el = this.shadowRoot.getElementById(id);
      if (el) el.addEventListener("click", handler);
    };
    if (mode === "idle") {
      on("start", () => this._start());
      this.shadowRoot
        .getElementById("prompt")
        .addEventListener("keydown", (e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) this._start();
        });
    } else {
      on("like", () => this._like());
      on("announce", () => this._toggleAnnounce());
      on("skip", () => this._call("skip"));
      on("stop", () => this._call("stop"));
      on("playpause", () => this._playPause());
      on("send", () => this._wish());
      this.shadowRoot.getElementById("wish").addEventListener("keydown", (e) => {
        if (e.key === "Enter") this._wish();
      });
      const volumeEl = this.shadowRoot.getElementById("volume");
      volumeEl.addEventListener("input", (e) => this._onVolumeInput(e));
      volumeEl.addEventListener("change", (e) => this._onVolumeChange(e));
    }
  }

  _update(sensor) {
    const attrs = sensor.attributes;
    this._showError(attrs.error || null);

    if (this._mode === "idle") {
      this._updatePlayers(attrs.available_players || []);
      return;
    }

    const player = attrs.player ? this._hass.states[attrs.player] : null;
    const current = attrs.current_track || {};

    // Reset the optimistic-like marker when the track changes.
    const key = `${current.artist || ""}|${current.title || ""}`;
    if (key !== this._currentKey) {
      this._currentKey = key;
      this._likedKey = null;
    }

    this._text("track-title", current.title || "—");
    this._text("track-artist", current.artist || "");
    this._text("comment", attrs.dj_comment || "");
    this.shadowRoot
      .getElementById("comment-row")
      .classList.toggle("hidden", !attrs.dj_comment);

    const art = this.shadowRoot.getElementById("art");
    const pic = player && player.attributes.entity_picture;
    art.style.backgroundImage = pic ? `url("${pic}")` : "none";
    art.classList.toggle("placeholder", !pic);

    const playing = player && player.state === "playing";
    this._text("playpause", playing ? "⏸" : "▶");

    const likedBySensor = (attrs.liked || []).some(
      (t) => t.title === current.title && t.artist === current.artist
    );
    const liked = likedBySensor || this._likedKey === key;
    this.shadowRoot.getElementById("like").classList.toggle("liked", liked);

    const announceOn = attrs.announce_enabled !== false;
    const announceBtn = this.shadowRoot.getElementById("announce");
    announceBtn.classList.toggle("on", announceOn);
    announceBtn.title = announceOn
      ? "Voice announcements on — click to mute"
      : "Voice announcements muted — click to unmute";
    this._text("announce", announceOn ? "🔊" : "🔇");

    const speaker = player
      ? player.attributes.friendly_name || attrs.player
      : attrs.player || "";
    this._text("status", `On air · ${speaker}`);

    if (!this._volumeDragging) {
      const level =
        player && typeof player.attributes.volume_level === "number"
          ? Math.round(player.attributes.volume_level * 100)
          : null;
      const volumeEl = this.shadowRoot.getElementById("volume");
      if (level !== null) {
        if (Number(volumeEl.value) !== level) volumeEl.value = level;
        this._text("volume-value", `${level}%`);
      } else {
        this._text("volume-value", "–");
      }
    }

    this._updateArc(attrs.plan || [], attrs.current_phase_index);
    this._updateList("upcoming", attrs.upcoming || [], "Up next");
    this._updateList("liked-list", attrs.liked || [], "Liked");
  }

  _updateArc(plan, currentIndex) {
    const row = this.shadowRoot.getElementById("arc-row");
    if (!row) return;
    if (!plan.length) {
      row.classList.add("hidden");
      return;
    }
    row.classList.remove("hidden");
    const idx = Math.min(
      typeof currentIndex === "number" ? currentIndex : 0,
      plan.length - 1
    );
    const dots = this.shadowRoot.getElementById("arc-dots");
    dots.innerHTML = plan
      .map((_, i) => {
        const cls = i < idx ? "done" : i === idx ? "active" : "";
        return `<span class="dot ${cls}"></span>`;
      })
      .join("");
    const phase = plan[idx];
    const label = this.shadowRoot.getElementById("arc-label");
    if (label.textContent !== phase.label) label.textContent = phase.label || "";
    label.title = phase.description || "";
    this._text("arc-progress", `${idx + 1} of ${plan.length}`);
  }

  _updatePlayers(players) {
    const select = this.shadowRoot.getElementById("player");
    const options = players.map((id) => {
      const st = this._hass.states[id];
      return { id, name: (st && st.attributes.friendly_name) || id };
    });
    const signature = JSON.stringify(options);
    if (select.dataset.signature === signature) return;
    const previous = select.value;
    select.dataset.signature = signature;
    select.innerHTML = options.length
      ? options
          .map((o) => `<option value="${o.id}">${this._esc(o.name)}</option>`)
          .join("")
      : `<option value="">No Music Assistant players found</option>`;
    if (previous && players.includes(previous)) select.value = previous;
  }

  _updateList(id, items, label) {
    const el = this.shadowRoot.getElementById(id);
    if (!items.length) {
      el.classList.add("hidden");
      el.innerHTML = "";
      return;
    }
    el.classList.remove("hidden");
    el.innerHTML =
      `<div class="list-label">${label}</div>` +
      items
        .map(
          (t) =>
            `<div class="list-item"><span class="t">${this._esc(
              t.title
            )}</span><span class="a">${this._esc(t.artist)}</span></div>`
        )
        .join("");
  }

  // ------------------------------------------------------------------ helpers

  _text(id, value) {
    const el = this.shadowRoot.getElementById(id);
    if (el && el.textContent !== value) el.textContent = value;
  }

  _esc(text) {
    const div = document.createElement("div");
    div.textContent = text || "";
    return div.innerHTML;
  }

  _setBusy(busy, loading = null) {
    this._busy = busy;
    this.shadowRoot
      .querySelectorAll("button, select, textarea, input")
      .forEach((el) => (el.disabled = busy));
    const overlay = this.shadowRoot.getElementById("loading");
    if (overlay) {
      if (busy && loading) {
        this._text("loading-text", loading);
        overlay.classList.remove("hidden");
      } else {
        overlay.classList.add("hidden");
      }
    }
  }

  _toast(message) {
    const el = this.shadowRoot.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.classList.remove("hidden");
    // force reflow so the transition runs even on rapid repeat toasts
    void el.offsetWidth;
    el.classList.add("show");
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => el.classList.add("hidden"), 300);
    }, 2400);
  }

  _showError(message) {
    const el = this.shadowRoot.getElementById("error");
    if (!el) return;
    el.classList.toggle("hidden", !message);
    el.textContent = message || "";
  }

  _renderMissing() {
    this.shadowRoot.innerHTML = `
      <style>${AiDjCard.styles}</style>
      <ha-card><div class="pad">Entity <b>${this._config.entity}</b> not found.
      Is the AI DJ integration set up?</div></ha-card>`;
    this._mode = null;
  }
}

AiDjCard.idleTemplate = `
  <div class="pad">
    <textarea id="prompt" rows="3"
      placeholder="What's the vibe? e.g. 'Friday evening cooking — upbeat funk and soul'"></textarea>
    <div class="row">
      <select id="player"></select>
      <button id="start" class="primary">Start the DJ</button>
    </div>
  </div>`;

AiDjCard.activeTemplate = `
  <div class="pad">
    <div id="arc-row" class="arc hidden">
      <div id="arc-dots" class="arc-dots"></div>
      <span id="arc-label" class="arc-label"></span>
      <span id="arc-progress" class="arc-progress"></span>
    </div>
    <div class="now">
      <div id="art" class="art placeholder"></div>
      <div class="meta">
        <div id="track-title" class="track-title">—</div>
        <div id="track-artist" class="track-artist"></div>
        <div class="controls">
          <button id="playpause" class="round" title="Play / pause">▶</button>
          <button id="like" class="round heart" title="Like — more like this">♥</button>
          <button id="announce" class="round announce" title="Toggle DJ voice announcements">🔊</button>
          <button id="skip" class="round" title="Skip">⏭</button>
          <button id="stop" class="round stop" title="End session">⏹</button>
        </div>
      </div>
    </div>
    <div class="volume-row">
      <span class="vol-icon">🔊</span>
      <input id="volume" type="range" min="0" max="100" step="1" value="0" />
      <span id="volume-value" class="vol-value">–</span>
    </div>
    <div id="comment-row" class="comment hidden">
      <span class="comment-badge">DJ</span><span id="comment"></span>
    </div>
    <div class="row">
      <input id="wish" type="text"
        placeholder="Wish for a song or change the mood…" />
      <button id="send" class="primary">Send</button>
    </div>
    <div id="upcoming" class="list hidden"></div>
    <div id="liked-list" class="list hidden"></div>
  </div>`;

AiDjCard.styles = `
  :host { display: block; }
  ha-card { overflow: hidden; position: relative; }
  .pad { padding: 16px; }
  .header {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px 0 16px;
  }
  .badge {
    width: 34px; height: 34px; border-radius: 10px; flex: none;
    display: flex; align-items: center; justify-content: center;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    font-size: 18px;
  }
  .title { font-size: 1.15em; font-weight: 600; flex: 1; }
  .status {
    font-size: .8em; padding: 3px 10px; border-radius: 999px;
    background: var(--secondary-background-color);
    color: var(--secondary-text-color);
    max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .status.active {
    background: rgba(76, 175, 80, .15); color: var(--success-color, #4caf50);
  }
  textarea, input[type=text], select {
    width: 100%; box-sizing: border-box; font: inherit;
    color: var(--primary-text-color);
    background: var(--secondary-background-color);
    border: 1px solid var(--divider-color); border-radius: 10px;
    padding: 10px 12px; outline: none; resize: vertical;
  }
  textarea:focus, input[type=text]:focus, select:focus {
    border-color: var(--primary-color);
  }
  .row { display: flex; gap: 8px; margin-top: 10px; align-items: stretch; }
  .row select, .row input { flex: 1; min-width: 0; }
  button {
    font: inherit; cursor: pointer; border: none; border-radius: 10px;
    background: var(--secondary-background-color);
    color: var(--primary-text-color);
    transition: transform .08s, background .15s, color .15s;
  }
  button:not(:disabled):active { transform: scale(0.94); }
  button:disabled { cursor: default; opacity: .6; }
  button.primary {
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    padding: 10px 18px; font-weight: 600; flex: none;
  }
  .arc {
    display: flex; align-items: center; gap: 8px; margin-bottom: 14px;
    font-size: .82em; color: var(--secondary-text-color);
  }
  .arc-dots { display: flex; gap: 4px; flex: none; }
  .arc-dots .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: transparent; border: 1.5px solid var(--divider-color);
    box-sizing: border-box;
  }
  .arc-dots .dot.done {
    background: var(--secondary-text-color); border-color: var(--secondary-text-color);
  }
  .arc-dots .dot.active {
    background: var(--primary-color); border-color: var(--primary-color);
  }
  .arc-label {
    font-weight: 600; color: var(--primary-text-color);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .arc-progress { margin-left: auto; flex: none; opacity: .8; }
  .now { display: flex; gap: 14px; align-items: center; }
  .art {
    width: 84px; height: 84px; border-radius: 12px; flex: none;
    background-size: cover; background-position: center;
  }
  .art.placeholder {
    background: linear-gradient(135deg, var(--primary-color), transparent);
    opacity: .35;
  }
  .meta { flex: 1; min-width: 0; }
  .track-title {
    font-size: 1.1em; font-weight: 600; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .track-artist { color: var(--secondary-text-color); margin-top: 2px; }
  .controls { display: flex; gap: 8px; margin-top: 10px; }
  .round {
    width: 40px; height: 40px; border-radius: 50%; font-size: 16px;
    display: flex; align-items: center; justify-content: center;
  }
  .round.heart.liked {
    background: rgba(233, 30, 99, .2); color: #e91e63;
    animation: pop .3s ease;
  }
  @keyframes pop {
    0% { transform: scale(1); } 45% { transform: scale(1.35); } 100% { transform: scale(1); }
  }
  .round.stop:hover { color: var(--error-color, #f44336); }
  .round.announce.on {
    background: rgba(3, 169, 244, .18); color: var(--primary-color);
  }
  .volume-row {
    display: flex; align-items: center; gap: 10px; margin-top: 14px;
  }
  .vol-icon { flex: none; font-size: .95em; opacity: .85; }
  .volume-row input[type=range] {
    flex: 1; -webkit-appearance: none; appearance: none;
    height: 4px; border-radius: 999px;
    background: var(--divider-color); outline: none; padding: 0;
    border: none;
  }
  .volume-row input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 16px; height: 16px; border-radius: 50%;
    background: var(--primary-color); cursor: pointer;
    border: 2px solid var(--card-background-color, #fff);
    box-shadow: 0 1px 3px rgba(0,0,0,.3);
  }
  .volume-row input[type=range]::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%;
    background: var(--primary-color); cursor: pointer; border: none;
  }
  .vol-value {
    flex: none; width: 36px; text-align: right;
    font-size: .82em; color: var(--secondary-text-color);
  }
  .comment {
    margin-top: 14px; padding: 10px 12px; border-radius: 10px;
    background: var(--secondary-background-color);
    color: var(--secondary-text-color); font-style: italic;
    display: flex; gap: 10px; align-items: baseline;
  }
  .comment-badge {
    font-style: normal; font-size: .7em; font-weight: 700; flex: none;
    color: var(--primary-color); border: 1px solid var(--primary-color);
    border-radius: 6px; padding: 1px 6px;
  }
  .list { margin-top: 14px; }
  .list-label {
    font-size: .75em; font-weight: 700; text-transform: uppercase;
    letter-spacing: .06em; color: var(--secondary-text-color);
    margin-bottom: 6px;
  }
  .list-item {
    display: flex; justify-content: space-between; gap: 12px;
    padding: 5px 0; border-bottom: 1px solid var(--divider-color);
    font-size: .92em;
  }
  .list-item:last-child { border-bottom: none; }
  .list-item .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .list-item .a {
    color: var(--secondary-text-color); flex: none; max-width: 45%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .error {
    margin: 0 16px 14px 16px; padding: 8px 12px; border-radius: 8px;
    background: rgba(244, 67, 54, .12); color: var(--error-color, #f44336);
    font-size: .85em;
  }
  .loading {
    position: absolute; inset: 0; z-index: 3;
    display: flex; flex-direction: column; gap: 12px;
    align-items: center; justify-content: center; text-align: center;
    padding: 20px;
    background: color-mix(in srgb, var(--card-background-color, #fff) 82%, transparent);
    backdrop-filter: blur(2px);
    color: var(--primary-text-color);
  }
  .spinner {
    width: 34px; height: 34px; border-radius: 50%;
    border: 3px solid var(--divider-color);
    border-top-color: var(--primary-color);
    animation: spin .8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .toast {
    position: absolute; left: 50%; bottom: 14px; transform: translate(-50%, 8px);
    z-index: 4; max-width: 90%;
    padding: 8px 14px; border-radius: 999px; font-size: .85em;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    box-shadow: 0 4px 14px rgba(0,0,0,.25);
    opacity: 0; transition: opacity .25s, transform .25s;
    pointer-events: none; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
  }
  .toast.show { opacity: 1; transform: translate(-50%, 0); }
  .hidden { display: none !important; }
`;

if (!customElements.get("ai-dj-card")) {
  customElements.define("ai-dj-card", AiDjCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "ai-dj-card"))
  window.customCards.push({
    type: "ai-dj-card",
    name: "AI DJ Card",
    description: "Start and steer an AI DJ session on Music Assistant.",
    preview: true,
  });

console.info(`%c AI-DJ-CARD %c v${CARD_VERSION} `,
  "background:#03a9f4;color:#fff;border-radius:4px 0 0 4px;padding:2px 0",
  "background:#555;color:#fff;border-radius:0 4px 4px 0;padding:2px 0");
