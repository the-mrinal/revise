// Feeds Revise-specific app context into bug reports filed via the
// bugs.mrinal.dev widget. Identity (email / user id) is handled declaratively
// by the widget's `data-identity-jwt="localStorage:auth"` attribute, which
// decodes the Supabase access_token; this file only adds app context.
//
// Loaded `defer` AFTER widget.js, so window.BugReport already exists. The
// context function is a live hook the widget re-evaluates at submit time, so
// the values reflect the user's state at the moment they report — not page load.
(function () {
  if (!window.BugReport) return;

  window.BugReport.context = function () {
    var ctx = { page: location.pathname };
    try {
      // Dashboard active tab (Overview/Today/Revised/Pattern/All/New) is hidden
      // state — it isn't reflected in the URL, so surface it for triage.
      var tab = localStorage.getItem("dashboard-tab");
      if (tab) ctx.dashboardTab = tab;
    } catch (e) {
      /* localStorage may be unavailable (private mode / blocked) — ignore */
    }
    return ctx;
  };
})();
