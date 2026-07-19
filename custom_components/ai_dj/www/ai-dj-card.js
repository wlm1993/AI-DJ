/* AI DJ Lovelace card — served automatically by the ai_dj integration. */

const CARD_VERSION = "0.1.0";

class AiDjCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._mode = null; // "idle" | "active"
    this._busy = false;
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

  async _call(service, data = {}) {
    if (this._busy) return;
    this._setBusy(true);
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
    if (!prompt || !player) return;
    this._call("start", { prompt, player });
  }

  _wish() {
    const input = this.shadowRoot.getElementById("wish");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    this._call("wish", { text });
  }

  _playPause() {
    const attrs = this._hass.states[this._config.entity].attributes;
    if (attrs.player) {
      this._hass.callService("media_player", "media_play_pause", {
        entity_id: attrs.player,
      });
    }
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
      on("like", () => this._call("like"));
      on("skip", () => this._call("skip"));
      on("stop", () => this._call("stop"));
      on("playpause", () => this._playPause());
      on("send", () => this._wish());
      this.shadowRoot.getElementById("wish").addEventListener("keydown", (e) => {
        if (e.key === "Enter") this._wish();
      });
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
    this._text("track-title", current.title || "—");
    this._text("track-artist", current.artist || "");
    this._text("comment", attrs.dj_comment || "");
    this.shadowRoot.getElementById("comment-row").classList.toggle(
      "hidden",
      !attrs.dj_comment
    );

    const art = this.shadowRoot.getElementById("art");
    const pic = player && player.attributes.entity_picture;
    art.style.backgroundImage = pic ? `url(${pic})` : "none";
    art.classList.toggle("placeholder", !pic);

    const playing = player && player.state === "playing";
    this._text("playpause", playing ? "⏸" : "▶");

    const liked = (attrs.liked || []).some(
      (t) => t.title === current.title && t.artist === current.artist
    );
    this.shadowRoot.getElementById("like").classList.toggle("liked", liked);

    const speaker = player
      ? player.attributes.friendly_name || attrs.player
      : attrs.player || "";
    this._text("status", `On air · ${speaker}`);

    this._updateList("upcoming", attrs.upcoming || [], "Up next");
    this._updateList("liked-list", attrs.liked || [], "Liked");
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
          .map((o) => `<option value="${o.id}">${o.name}</option>`)
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

  _setBusy(busy) {
    this._busy = busy;
    this.shadowRoot
      .querySelectorAll("button, select, textarea, input")
      .forEach((el) => (el.disabled = busy));
    const card = this.shadowRoot.querySelector("ha-card");
    if (card) card.classList.toggle("busy", busy);
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
    <div class="now">
      <div id="art" class="art placeholder"></div>
      <div class="meta">
        <div id="track-title" class="track-title">—</div>
        <div id="track-artist" class="track-artist"></div>
        <div class="controls">
          <button id="playpause" class="round" title="Play / pause">▶</button>
          <button id="like" class="round heart" title="Like — more like this">♥</button>
          <button id="skip" class="round" title="Skip">⏭</button>
          <button id="stop" class="round stop" title="End session">⏹</button>
        </div>
      </div>
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
  ha-card { overflow: hidden; transition: opacity .2s; }
  ha-card.busy { opacity: .65; pointer-events: none; }
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
  }
  button.primary {
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    padding: 10px 18px; font-weight: 600; flex: none;
  }
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
  .round.heart.liked { background: rgba(233, 30, 99, .2); color: #e91e63; }
  .round.stop:hover { color: var(--error-color, #f44336); }
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
  .hidden { display: none !important; }
`;

customElements.define("ai-dj-card", AiDjCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "ai-dj-card",
  name: "AI DJ Card",
  description: "Start and steer an AI DJ session on Music Assistant.",
  preview: true,
});

console.info(`%c AI-DJ-CARD %c v${CARD_VERSION} `,
  "background:#03a9f4;color:#fff;border-radius:4px 0 0 4px;padding:2px 0",
  "background:#555;color:#fff;border-radius:0 4px 4px 0;padding:2px 0");
