/* Shared per-question History/Audit modal.
 * Exposes two globals used by every template:
 *   openHistory(qid)  — fetch + show the timeline modal for a question
 *   historyBtn(qid)   — returns the HTML for the small clock trigger button
 *
 * Self-contained: reads the same `auth` token the pages use and injects its own
 * styles (scoped with a `qh-` prefix, themed off the page's CSS vars with
 * dark-theme fallbacks) so it works identically on dashboard / prep / research.
 */
(function () {
  var STYLE_ID = "qh-styles";
  var OVERLAY_ID = "qh-overlay";

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var css = `
      .qh-trigger { background:none; border:none; cursor:pointer; font-size:13px;
        opacity:.55; padding:2px 4px; line-height:1; color:var(--muted,#9aa0aa); }
      .qh-trigger:hover { opacity:1; }
      #${OVERLAY_ID} { position:fixed; inset:0; background:rgba(0,0,0,.55);
        display:none; align-items:center; justify-content:center; z-index:1000; }
      #${OVERLAY_ID}.qh-open { display:flex; }
      .qh-modal { background:var(--card,#16161a); color:var(--text,#e7e7ea);
        border:1px solid var(--border,#2a2a31); border-radius:12px; width:min(440px,92vw);
        max-height:82vh; overflow:auto; padding:18px 20px;
        box-shadow:0 12px 40px rgba(0,0,0,.5); }
      .qh-head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
      .qh-title { font-size:15px; font-weight:600; margin:0 0 2px; }
      .qh-sub { font-size:12px; color:var(--muted,#9aa0aa); margin-bottom:14px; }
      .qh-close { background:none; border:none; color:var(--muted,#9aa0aa);
        font-size:20px; cursor:pointer; line-height:1; }
      .qh-close:hover { color:var(--text,#e7e7ea); }
      .qh-timeline { list-style:none; margin:0; padding:0; position:relative; }
      .qh-ev { position:relative; padding:0 0 16px 20px; border-left:2px solid var(--border,#2a2a31); }
      .qh-ev:last-child { border-left-color:transparent; padding-bottom:0; }
      .qh-ev::before { content:""; position:absolute; left:-6px; top:3px; width:10px; height:10px;
        border-radius:50%; background:var(--accent,#5b6abf); }
      .qh-ev-new::before { background:#10b981; }
      .qh-date { font-size:11px; color:var(--muted,#9aa0aa); }
      .qh-label { font-size:13px; font-weight:600; margin:1px 0; }
      .qh-stars { color:#fbbf24; font-size:12px; letter-spacing:1px; }
      .qh-outcome { font-size:12px; color:var(--muted,#9aa0aa); margin-top:2px; }
      .qh-recon { font-size:10px; color:var(--muted,#9aa0aa); opacity:.7; font-style:italic; }
      .qh-empty { font-size:13px; color:var(--muted,#9aa0aa); padding:8px 0; }
    `;
    var el = document.createElement("style");
    el.id = STYLE_ID;
    el.textContent = css;
    document.head.appendChild(el);
  }

  function ensureOverlay() {
    var ov = document.getElementById(OVERLAY_ID);
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = OVERLAY_ID;
    ov.innerHTML = '<div class="qh-modal" id="qh-modal"></div>';
    ov.addEventListener("click", function (e) {
      if (e.target === ov) closeHistory();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeHistory();
    });
    document.body.appendChild(ov);
    return ov;
  }

  function closeHistory() {
    var ov = document.getElementById(OVERLAY_ID);
    if (ov) ov.classList.remove("qh-open");
  }

  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d)) return String(iso).slice(0, 10);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  function stars(n) {
    n = n || 0;
    return n ? "★".repeat(n) + "☆".repeat(5 - n) : "";
  }

  var LABELS = { created: "First solved", reviewed: "Reviewed", attempted: "Re-attempted" };

  function outcome(ev) {
    if (ev.event_type === "attempted") return "Re-opened the problem";
    if (ev.next_review == null) return "";
    if (ev.interval === 1 && (ev.repetitions === 1 || ev.repetitions === 0)) {
      return ev.event_type === "created" ? "→ first review tomorrow" : "→ reset to 1 day";
    }
    var iv = ev.interval != null ? ` (${ev.interval}d interval)` : "";
    return "→ next review " + fmtDate(ev.next_review) + iv;
  }

  function render(data) {
    var modal = document.getElementById("qh-modal");
    var q = data.question || {};
    var events = (data.events || []).slice();

    var solveEvents = events.filter(function (e) {
      return e.event_type === "created" || e.event_type === "reviewed";
    });
    var first = solveEvents.length
      ? fmtDate(solveEvents[0].created_at)
      : (q.solved_at ? fmtDate(q.solved_at) : "—");
    var reviews = events.filter(function (e) { return e.event_type === "reviewed"; }).length;
    var attempts = q.attempts || 1;

    var sub =
      "First solved " + first +
      " · " + reviews + (reviews === 1 ? " review" : " reviews") +
      (attempts > 1 ? " · ×" + attempts + " attempts" : "");

    // newest first
    events.sort(function (a, b) {
      return String(b.created_at || "").localeCompare(String(a.created_at || ""));
    });

    var rows = events.map(function (ev) {
      var isNew = ev.event_type === "created";
      var s = stars(ev.self_rating);
      var oc = outcome(ev);
      var time = ev.time_taken ? " · " + ev.time_taken + "m" : "";
      return (
        '<li class="qh-ev' + (isNew ? " qh-ev-new" : "") + '">' +
        '<div class="qh-date">' + escHtml(fmtDate(ev.created_at)) + "</div>" +
        '<div class="qh-label">' + escHtml(LABELS[ev.event_type] || ev.event_type) +
        (ev.reconstructed ? ' <span class="qh-recon">(reconstructed)</span>' : "") + "</div>" +
        (s ? '<div class="qh-stars">' + s + escHtml(time) + "</div>" : (time ? '<div class="qh-outcome">' + escHtml(time.replace(" · ", "")) + "</div>" : "")) +
        (oc ? '<div class="qh-outcome">' + escHtml(oc) + "</div>" : "") +
        "</li>"
      );
    }).join("");

    modal.innerHTML =
      '<div class="qh-head">' +
      "<div>" +
      '<p class="qh-title">' + escHtml(q.title || q.url || "Question") + "</p>" +
      '<div class="qh-sub">' + escHtml(sub) + "</div>" +
      "</div>" +
      '<button class="qh-close" aria-label="Close" onclick="closeHistory()">×</button>' +
      "</div>" +
      (events.length
        ? '<ul class="qh-timeline">' + rows + "</ul>"
        : '<div class="qh-empty">No recorded history yet — earlier activity wasn\'t logged.</div>');
  }

  function getToken() {
    try {
      var auth = JSON.parse(localStorage.getItem("auth") || "null");
      return auth && auth.access_token ? auth.access_token : null;
    } catch (e) {
      return null;
    }
  }

  async function openHistory(qid) {
    injectStyles();
    var ov = ensureOverlay();
    var modal = document.getElementById("qh-modal");
    modal.innerHTML = '<div class="qh-empty">Loading history…</div>';
    ov.classList.add("qh-open");
    var token = getToken();
    if (!token) {
      modal.innerHTML = '<div class="qh-empty">Sign in to view history.</div>';
      return;
    }
    try {
      var r = await fetch("/api/questions/" + qid + "/history", {
        headers: { Authorization: "Bearer " + token },
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      render(await r.json());
    } catch (e) {
      modal.innerHTML = '<div class="qh-empty">Couldn\'t load history.</div>';
    }
  }

  function historyBtn(qid) {
    return '<button class="qh-trigger" title="History" onclick="openHistory(' + qid + ')">🕓</button>';
  }

  window.openHistory = openHistory;
  window.closeHistory = closeHistory;
  window.historyBtn = historyBtn;
})();
