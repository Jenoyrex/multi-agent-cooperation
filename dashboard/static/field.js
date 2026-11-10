// Living background: a galaxy scene on one canvas. Dark theme is deep space; light theme is a star atlas, ink stars on a pale nebula sky.
// Beyond the slow drift, its movement reflects real application state; app.js is the sole caller.
// Parameters are tweened with Motion (plain-value animate + onUpdate); the
// particles themselves are drawn in one rAF loop that stops when the tab is
// hidden and never starts under prefers-reduced-motion.

const M = window.Motion;
const reduced = () => document.documentElement.dataset.motion === "reduce" || matchMedia("(prefers-reduced-motion: reduce)").matches;

export function createField(canvas) {
  const ctx = canvas.getContext("2d");
  // Live parameters. energy: drift speed; flow: horizontal current (-1 toward A, +1 toward B);
  // converge: pull toward the centre line; gap: how far short of the centre the pull stops;
  // disturb: brief jitter; pulse: refresh ripple (0..1); strength: overall visibility.
  const P = { energy: 1, flow: 0, converge: 0, gap: 0, disturb: 0, pulse: 0, strength: 0.55, glow: 0, meet: 0 };
  const tweens = {};
  const tween = (key, to, opts = {}) => {
    tweens[key]?.stop();
    if (!M || reduced()) { P[key] = to; return; }
    tweens[key] = M.animate(P[key], to, { duration: 1.2, ease: [0.16, 1, 0.3, 1], ...opts, onUpdate: (v) => { P[key] = v; } });
  };

  let W = 0, H = 0, dpr = 1, parts = [], ink = "237,241,243", running = false, last = 0, star = null, starTimer = 0, stars = false, dark = false, spin = 0, px = 0, py = 0, tx = 0, ty = 0, galA = null, galB = null;

  function readInk() {
    dark = document.documentElement.dataset.theme === "dark";
    ink = dark ? "236,241,255" : "46,52,118";
    galA = galaxy(dark ? [255, 244, 228] : [112, 124, 240], dark ? [126, 172, 255] : [68, 86, 208]);
    galB = galaxy(dark ? [255, 232, 206] : [240, 134, 104], dark ? [255, 152, 112] : [212, 88, 62]);
  }

  /** One spiral galaxy, drawn once to its own canvas so that rotating it each frame is a single image draw. */
  function galaxy(core, arm) {
    const S = 720, R = S / 2, sprite = document.createElement("canvas");
    sprite.width = sprite.height = S;
    const g = sprite.getContext("2d"), css = (c, a) => `rgba(${c.join(",")},${a.toFixed(3)})`;
    const gauss = () => (Math.random() + Math.random() + Math.random() - 1.5) / 1.5;
    g.translate(R, R);
    g.globalCompositeOperation = dark ? "lighter" : "source-over";
    const glow = (r, c, a) => { const f = g.createRadialGradient(0, 0, 0, 0, 0, r); f.addColorStop(0, css(c, a)); f.addColorStop(1, css(c, 0)); g.fillStyle = f; g.fillRect(-r, -r, r * 2, r * 2); };
    glow(R, arm, dark ? 0.16 : 0.12);
    const star = dark ? [236, 241, 255] : [46, 52, 118];
    const dot = (x, y, c, a, s) => { g.fillStyle = css(c, a); g.beginPath(); g.arc(x, y, s, 0, 6.2832); g.fill(); };
    for (let a = 0; a < 2; a++) {
      for (let i = 0; i < 1500; i++) {
        const f = Math.pow(Math.random(), 1.35), r = (0.08 + f * 0.9) * R;
        const th = a * Math.PI + Math.log(1 + f * 8) * 2.4 + gauss() * (0.5 - f * 0.28);
        dot(r * Math.cos(th), r * Math.sin(th), Math.random() < 0.42 ? arm : star, (0.22 + Math.random() * 0.68) * (1 - f * 0.55), 0.5 + Math.random() * 1.1);
      }
    }
    for (let i = 0; i < 620; i++) { const r = Math.abs(gauss()) * R * 0.3, th = Math.random() * 6.2832; dot(r * Math.cos(th), r * Math.sin(th), core, 0.2 + Math.random() * 0.6, 0.5 + Math.random() * 0.9); }
    glow(R * 0.26, core, dark ? 0.9 : 0.5);
    return sprite;
  }
  function resize() {
    dpr = Math.min(devicePixelRatio || 1, 1.5);
    W = innerWidth; H = innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    // ponytail: star count scales with viewport area, hard-capped at 260; raise the cap only if profiling allows.
    const n = Math.min(260, Math.max(70, Math.round((W * H) / 6500)));
    while (parts.length < n) parts.push(spawn());
    parts.length = n;
    if (!running) draw(0);
  }
  function spawn() {
    const z = 0.25 + Math.random() * 0.75; // depth: far (small, dim, slow) to near
    // Every star drifts the same way, nearer ones faster, so the sky reads as depth rather than noise.
    return { x: Math.random(), y: Math.random(), z, vx: -(0.004 + z * 0.018), vy: 0.0015 + z * 0.005, tw: Math.random() * 6.28, rate: 1.2 + Math.random() * 3.4, big: Math.random() < 0.1 };
  }

  function step(dt) {
    spin += dt * 0.055 * P.energy;
    px += (tx - px) * Math.min(1, dt * 4); py += (ty - py) * Math.min(1, dt * 4);
    const e = P.energy, j = P.disturb;
    for (const p of parts) {
      p.x += (p.vx * e + P.flow * 0.012 * p.z) * dt;
      p.y += p.vy * e * dt;
      if (j > 0.001) { p.x += (Math.random() - 0.5) * 0.01 * j; p.y += (Math.random() - 0.5) * 0.01 * j; }
      if (p.x < -0.02) p.x += 1.04; else if (p.x > 1.02) p.x -= 1.04;
      if (p.y < -0.02) p.y += 1.04; else if (p.y > 1.02) p.y -= 1.04;
      p.tw += dt * p.rate;
    }
  }

  // Nebula colour behind everything. Summed unrelated sines never quite repeat, and the shortest cycle is
  // about 40 s: far from the ~5 s rhythm that makes full-screen motion uncomfortable.
  function cloud(cx, cy, r, rgb, a) {
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    g.addColorStop(0, `rgba(${rgb},${a.toFixed(3)})`); g.addColorStop(0.5, `rgba(${rgb},${(a * 0.4).toFixed(3)})`); g.addColorStop(1, `rgba(${rgb},0)`);
    ctx.fillStyle = g; ctx.fillRect(cx - r, cy - r, r * 2, r * 2);
  }
  function drawSky(t) {
    if (P.glow < 0.005) return;
    const s = t / 1000, a = P.glow * (dark ? 0.2 : 0.42), r = Math.max(W, H) * 0.5, m = P.meet * 0.16;
    const ax = 0.85 + 0.035 * Math.sin(s * 0.157) + 0.02 * Math.sin(s * 0.0671 + 1.3) - m, ay = 0.25 + 0.05 * Math.sin(s * 0.119 + 0.7) + m * 0.5;
    const bx = 0.09 + 0.03 * Math.sin(s * 0.137 + 2.1) + 0.02 * Math.sin(s * 0.0719) + m, by = 0.86 + 0.04 * Math.sin(s * 0.103 + 4) - m * 0.5;
    ctx.globalCompositeOperation = dark ? "lighter" : "source-over";
    cloud((ax + px * 0.004) * W, (ay + py * 0.004) * H, r, dark ? "48,96,226" : "160,170,255", a);
    cloud((bx + px * 0.004) * W, (by + py * 0.004) * H, r, dark ? "214,84,44" : "255,186,156", a * 0.85);
    cloud((0.5 + 0.1 * Math.sin(s * 0.091 + 1)) * W, (0.48 + 0.08 * Math.sin(s * 0.083)) * H, r * 0.8, dark ? "112,64,206" : "208,176,255", a * 0.7);
    // Two galaxies, one for each negotiating side. They turn opposite ways and close in while an experiment runs.
    // Each bright core starts outside the page column (main: 1120 wide, 16-40 px padding) so it never sits behind text; it dims as it closes in.
    const d = Math.max(W, H), alpha = Math.min(1, P.glow * (dark ? 0.85 : 0.62)) * (1 - P.meet * 0.5);
    const column = (Math.max(0, W - 1120) / 2 + Math.min(40, Math.max(16, W * 0.04))) / W;
    const place = (sprite, x, y, size, tilt, angle) => {
      ctx.save(); ctx.globalAlpha = alpha; ctx.translate(x * W, y * H); ctx.rotate(tilt); ctx.scale(1, 0.5); ctx.rotate(angle);
      ctx.drawImage(sprite, -size / 2, -size / 2, size, size); ctx.restore();
    };
    place(galA, Math.max(ax + m, 1 - column + d * 0.065 / W) - m + px * 0.012, ay + py * 0.012, d * 0.5, -0.42, spin);
    place(galB, Math.min(bx - m, column - d * 0.052 / W) + m + px * 0.01, by + py * 0.01, d * 0.4, 0.52, -spin * 1.2);
    ctx.globalCompositeOperation = "source-over";
  }

  function draw(t) {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    drawSky(t);
    const s = P.strength, scrollOff = scrollY * 0.06, c = P.converge;
    for (const p of parts) {
      const a = Math.min(1, (0.3 + p.z * 0.7) * s * (0.58 + 0.42 * Math.sin(p.tw)));
      let y = (p.y * H - scrollOff * p.z) % H; if (y < 0) y += H;
      // Convergence compresses the field toward the centre (reversibly, as a display transform);
      // a gap keeps the two halves apart, as when a negotiation times out.
      const off = p.x - 0.5, side = off < 0 ? -1 : 1;
      const x = c ? 0.5 + side * (P.gap * c + Math.abs(off) * (1 - 0.72 * c)) : p.x;
      ctx.fillStyle = `rgba(${ink},${a.toFixed(3)})`;
      const sx = x * W + px * 14 * p.z, sy = y + py * 14 * p.z, size = 0.5 + p.z * 1.2;
      ctx.beginPath(); ctx.arc(sx, sy, size, 0, 6.2832); ctx.fill();
      if (p.big) { const l = size * (3.2 + 2.4 * Math.sin(p.tw)); ctx.fillRect(sx - l, sy - 0.35, l * 2, 0.7); ctx.fillRect(sx - 0.35, sy - l, 0.7, l * 2); } // four-point glint
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
    }, stars ? 3000 + Math.random() * 6000 : 8000 + Math.random() * 12000);
  }

  readInk(); resize();
  addEventListener("resize", resize);
  if (matchMedia("(pointer: fine)").matches) addEventListener("pointermove", (e) => { tx = e.clientX / innerWidth - 0.5; ty = e.clientY / innerHeight - 0.5; }, { passive: true });
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  new MutationObserver(() => { readInk(); if (reduced()) stop(); else start(); if (!running) draw(0); }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-motion"] });
  matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", () => (reduced() ? (stop(), draw(0)) : start()));
  start();

  let base = "idle", loading = false, busy = false;
  const applyEnergy = () => tween("energy", loading ? 1.9 : busy ? 1.7 : base === "running" ? 1.45 : base === "complete" ? 0.5 : 1, { duration: 0.9 });
  return {
    /** Page context: strongest on Home, receding behind data so it never competes with reading. */
    page(name) {
      const replay = name === "negotiation", home = name === "";
      stars = home || replay; start();
      tween("strength", replay ? 0.8 : home ? 1 : 0.62, { duration: 0.8 });
      tween("glow", replay ? 0.3 : home ? 1 : 0.5, { duration: 1.2 });
      if (!replay) { tween("flow", 0); tween("converge", 0); tween("gap", 0); }
      if (!running) draw(0);
    },
    /** An experiment is running on this page: the two sides drift toward each other. */
    busy(on) { if (on !== busy) { busy = on; applyEnergy(); tween("meet", on ? 1 : 0, { duration: 2.4 }); } },
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
