// Living background: a sparse, low-contrast particle field on one canvas.
// It only ever reflects real application state; app.js is the sole caller.
// Parameters are tweened with Motion (plain-value animate + onUpdate); the
// particles themselves are drawn in one rAF loop that stops when the tab is
// hidden and never starts under prefers-reduced-motion.

const M = window.Motion;
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export function createField(canvas) {
  const ctx = canvas.getContext("2d");
  // Live parameters. energy: drift speed; flow: horizontal current (-1 toward A, +1 toward B);
  // converge: pull toward the centre line; gap: how far short of the centre the pull stops;
  // disturb: brief jitter; pulse: refresh ripple (0..1); strength: overall visibility.
  const P = { energy: 1, flow: 0, converge: 0, gap: 0, disturb: 0, pulse: 0, strength: 0.55 };
  const tweens = {};
  const tween = (key, to, opts = {}) => {
    tweens[key]?.stop();
    if (!M || reduced()) { P[key] = to; return; }
    tweens[key] = M.animate(P[key], to, { duration: 1.2, ease: [0.16, 1, 0.3, 1], ...opts, onUpdate: (v) => { P[key] = v; } });
  };

  let W = 0, H = 0, dpr = 1, parts = [], ink = "237,241,243", running = false, last = 0, star = null, starTimer = 0;

  function readInk() {
    const hex = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim().replace("#", "");
    if (hex.length === 6) ink = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(",");
  }
  function resize() {
    dpr = Math.min(devicePixelRatio || 1, 1.5);
    W = innerWidth; H = innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    // ponytail: particle count scales with viewport area, hard-capped at 90; raise the cap only if profiling allows.
    const n = Math.min(90, Math.max(28, Math.round((W * H) / 20000)));
    while (parts.length < n) parts.push(spawn());
    parts.length = n;
    if (!running) draw(0);
  }
  function spawn() {
    const z = 0.25 + Math.random() * 0.75; // depth: far (small, dim, slow) to near
    return { x: Math.random(), y: Math.random(), z, vx: (Math.random() - 0.5) * 0.004, vy: (Math.random() - 0.5) * 0.003, tw: Math.random() * 6.28 };
  }

  function step(dt) {
    const e = P.energy, j = P.disturb;
    for (const p of parts) {
      p.x += (p.vx * e + P.flow * 0.012 * p.z) * dt;
      p.y += p.vy * e * dt;
      if (j > 0.001) { p.x += (Math.random() - 0.5) * 0.01 * j; p.y += (Math.random() - 0.5) * 0.01 * j; }
      if (p.x < -0.02) p.x += 1.04; else if (p.x > 1.02) p.x -= 1.04;
      if (p.y < -0.02) p.y += 1.04; else if (p.y > 1.02) p.y -= 1.04;
      p.tw += dt * 0.6;
    }
  }

  function draw(t) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const s = P.strength, scrollOff = scrollY * 0.06, c = P.converge;
    for (const p of parts) {
      const a = (0.16 + p.z * 0.55) * s * (0.75 + 0.25 * Math.sin(p.tw)); // ~1.6x the original star alpha
      let y = (p.y * H - scrollOff * p.z) % H; if (y < 0) y += H;
      // Convergence compresses the field toward the centre (reversibly, as a display transform);
      // a gap keeps the two halves apart, as when a negotiation times out.
      const off = p.x - 0.5, side = off < 0 ? -1 : 1;
      const x = c ? 0.5 + side * (P.gap * c + Math.abs(off) * (1 - 0.72 * c)) : p.x;
      ctx.fillStyle = `rgba(${ink},${a.toFixed(3)})`;
      ctx.beginPath(); ctx.arc(x * W, y, 0.45 + p.z * 1.05, 0, 6.2832); ctx.fill();
    }
    if (P.pulse > 0 && P.pulse < 1) {
      ctx.strokeStyle = `rgba(${ink},${(0.16 * (1 - P.pulse) * s).toFixed(3)})`; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(W * 0.5, H * 0.35, P.pulse * Math.max(W, H) * 0.6, 0, 6.2832); ctx.stroke();
    }
    if (star) {
      const { x, y, dx, dy, len, p } = star, hx = x + dx * p, hy = y + dy * p;
      const g = ctx.createLinearGradient(hx, hy, hx - dx * len, hy - dy * len);
      const fade = p < 0.8 ? 1 : (1 - p) / 0.2;
      g.addColorStop(0, `rgba(${ink},${(1.0 * s * fade).toFixed(3)})`); g.addColorStop(1, `rgba(${ink},0)`);
      ctx.strokeStyle = g; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(hx, hy); ctx.lineTo(hx - dx * len, hy - dy * len); ctx.stroke();
    }
  }

  function loop(t) {
    if (!running) return;
    const dt = Math.min(0.05, (t - (last || t)) / 1000); last = t;
    step(dt); draw(t);
    requestAnimationFrame(loop);
  }
  function start() {
    if (running || reduced() || document.hidden) return;
    running = true; last = 0; requestAnimationFrame(loop); scheduleStar();
  }
  function stop() { running = false; clearTimeout(starTimer); }

  // Occasional shooting star: rare, short, low contrast. Never under reduced motion.
  function scheduleStar() {
    clearTimeout(starTimer);
    starTimer = setTimeout(() => {
      if (!running || !M) return scheduleStar();
      const ang = (0.18 + Math.random() * 0.25) * Math.PI, dist = Math.max(W, H) * (0.25 + Math.random() * 0.2);
      star = { x: W * (0.1 + Math.random() * 0.7), y: H * Math.random() * 0.35, dx: Math.cos(ang) * dist, dy: Math.sin(ang) * dist, len: 0.35, p: 0 };
      M.animate(0, 1, { duration: 1.4, ease: [0.3, 0.1, 0.2, 1], onUpdate: (v) => { if (star) star.p = v; }, onComplete: () => { star = null; scheduleStar(); } });
    }, 9000 + Math.random() * 14000);
  }

  readInk(); resize();
  addEventListener("resize", resize);
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  new MutationObserver(() => { readInk(); if (!running) draw(0); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", () => (reduced() ? (stop(), draw(0)) : start()));
  start();

  let base = "idle", loading = false;
  const applyEnergy = () => tween("energy", loading ? 1.9 : base === "running" ? 1.45 : base === "complete" ? 0.5 : 1, { duration: 0.9 });
  return {
    /** Page context: on the Overview the hero video is the moving layer, so the field recedes there. */
    page(name) {
      tween("strength", name === "overview" ? 0.3 : name === "negotiation" ? 0.7 : 0.5, { duration: 0.8 });
      if (name !== "negotiation") { tween("flow", 0); tween("converge", 0); tween("gap", 0); }
    },
    /** Experiment state from the run records: idle | running | complete. */
    base(state) { if (state !== base) { base = state; applyEnergy(); } },
    /** A real fetch is in flight. */
    loading(on) { if (on !== loading) { loading = on; applyEnergy(); } },
    /** The stored data changed on refresh. */
    refresh() { P.pulse = 0; tween("pulse", 1, { duration: 2.2, ease: "easeOut" }); },
    /** A recorded turn by Agent A or B is shown: current flows toward the other agent. */
    turn(actor) { tween("flow", actor === "A" ? 1 : -1, { duration: 0.45 }); },
    /** The recorded outcome of the negotiation on screen. */
    outcome(o) {
      tween("flow", 0, { duration: 1.4 });
      if (o === "agreed") { tween("gap", 0); tween("converge", 0.9, { duration: 2.4 }); }
      else if (o === "timeout") { tween("gap", 0.12); tween("converge", 0.8, { duration: 2.4 }); }
      else if (o === "invalid_action") { P.disturb = 1; tween("disturb", 0, { duration: 1.1, ease: "easeOut" }); }
      else { tween("converge", 0); tween("flow", 0); }
    },
    reset() { tween("flow", 0); tween("converge", 0, { duration: 0.8 }); tween("gap", 0); },
  };
}
