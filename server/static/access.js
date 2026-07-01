// Shared client-side access gate for gated pages (/research, /admin).
//
// Reads the Supabase session from localStorage (key 'auth', written by the
// dashboard on login), calls the authed /api/me endpoint, and either reveals
// the page or replaces it with a locked overlay. This is a *client-side* gate:
// it controls what a browser shows, while the real enforcement for data lives
// on the server (every /api/admin/* route requires the admin role).
(function () {
  const API_BASE = '/api';

  function getAuth() {
    try {
      const parsed = JSON.parse(localStorage.getItem('auth') || 'null');
      return parsed && parsed.access_token ? parsed : null;
    } catch {
      return null;
    }
  }

  function reveal() {
    document.documentElement.style.visibility = '';
  }

  function lockScreen(title, message, linkHtml) {
    reveal();
    if (linkHtml === undefined) {
      linkHtml =
        '<a href="/dashboard" style="color:#5b6abf;text-decoration:none;font-weight:600">' +
        'Go to Dashboard →</a>';
    }
    document.body.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:center;' +
      'height:100vh;flex-direction:column;gap:14px;text-align:center;padding:24px;' +
      'font-family:system-ui,-apple-system,sans-serif;color:#8a8f98;background:#0d0d10">' +
      '<h2 style="color:#e8e8ea;margin:0;font-size:22px">' + title + '</h2>' +
      '<p style="margin:0;max-width:440px;line-height:1.55">' + message + '</p>' +
      linkHtml + '</div>';
  }

  // Fetch /api/me, transparently refreshing the access token once on 401.
  // Returns the identity object, or null when the visitor is simply not signed
  // in (no/invalid session). Throws on a transport/server error so the caller
  // can show a "couldn't verify" screen rather than a misleading login prompt.
  async function fetchMe() {
    const auth = getAuth();
    if (!auth) return null;
    let resp = await fetch(API_BASE + '/me', {
      headers: { Authorization: 'Bearer ' + auth.access_token },
    });
    if ((resp.status === 401 || resp.status === 403) && auth.refresh_token) {
      const r = await fetch(API_BASE + '/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: auth.refresh_token }),
      });
      if (!r.ok) return null; // refresh failed → treat as signed out
      const tokens = await r.json();
      localStorage.setItem(
        'auth',
        JSON.stringify({
          access_token: tokens.access_token,
          refresh_token: tokens.refresh_token,
        })
      );
      resp = await fetch(API_BASE + '/me', {
        headers: { Authorization: 'Bearer ' + tokens.access_token },
      });
    }
    if (resp.status === 401 || resp.status === 403) return null; // signed out
    if (!resp.ok) throw new Error('me failed: ' + resp.status); // 5xx etc.
    return resp.json();
  }

  // Guard the current page. Hides content immediately, then reveals it only if
  // access is granted. Returns a Promise<me> that resolves with the identity on
  // success and NEVER resolves when access is denied (an overlay is shown).
  //
  //   AccessGate.guard('research')            → requires the 'research' feature
  //   AccessGate.guard(null, { admin: true }) → requires the admin role
  async function guard(feature, opts) {
    opts = opts || {};
    document.documentElement.style.visibility = 'hidden';
    let me;
    try {
      me = await fetchMe();
    } catch (e) {
      // Transport or server error — never leave the page stuck hidden.
      lockScreen(
        'Couldn’t verify access',
        'Something went wrong checking your access. Please try again.',
        '<a href="" onclick="location.reload();return false" ' +
          'style="color:#5b6abf;text-decoration:none;font-weight:600">Retry</a>'
      );
      return new Promise(function () {});
    }
    if (!me) {
      lockScreen(
        'Sign in required',
        'This page is only available to authorized users. Sign in from the ' +
          'dashboard, then reopen this link.'
      );
      return new Promise(function () {});
    }
    if (opts.admin) {
      if (!me.is_admin) {
        lockScreen('Admin only', 'You don’t have admin access to this page.');
        return new Promise(function () {});
      }
      reveal();
      return me;
    }
    // Admins implicitly have every feature.
    if (me.is_admin || (me.features || []).indexOf(feature) !== -1) {
      reveal();
      return me;
    }
    lockScreen(
      'Access restricted',
      'You don’t have access to this page yet. Ask an admin to grant you the ' +
        '“' + feature + '” feature.'
    );
    return new Promise(function () {});
  }

  window.AccessGate = { guard: guard, getAuth: getAuth, fetchMe: fetchMe };
})();
