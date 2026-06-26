(() => {
  const API = "https://revise.mrinal.dev/api";
  let panelOpen = false;
  let host = null;
  let shadow = null;
  let currentQuestion = null;
  let timerDisplayInterval = null;

  // --- Persistent floating timer widget ---
  let widgetHost = null;
  let widgetShadow = null;
  let widgetInterval = null;

  // --- Auth helpers ---
  function getAuth() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get("auth", (data) => {
          if (chrome.runtime.lastError) { resolve(null); return; }
          resolve(data.auth || null);
        });
      } catch { resolve(null); }
    });
  }

  function getTimer() {
    return new Promise((resolve) => {
      try {
        chrome.storage.local.get("timer", (data) => {
          if (chrome.runtime.lastError) { resolve(null); return; }
          resolve(data.timer || null);
        });
      } catch { resolve(null); }
    });
  }

  async function apiFetch(path, options = {}) {
    const auth = await getAuth();
    if (!auth || !auth.access_token) throw new Error("Not authenticated");
    const headers = { ...options.headers, Authorization: `Bearer ${auth.access_token}` };
    let r = await fetch(`${API}${path}`, { ...options, headers });
    if (r.status === 401 && auth.refresh_token) {
      const refreshResp = await fetch(`${API}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: auth.refresh_token }),
      });
      if (refreshResp.ok) {
        const tokens = await refreshResp.json();
        chrome.storage.local.set({ auth: tokens });
        headers.Authorization = `Bearer ${tokens.access_token}`;
        r = await fetch(`${API}${path}`, { ...options, headers });
      }
    }
    return r;
  }

  // --- Time formatting ---
  function formatTime(totalSeconds) {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
  }

  function getElapsedSeconds(timer) {
    let elapsed = timer.accumulated || 0;
    if (timer.running && timer.startTime) {
      elapsed += Math.floor((Date.now() - timer.startTime) / 1000);
    }
    return elapsed;
  }

  // =============================================
  // PERSISTENT FLOATING TIMER WIDGET
  // Always visible in bottom-right when timer is active
  // =============================================
  function createWidget() {
    if (widgetHost) return;
    widgetHost = document.createElement("div");
    widgetHost.id = "revise-timer-widget-host";
    widgetShadow = widgetHost.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = `
      :host {
        all: initial;
        display: block !important;
        position: fixed !important;
        bottom: 20px !important;
        right: 20px !important;
        z-index: 2147483646 !important;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        visibility: visible !important;
        opacity: 1 !important;
        pointer-events: auto !important;
      }
      .widget {
        display: flex;
        align-items: center;
        gap: 10px;
        background: #0f0f0f;
        border: 1px solid #2a4a2a;
        border-radius: 12px;
        padding: 8px 14px;
        cursor: pointer;
        user-select: none;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        transition: border-color 0.2s, box-shadow 0.2s;
      }
      .widget:hover {
        border-color: #4ade80;
        box-shadow: 0 4px 24px rgba(74,222,128,0.15);
      }
      .widget.paused {
        border-color: #78350f;
      }
      .widget.paused:hover {
        border-color: #fbbf24;
        box-shadow: 0 4px 24px rgba(251,191,36,0.15);
      }
      .dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: #4ade80;
        animation: pulse 1.5s ease-in-out infinite;
        flex-shrink: 0;
      }
      .widget.paused .dot {
        background: #fbbf24;
        animation: none;
      }
      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.4; }
      }
      .time {
        font-size: 16px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        color: #fff;
        letter-spacing: 1px;
      }
      .label {
        font-size: 10px;
        color: #6ee7b7;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        font-weight: 600;
      }
      .widget.paused .label {
        color: #fbbf24;
      }
      .notes-btn {
        background: rgba(91, 106, 191, 0.3);
        border: 1px solid rgba(91, 106, 191, 0.5);
        border-radius: 6px;
        color: #a5b4fc;
        font-size: 10px;
        font-weight: 600;
        padding: 4px 8px;
        cursor: pointer;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        transition: background 0.2s;
      }
      .notes-btn:hover {
        background: rgba(91, 106, 191, 0.5);
      }
    `;

    const container = document.createElement("div");
    container.innerHTML = `
      <div class="widget" id="revise-widget">
        <span class="dot"></span>
        <div>
          <div class="label">Revise</div>
          <div class="time" id="revise-widget-time">00:00:00</div>
        </div>
        <button class="notes-btn" id="revise-widget-notes">Notes</button>
      </div>
    `;

    widgetShadow.appendChild(style);
    widgetShadow.appendChild(container);
    document.body.appendChild(widgetHost);

    widgetShadow.getElementById("revise-widget-notes").addEventListener("click", (e) => {
      e.stopPropagation();
      togglePanel();
    });

    widgetShadow.getElementById("revise-widget").addEventListener("click", () => {
      togglePanel();
    });
  }

  function destroyWidget() {
    if (widgetInterval) {
      clearInterval(widgetInterval);
      widgetInterval = null;
    }
    if (widgetHost && widgetHost.parentNode) {
      widgetHost.parentNode.removeChild(widgetHost);
    }
    widgetHost = null;
    widgetShadow = null;
  }

  function updateWidget() {
    if (!widgetShadow) return;
    try {
      chrome.storage.local.get("timer", (data) => {
        if (chrome.runtime.lastError) { destroyWidget(); return; }
        const timer = data.timer;
        if (!timer || !timer.questionId) {
          destroyWidget();
          return;
        }
        const timeEl = widgetShadow.getElementById("revise-widget-time");
        const widgetEl = widgetShadow.getElementById("revise-widget");
        if (timeEl) timeEl.textContent = formatTime(getElapsedSeconds(timer));
        if (widgetEl) {
          widgetEl.classList.toggle("paused", !timer.running);
        }
      });
    } catch { destroyWidget(); }
  }

  function startWidget() {
    createWidget();
    updateWidget();
    if (widgetInterval) clearInterval(widgetInterval);
    widgetInterval = setInterval(updateWidget, 1000);
  }

  // Check timer on load and show widget if active
  async function initWidget() {
    const timer = await getTimer();
    if (timer && timer.questionId) {
      startWidget();
    }
  }

  // React to timer changes (start/stop/pause from popup)
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local" || !changes.timer) return;
    const newTimer = changes.timer.newValue;
    if (newTimer && newTimer.questionId) {
      if (!widgetHost) startWidget();
      else updateWidget();
    } else {
      destroyWidget();
    }
  });

  // =============================================
  // SLIDE-IN NOTES PANEL
  // =============================================
  function createPanel() {
    host = document.createElement("div");
    host.id = "revise-overlay-host";
    shadow = host.attachShadow({ mode: "closed" });

    const style = document.createElement("style");
    style.textContent = `
      :host {
        all: initial;
        position: fixed;
        top: 0;
        right: 0;
        width: 380px;
        height: 100vh;
        z-index: 2147483647;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      .backdrop {
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.4);
        z-index: 1;
      }
      .panel {
        position: fixed;
        top: 0;
        right: 0;
        width: 380px;
        height: 100vh;
        background: #0f0f0f;
        border-left: 1px solid #2a2a4a;
        display: flex;
        flex-direction: column;
        z-index: 2;
        transform: translateX(100%);
        transition: transform 0.25s ease;
      }
      .panel.open {
        transform: translateX(0);
      }
      .panel-header {
        padding: 14px 16px;
        background: linear-gradient(135deg, #1a1a2e, #16213e);
        border-bottom: 1px solid #2a2a4a;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-shrink: 0;
      }
      .panel-header h2 {
        font-size: 14px;
        font-weight: 600;
        color: #fff;
        margin: 0;
      }
      .close-btn {
        background: none;
        border: none;
        color: #888;
        font-size: 20px;
        cursor: pointer;
        padding: 0 4px;
        line-height: 1;
      }
      .close-btn:hover { color: #fff; }

      /* Timer banner inside panel */
      .timer-banner {
        padding: 12px 16px;
        background: linear-gradient(135deg, #1a2e1a, #0f2a1f);
        border-bottom: 1px solid #2a4a2a;
        flex-shrink: 0;
      }
      .timer-info {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 6px;
      }
      .timer-label {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: #6ee7b7;
        font-weight: 600;
      }
      .timer-label.paused {
        color: #fbbf24;
      }
      .timer-title {
        font-size: 12px;
        color: #aaa;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .timer-display {
        font-size: 28px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        color: #fff;
        text-align: center;
        letter-spacing: 2px;
        margin-bottom: 8px;
      }
      .timer-controls {
        display: flex;
        gap: 8px;
      }
      .timer-btn {
        flex: 1;
        padding: 7px;
        border: none;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: opacity 0.2s;
      }
      .timer-btn:hover { opacity: 0.85; }
      .timer-btn.pause {
        background: #78350f;
        color: #fbbf24;
      }
      .timer-btn.resume {
        background: #064e3b;
        color: #6ee7b7;
      }
      .timer-btn.stop {
        background: #4c1d1d;
        color: #fca5a5;
      }

      .panel-body {
        padding: 16px;
        flex: 1;
        overflow-y: auto;
      }
      .field {
        margin-bottom: 14px;
      }
      .field label {
        display: block;
        font-size: 11px;
        font-weight: 500;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 4px;
      }
      .field input,
      .field textarea {
        width: 100%;
        padding: 8px 10px;
        background: #1a1a1a;
        border: 1px solid #333;
        border-radius: 6px;
        color: #e0e0e0;
        font-size: 13px;
        font-family: inherit;
        outline: none;
        transition: border-color 0.2s;
        box-sizing: border-box;
      }
      .field input:focus,
      .field textarea:focus {
        border-color: #5b6abf;
      }
      .field textarea {
        height: 90px;
        resize: vertical;
      }
      .row {
        display: flex;
        gap: 10px;
      }
      .row .field {
        flex: 1;
      }
      .field select {
        width: 100%;
        padding: 8px 10px;
        background: #1a1a1a;
        border: 1px solid #333;
        border-radius: 6px;
        color: #e0e0e0;
        font-size: 13px;
        font-family: inherit;
        outline: none;
        transition: border-color 0.2s;
        box-sizing: border-box;
        appearance: none;
        -webkit-appearance: none;
        background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E");
        background-repeat: no-repeat;
        background-position: right 10px center;
        padding-right: 28px;
      }
      .field select:focus {
        border-color: #5b6abf;
      }
      .stars {
        display: flex;
        gap: 4px;
      }
      .stars .star {
        width: 28px;
        height: 28px;
        border: 1px solid #333;
        border-radius: 6px;
        background: #1a1a1a;
        color: #555;
        font-size: 15px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: all 0.15s;
      }
      .stars .star:hover,
      .stars .star.active {
        background: #2a2a4a;
        border-color: #5b6abf;
        color: #f4c542;
      }
      .complexity-row {
        display: flex;
        gap: 10px;
      }
      .complexity-row .field {
        flex: 1;
      }
      .btn {
        width: 100%;
        padding: 10px;
        border: none;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 600;
        cursor: pointer;
        transition: opacity 0.2s;
        background: linear-gradient(135deg, #5b6abf, #7c3aed);
        color: #fff;
      }
      .btn:hover { opacity: 0.85; }
      .btn:disabled { opacity: 0.4; cursor: not-allowed; }
      .btn-finish {
        background: linear-gradient(135deg, #064e3b, #047857);
        color: #6ee7b7;
      }
      .finish-header {
        background: #1a1a2e;
        border: 1px solid #2a2a4a;
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 14px;
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
      .finish-title {
        font-weight: 600;
        font-size: 13px;
        color: #fff;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        max-width: 180px;
      }
      .finish-time {
        font-variant-numeric: tabular-nums;
        font-size: 13px;
        font-weight: 600;
        color: #6ee7b7;
      }
      .toast {
        display: none;
        padding: 10px 16px;
        font-size: 12px;
        font-weight: 500;
        text-align: center;
        border-radius: 6px;
        margin-bottom: 12px;
      }
      .toast.success { background: #064e3b; color: #6ee7b7; display: block; }
      .toast.error { background: #4c1d1d; color: #fca5a5; display: block; }
      .message {
        padding: 20px 16px;
        text-align: center;
        color: #888;
        font-size: 13px;
        line-height: 1.5;
      }
      .question-title {
        font-size: 12px;
        color: #aaa;
        margin-bottom: 12px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .shortcut-hint {
        text-align: center;
        font-size: 10px;
        color: #555;
        margin-top: 12px;
      }
    `;

    const container = document.createElement("div");
    container.innerHTML = `
      <div class="backdrop" id="revise-backdrop"></div>
      <div class="panel" id="revise-panel">
        <div class="panel-header">
          <h2>Revise Notes</h2>
          <button class="close-btn" id="revise-close">&times;</button>
        </div>
        <div id="revise-timer-banner"></div>
        <div class="panel-body" id="revise-body">
          <div class="message">Loading...</div>
        </div>
      </div>
    `;

    shadow.appendChild(style);
    shadow.appendChild(container);
    document.body.appendChild(host);

    shadow.getElementById("revise-close").addEventListener("click", closePanel);
    shadow.getElementById("revise-backdrop").addEventListener("click", closePanel);
  }

  // --- Timer display in panel ---
  function startTimerDisplay() {
    stopTimerDisplay();
    updateTimerBanner();
    timerDisplayInterval = setInterval(updateTimerBanner, 1000);
  }

  function stopTimerDisplay() {
    if (timerDisplayInterval) {
      clearInterval(timerDisplayInterval);
      timerDisplayInterval = null;
    }
  }

  function updateTimerBanner() {
    if (!shadow) return;
    const banner = shadow.getElementById("revise-timer-banner");
    if (!banner) return;

    try { chrome.storage.local.get("timer", (data) => {
      if (chrome.runtime.lastError) return;
      const timer = data.timer;
      if (!timer || !timer.questionId) {
        banner.innerHTML = "";
        return;
      }

      const elapsed = getElapsedSeconds(timer);
      const statusLabel = timer.running ? "Timer running" : "Timer paused";
      const statusClass = timer.running ? "" : " paused";
      const pauseBtnClass = timer.running ? "pause" : "resume";
      const pauseBtnText = timer.running ? "Pause" : "Resume";

      banner.innerHTML = `
        <div class="timer-banner">
          <div class="timer-info">
            <span class="timer-label${statusClass}">${statusLabel}</span>
            <span class="timer-title">${timer.title || timer.url || "Untitled"}</span>
          </div>
          <div class="timer-display">${formatTime(elapsed)}</div>
          <div class="timer-controls">
            <button class="timer-btn ${pauseBtnClass}" id="revise-panel-pause">${pauseBtnText}</button>
            <button class="timer-btn stop" id="revise-panel-stop">Stop</button>
          </div>
        </div>
      `;

      const pauseBtn = shadow.getElementById("revise-panel-pause");
      const stopBtn = shadow.getElementById("revise-panel-stop");
      if (pauseBtn) pauseBtn.addEventListener("click", toggleTimerPause);
      if (stopBtn) stopBtn.addEventListener("click", stopTimer);
    }); } catch { /* extension context invalidated */ }
  }

  function renderForm() {
    const body = shadow.getElementById("revise-body");
    const q = currentQuestion;
    const rating = q.self_rating || 0;

    const diffOptions = ["", "easy", "medium", "hard"].map(d => {
      const label = d ? d.charAt(0).toUpperCase() + d.slice(1) : "Select";
      const selected = (q.difficulty || "") === d ? "selected" : "";
      return `<option value="${d}" ${selected}>${label}</option>`;
    }).join("");

    const starButtons = [1,2,3,4,5].map(v => {
      const active = v <= rating ? "active" : "";
      return `<button class="star ${active}" data-value="${v}">★</button>`;
    }).join("");

    body.innerHTML = `
      <div class="question-title">${q.title || q.url}</div>
      <div class="toast" id="revise-toast"></div>
      <div class="row">
        <div class="field">
          <label>Difficulty</label>
          <select id="revise-difficulty">${diffOptions}</select>
        </div>
        <div class="field">
          <label>Self Rating</label>
          <div class="stars" id="revise-stars">${starButtons}</div>
        </div>
      </div>
      <div class="field">
        <label>Approach / Thought Process</label>
        <textarea id="revise-approach" placeholder="How did you think about this problem?">${q.approach || ""}</textarea>
      </div>
      <div class="field">
        <label>Mistakes / What Went Wrong</label>
        <textarea id="revise-mistakes" placeholder="Edge cases missed, wrong assumptions...">${q.mistakes || ""}</textarea>
      </div>
      <div class="complexity-row">
        <div class="field">
          <label>Time Complexity</label>
          <input type="text" id="revise-time-complexity" placeholder="O(n log n)" value="${q.time_complexity || ""}">
        </div>
        <div class="field">
          <label>Space Complexity</label>
          <input type="text" id="revise-space-complexity" placeholder="O(n)" value="${q.space_complexity || ""}">
        </div>
      </div>
      <div class="field">
        <label>Notes</label>
        <textarea id="revise-notes" placeholder="Key insights, things to remember...">${q.notes || ""}</textarea>
      </div>
      <button class="btn" id="revise-save">Save Notes</button>
      <div class="shortcut-hint">Press Ctrl+Shift+R to toggle this panel</div>
    `;

    // Star rating click handlers
    let selectedRating = rating;
    shadow.querySelectorAll("#revise-stars .star").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedRating = parseInt(btn.dataset.value);
        shadow.querySelectorAll("#revise-stars .star").forEach((s, i) => {
          s.classList.toggle("active", i < selectedRating);
        });
      });
    });

    shadow.getElementById("revise-save").addEventListener("click", () => {
      saveNotes(selectedRating);
    });
  }

  function renderNotTracked() {
    const body = shadow.getElementById("revise-body");
    body.innerHTML = `
      <div class="message">
        This question isn't tracked yet.<br><br>
        Start a timer from the Revise popup first, then come back here to add notes.
      </div>
    `;
  }

  function renderNoAuth() {
    const body = shadow.getElementById("revise-body");
    body.innerHTML = `
      <div class="message">
        Not signed in.<br><br>
        Sign in via the Revise popup first.
      </div>
    `;
  }

  function showToast(msg, type) {
    const el = shadow.getElementById("revise-toast");
    if (!el) return;
    el.textContent = msg;
    el.className = `toast ${type}`;
    setTimeout(() => { el.className = "toast"; }, 3000);
  }

  // --- Open / Close ---
  async function openPanel() {
    if (!host) createPanel();

    panelOpen = true;
    requestAnimationFrame(() => {
      shadow.getElementById("revise-panel").classList.add("open");
    });

    startTimerDisplay();

    const body = shadow.getElementById("revise-body");
    body.innerHTML = '<div class="message">Loading...</div>';

    let auth;
    try {
      auth = await getAuth();
    } catch {
      renderNoAuth();
      return;
    }
    if (!auth || !auth.access_token) {
      renderNoAuth();
      return;
    }

    try {
      const r = await apiFetch(`/questions/lookup?url=${encodeURIComponent(window.location.href)}`);
      if (!shadow) return; // panel was closed while loading
      if (r.ok) {
        const text = await r.text();
        if (!text || text === "null") {
          renderNotTracked();
        } else {
          const data = JSON.parse(text);
          if (data && data.id) {
            currentQuestion = data;
            renderForm();
          } else {
            renderNotTracked();
          }
        }
      } else {
        renderNotTracked();
      }
    } catch (e) {
      if (!shadow) return;
      console.error("[Revise] Overlay lookup error:", e);
      renderNoAuth();
    }
  }

  function closePanel() {
    if (!shadow) return;
    panelOpen = false;
    stopTimerDisplay();
    const panel = shadow.getElementById("revise-panel");
    if (panel) panel.classList.remove("open");
    setTimeout(() => {
      if (host && host.parentNode) host.parentNode.removeChild(host);
      host = null;
      shadow = null;
    }, 300);
  }

  function togglePanel() {
    if (panelOpen) closePanel();
    else openPanel();
  }

  // --- Save ---
  async function saveNotes(selectedRating) {
    if (!currentQuestion) return;
    const btn = shadow.getElementById("revise-save");
    btn.disabled = true;
    btn.textContent = "Saving...";

    const payload = {
      difficulty: shadow.getElementById("revise-difficulty").value || null,
      self_rating: selectedRating || null,
      notes: shadow.getElementById("revise-notes").value || null,
      approach: shadow.getElementById("revise-approach").value || null,
      mistakes: shadow.getElementById("revise-mistakes").value || null,
      time_complexity: shadow.getElementById("revise-time-complexity").value || null,
      space_complexity: shadow.getElementById("revise-space-complexity").value || null,
    };

    try {
      const r = await apiFetch(`/questions/${currentQuestion.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (r.ok) {
        const updated = await r.json();
        currentQuestion = { ...currentQuestion, ...updated };
        showToast("Notes saved!", "success");
      } else {
        let msg = "Failed to save";
        try { const err = await r.json(); msg = err.detail || msg; } catch {}
        showToast(msg, "error");
      }
    } catch {
      showToast("Cannot reach server", "error");
    }

    btn.disabled = false;
    btn.textContent = "Save Notes";
  }

  // --- Timer controls ---
  function toggleTimerPause() {
    chrome.storage.local.get("timer", (data) => {
      const timer = data.timer;
      if (!timer) return;
      if (timer.running) {
        timer.accumulated += Math.floor((Date.now() - timer.startTime) / 1000);
        timer.startTime = null;
        timer.running = false;
      } else {
        timer.startTime = Date.now();
        timer.running = true;
      }
      chrome.storage.local.set({ timer });
    });
  }

  function stopTimer() {
    chrome.storage.local.get("timer", (data) => {
      const timer = data.timer;
      if (!timer) return;
      if (timer.running) {
        timer.accumulated += Math.floor((Date.now() - timer.startTime) / 1000);
        timer.startTime = null;
        timer.running = false;
        chrome.storage.local.set({ timer });
      }
      // Show finish form in the overlay
      stopTimerDisplay();
      renderFinishForm(timer);
    });
  }

  function renderFinishForm(timer) {
    if (!shadow) return;
    const banner = shadow.getElementById("revise-timer-banner");
    if (banner) banner.innerHTML = "";

    const body = shadow.getElementById("revise-body");
    const totalSeconds = timer.accumulated || 0;
    const totalMinutes = Math.max(1, Math.round(totalSeconds / 60));
    const q = currentQuestion || {};
    const rating = q.self_rating || 0;

    const diffOptions = ["", "easy", "medium", "hard"].map(d => {
      const label = d ? d.charAt(0).toUpperCase() + d.slice(1) : "Select";
      const selected = (q.difficulty || "") === d ? "selected" : "";
      return `<option value="${d}" ${selected}>${label}</option>`;
    }).join("");

    const starButtons = [1,2,3,4,5].map(v => {
      const active = v <= rating ? "active" : "";
      return `<button class="star ${active}" data-value="${v}">★</button>`;
    }).join("");

    body.innerHTML = `
      <div class="finish-header">
        <span class="finish-title">${timer.title || "Untitled"}</span>
        <span class="finish-time">${formatTime(totalSeconds)} (${totalMinutes} min)</span>
      </div>
      <div class="toast" id="revise-toast"></div>
      <div class="row">
        <div class="field">
          <label>Difficulty</label>
          <select id="revise-difficulty">${diffOptions}</select>
        </div>
        <div class="field">
          <label>Self Rating</label>
          <div class="stars" id="revise-stars">${starButtons}</div>
        </div>
      </div>
      <div class="field">
        <label>Approach / Thought Process</label>
        <textarea id="revise-approach" placeholder="How did you think about this problem?">${q.approach || ""}</textarea>
      </div>
      <div class="field">
        <label>Mistakes / What Went Wrong</label>
        <textarea id="revise-mistakes" placeholder="Edge cases missed, wrong assumptions...">${q.mistakes || ""}</textarea>
      </div>
      <div class="complexity-row">
        <div class="field">
          <label>Time Complexity</label>
          <input type="text" id="revise-time-complexity" placeholder="O(n log n)" value="${q.time_complexity || ""}">
        </div>
        <div class="field">
          <label>Space Complexity</label>
          <input type="text" id="revise-space-complexity" placeholder="O(n)" value="${q.space_complexity || ""}">
        </div>
      </div>
      <div class="field">
        <label>Notes</label>
        <textarea id="revise-notes" placeholder="Key insights, things to remember...">${q.notes || ""}</textarea>
      </div>
      <button class="btn btn-finish" id="revise-finish" disabled>Save & Finish</button>
    `;

    let selectedRating = rating;
    const updateFinishBtn = () => {
      const btn = shadow.getElementById("revise-finish");
      if (btn) btn.disabled = selectedRating === 0;
    };
    updateFinishBtn();

    shadow.querySelectorAll("#revise-stars .star").forEach((btn) => {
      btn.addEventListener("click", () => {
        selectedRating = parseInt(btn.dataset.value);
        shadow.querySelectorAll("#revise-stars .star").forEach((s, i) => {
          s.classList.toggle("active", i < selectedRating);
        });
        updateFinishBtn();
      });
    });

    shadow.getElementById("revise-finish").addEventListener("click", () => {
      saveAndFinish(timer, selectedRating, totalMinutes);
    });
  }

  async function saveAndFinish(timer, selectedRating, totalMinutes) {
    const qid = timer.questionId;
    if (!qid || !selectedRating) return;
    const btn = shadow.getElementById("revise-finish");
    btn.disabled = true;
    btn.textContent = "Saving...";

    try {
      // Run SM2 review
      const reviewRes = await apiFetch(`/questions/${qid}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ self_rating: selectedRating }),
      });

      if (!reviewRes.ok) {
        let msg = "Failed to save review";
        try { const err = await reviewRes.json(); msg = err.detail || msg; } catch {}
        showToast(msg, "error");
        btn.disabled = false;
        btn.textContent = "Save & Finish";
        return;
      }

      // The review endpoint auto-merges duplicates and may return a different
      // surviving question id. Use that id for the metadata update so we don't
      // PUT to a row that was just merged away (which would 404 and silently
      // drop the notes/difficulty/time fields).
      let targetId = qid;
      try {
        const reviewed = await reviewRes.json();
        if (reviewed && reviewed.id) targetId = reviewed.id;
      } catch {}

      // Update metadata
      const payload = {
        self_rating: selectedRating,
        difficulty: shadow.getElementById("revise-difficulty").value || null,
        time_taken: totalMinutes,
        notes: shadow.getElementById("revise-notes").value || null,
        approach: shadow.getElementById("revise-approach").value || null,
        mistakes: shadow.getElementById("revise-mistakes").value || null,
        time_complexity: shadow.getElementById("revise-time-complexity").value || null,
        space_complexity: shadow.getElementById("revise-space-complexity").value || null,
      };

      const updateRes = await apiFetch(`/questions/${targetId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!updateRes.ok) {
        let msg = "Saved rating, but failed to save details";
        try { const err = await updateRes.json(); msg = err.detail || msg; } catch {}
        showToast(msg, "error");
        btn.disabled = false;
        btn.textContent = "Save & Finish";
        return;
      }

      // Clear timer
      chrome.storage.local.remove("timer");
      showToast(`Saved! ${totalMinutes} min recorded.`, "success");

      // Close panel after brief delay
      setTimeout(() => closePanel(), 1500);
    } catch {
      showToast("Cannot reach server", "error");
      btn.disabled = false;
      btn.textContent = "Save & Finish";
    }
  }

  // --- Listen for messages from popup/background ---
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === "toggleOverlay") {
      togglePanel();
    }
  });

  // --- Keyboard shortcut fallback (Ctrl+Shift+R) ---
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === "R") {
      e.preventDefault();
      e.stopPropagation();
      togglePanel();
    }
  }, true);

  // --- Init: show floating timer widget if timer is active ---
  initWidget();
})();
