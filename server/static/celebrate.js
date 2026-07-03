// Celebrate — dependency-free confetti + count-up numbers for the flex views.
// Exposes window.Celebrate = { confetti(opts), countUp(el, target, ms, format) }.
// Both no-op into their final state under prefers-reduced-motion.
(function () {
  "use strict";

  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  var PALETTE = ["#a78bfa", "#6ee7b7", "#fbbf24", "#f87171", "#7c9cff", "#34d399"];

  // Full-viewport burst of paper from two side cannons. Each call gets its
  // own canvas, so overlapping bursts are safe; the canvas removes itself
  // when the last particle falls out of view (4s hard cap).
  function confetti(opts) {
    if (reducedMotion()) return;
    opts = opts || {};
    var count = opts.count || 140;

    var canvas = document.createElement("canvas");
    canvas.style.cssText = "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:2147483000;";
    document.body.appendChild(canvas);
    var ctx = canvas.getContext("2d");
    var W = (canvas.width = window.innerWidth);
    var H = (canvas.height = window.innerHeight);

    var particles = [];
    for (var i = 0; i < count; i++) {
      var fromLeft = i % 2 === 0;
      var angle = fromLeft ? -Math.PI / 4 : (-3 * Math.PI) / 4; // up & inward
      var spread = (Math.random() - 0.5) * (Math.PI / 3);
      var speed = 8 + Math.random() * 7;
      particles.push({
        x: W * (fromLeft ? 0.2 : 0.8),
        y: H * 0.85,
        vx: Math.cos(angle + spread) * speed,
        vy: Math.sin(angle + spread) * speed,
        rot: Math.random() * Math.PI * 2,
        vr: (Math.random() - 0.5) * 0.3,
        w: 6 + Math.random() * 5,
        h: 8 + Math.random() * 7,
        color: PALETTE[i % PALETTE.length],
      });
    }

    var start = performance.now();
    function frame(now) {
      ctx.clearRect(0, 0, W, H);
      var alive = false;
      for (var i = 0; i < particles.length; i++) {
        var p = particles[i];
        p.vy += 0.12;       // gravity
        p.vx *= 0.992;      // drag
        p.x += p.vx;
        p.y += p.vy;
        p.rot += p.vr;
        if (p.y < H + 30) {
          alive = true;
          ctx.save();
          ctx.translate(p.x, p.y);
          ctx.rotate(p.rot);
          ctx.fillStyle = p.color;
          ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
          ctx.restore();
        }
      }
      if (alive && now - start < 4000) {
        requestAnimationFrame(frame);
      } else {
        canvas.remove();
      }
    }
    requestAnimationFrame(frame);
  }

  // Animate el.textContent from 0 to target with an ease-out cubic. `format`
  // shapes the displayed value (e.g. v => v.toFixed(1) + '/5'); defaults to
  // rounding. Non-numeric targets are set directly.
  function countUp(el, target, ms, format) {
    if (!el) return;
    format = format || function (v) { return String(Math.round(v)); };
    var n = Number(target);
    if (!isFinite(n)) { el.textContent = String(target); return; }
    if (reducedMotion()) { el.textContent = format(n); return; }
    ms = ms || 900;
    var start = performance.now();
    function tick(now) {
      var t = Math.min(1, (now - start) / ms);
      var eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(n * eased);
      if (t < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  window.Celebrate = { confetti: confetti, countUp: countUp };
})();
