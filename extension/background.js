const API_BASE = "https://revise.mrinal.dev/api";

// --- Proactive token refresh ---
let refreshInProgress = null; // Promise lock to prevent concurrent refreshes

function decodeJwtPayload(token) {
  try {
    const payload = token.split(".")[1];
    return JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
  } catch { return null; }
}

function tokenExpiresWithin(token, seconds) {
  const payload = decodeJwtPayload(token);
  if (!payload || !payload.exp) return true; // treat as expired if unreadable
  return (payload.exp - Date.now() / 1000) < seconds;
}

async function refreshAuthToken(force = false) {
  // If a refresh is already in progress, wait for it
  if (refreshInProgress) return refreshInProgress;

  refreshInProgress = (async () => {
    try {
      const authData = await chrome.storage.local.get("auth");
      const auth = authData.auth;
      if (!auth?.refresh_token) return null;

      // Skip if token still has >5 minutes left — unless the caller just got
      // a 401 with this token and needs a fresh one regardless.
      if (!force && auth.access_token && !tokenExpiresWithin(auth.access_token, 300)) {
        return auth;
      }

      console.log("[Revise] Proactively refreshing token");
      const resp = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: auth.refresh_token }),
      });

      if (resp.ok) {
        const tokens = await resp.json();
        await chrome.storage.local.set({ auth: tokens });
        console.log("[Revise] Token refreshed successfully");
        return tokens;
      } else {
        console.warn("[Revise] Token refresh failed:", resp.status);
        if (resp.status >= 400 && resp.status < 500) {
          // Session is dead (revoked/rotated-away refresh token) — keeping it
          // would retry forever on every alarm. Drop it; the dashboard content
          // script re-syncs fresh tokens on the next dashboard visit.
          await chrome.storage.local.remove("auth");
        }
        return null;
      }
    } catch (e) {
      console.error("[Revise] Token refresh error:", e);
      return null;
    } finally {
      refreshInProgress = null;
    }
  })();

  return refreshInProgress;
}

// Token capture from the callback URL was removed on purpose: the dashboard
// owns the magic-link hash, and the content script (capture-tokens.js) syncs
// tokens both ways once the page has stored them.

// --- Badge update with auth ---
async function updateBadge() {
  try {
    // Check for active timer first
    const timerData = await chrome.storage.local.get("timer");
    if (timerData.timer && timerData.timer.running) {
      chrome.action.setBadgeText({ text: "⏱" });
      chrome.action.setBadgeBackgroundColor({ color: "#047857" });
      return;
    }

    // Get auth token
    const authData = await chrome.storage.local.get("auth");
    const auth = authData.auth;
    if (!auth || !auth.access_token) {
      chrome.action.setBadgeText({ text: "" });
      return;
    }

    const resp = await fetch(`${API_BASE}/revisions/today`, {
      headers: { Authorization: `Bearer ${auth.access_token}` },
    });
    if (resp.ok) {
      const data = await resp.json();
      const count = data.length;
      chrome.action.setBadgeText({ text: count > 0 ? String(count) : "" });
      chrome.action.setBadgeBackgroundColor({ color: "#e74c3c" });
    } else if (resp.status === 401) {
      const refreshed = await refreshAuthToken();
      if (refreshed?.access_token) {
        const retry = await fetch(`${API_BASE}/revisions/today`, {
          headers: { Authorization: `Bearer ${refreshed.access_token}` },
        });
        if (retry.ok) {
          const data = await retry.json();
          const count = data.length;
          chrome.action.setBadgeText({ text: count > 0 ? String(count) : "" });
          chrome.action.setBadgeBackgroundColor({ color: "#e74c3c" });
        }
      }
    }
  } catch {
    chrome.action.setBadgeText({ text: "" });
  }
}

// --- Sunday link, kept from /api/me ---
// {login, connected_at} while this Revise account is connected to Sunday,
// else null. The panel and popup read it from storage and change nothing
// unless it is set. A failed check keeps what was there; signing out clears it.
async function updateSunday() {
  try {
    const { auth } = await chrome.storage.local.get("auth");
    if (!auth?.access_token) {
      await chrome.storage.local.remove("sunday");
      return;
    }
    const resp = await fetch(`${API_BASE}/me`, {
      headers: { Authorization: `Bearer ${auth.access_token}` },
    });
    if (!resp.ok) return;
    const me = await resp.json();
    if (me.sunday) await chrome.storage.local.set({ sunday: me.sunday });
    else await chrome.storage.local.remove("sunday");
  } catch { /* offline: keep what was there */ }
}

// Update badge every 30 minutes
chrome.alarms.create("checkRevisions", { periodInMinutes: 30 });

// Also update badge every minute when timer might be active
chrome.alarms.create("checkTimer", { periodInMinutes: 1 });

// Proactive token refresh every 5 minutes
chrome.alarms.create("refreshToken", { periodInMinutes: 5 });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "refreshToken") {
    refreshAuthToken();
  } else if (alarm.name === "checkRevisions" || alarm.name === "checkTimer") {
    updateBadge();
    if (alarm.name === "checkRevisions") updateSunday();
  }
});

// Update on install/startup — refresh token first, then update badge
chrome.runtime.onInstalled.addListener(() => { refreshAuthToken().then(updateBadge).then(updateSunday); });
chrome.runtime.onStartup.addListener(() => { refreshAuthToken().then(updateBadge).then(updateSunday); });

// Update badge when storage changes (timer start/stop or auth change)
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.timer || changes.auth)) {
    updateBadge();
  }
  if (area === "local" && changes.auth) updateSunday();
});

// --- Start-timer transaction ---
// Runs here rather than in the popup because Chrome destroys the popup the
// moment it loses focus. If the popup dies between the POST and the timer
// write, the server has a question the client doesn't know about — no widget,
// no cancel path. The service worker stays alive until sendResponse, so both
// steps complete no matter when the user clicks away.
async function startTimerTransaction(payload) {
  const { auth } = await chrome.storage.local.get("auth");
  if (!auth?.access_token) return { ok: false, error: "Not authenticated", authExpired: true };

  const post = (token) =>
    fetch(`${API_BASE}/questions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        url: payload.url,
        title: payload.title,
        difficulty: null,
        time_taken: null,
        notes: null,
        question_type: payload.questionType,
      }),
    });

  let r = await post(auth.access_token);
  if (r.status === 401) {
    const refreshed = await refreshAuthToken(true);
    if (!refreshed?.access_token) {
      return { ok: false, error: "Session expired", authExpired: true };
    }
    r = await post(refreshed.access_token);
  }

  if (!r.ok) {
    let msg = `Server error (${r.status})`;
    try {
      const err = await r.json();
      msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    } catch {}
    return { ok: false, error: msg };
  }

  const question = await r.json();
  const timerState = {
    questionId: question.id,
    // Cancel needs to know whether to delete the row (fresh) or just
    // roll back the attempt bump (already tracked).
    wasExisting: !!question.was_existing,
    url: payload.url,
    title: payload.title || payload.url,
    questionType: payload.questionType,
    startTime: Date.now(),
    accumulated: 0,
    running: true,
  };
  await chrome.storage.local.set({ timer: timerState });
  return { ok: true };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "startTimer") {
    startTimerTransaction(msg.payload)
      .catch((e) => ({ ok: false, error: "Cannot reach server: " + e.message }))
      .then(sendResponse);
    return true; // keep the message channel open for the async response
  }
});

// --- Toggle overlay command ---
chrome.commands.onCommand.addListener((command) => {
  if (command === "toggle-overlay") {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]?.id) {
        chrome.tabs.sendMessage(tabs[0].id, { action: "toggleOverlay" });
      }
    });
  }
});
