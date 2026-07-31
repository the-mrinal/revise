// Two-way token sync between the dashboard (localStorage "auth") and the
// extension (chrome.storage.local "auth"). The dashboard owns login: it reads
// the magic-link hash itself — this script must never touch location.hash;
// racing the page for it is what used to strip tokens before the page saw them.
//
// Both sides hold the SAME Supabase refresh token, and Supabase rotates it on
// every refresh. If either side refreshes while the other keeps the old token,
// the next refresh from the stale side trips Supabase's reuse detection and
// revokes the whole session. So on every sync we compare access-token expiry
// and push the fresher pair to both stores.
(function () {
  function tokenExp(token) {
    try {
      const payload = JSON.parse(
        atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))
      );
      return payload.exp || 0;
    } catch {
      return 0;
    }
  }

  function readLocal() {
    try {
      const t = JSON.parse(localStorage.getItem("auth") || "null");
      return t?.access_token && t?.refresh_token ? t : null;
    } catch {
      return null;
    }
  }

  function sync() {
    chrome.storage.local.get("auth", ({ auth: ext }) => {
      const page = readLocal();
      const extExp = ext?.access_token ? tokenExp(ext.access_token) : 0;
      const pageExp = page?.access_token ? tokenExp(page.access_token) : 0;

      if (page && pageExp > extExp) {
        chrome.storage.local.set({
          auth: { access_token: page.access_token, refresh_token: page.refresh_token },
        });
      } else if (ext && extExp > pageExp) {
        localStorage.setItem(
          "auth",
          JSON.stringify({ access_token: ext.access_token, refresh_token: ext.refresh_token })
        );
      }
    });
  }

  sync();
  // Keep syncing while the dashboard tab is open, so a refresh on either side
  // propagates before the other side tries to use its stale copy.
  setInterval(sync, 30 * 1000);
  // Extension-side rotation → push to the page immediately.
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.auth) sync();
  });
})();
