// Live black-hole scene for the Overview hero. The supplied reference image is
// the unchanged base layer; everything alive is layered over it:
//   camera   - slow, non-repeating drift (Motion, random targets each leg)
//   lens     - a ring-masked copy of the image rotated by fractions of a degree
//              about the hole, so the photon ring and inner disc shear subtly
//   orbitals - dust and light on the disc's measured ellipse, Keplerian speeds
//              (inner faster), hidden behind the shadow on the far side
//   debris   - a rare, faint arc that follows an outer orbit
// Geometry was measured from the image (736x414 px): shadow centre (110, 120),
// shadow radius 122, disc tilt 16 deg, disc minor/major ratio 0.2.
// Speed and brightness follow the field's live state (energy, refresh pulse),
// which app.js sets only from real application events.

const M = window.Motion;
const IMG_W = 736, IMG_H = 414;
const HOLE = { x: 110, y: 120, r: 122 };
const TILT = (16 * Math.PI) / 180, FLAT = 0.2;
const COS = Math.cos(TILT), SIN = Math.sin(TILT);
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const rand = (a, b) => a + Math.random() * (b - a);

export function createBlackHole(scene, state) {
  const cam = scene.querySelector(".bh-cam"), lens = scene.querySelector(".bh-lens"), canvas = scene.querySelector(".bh-fx");
  const ctx = canvas.getContext("2d");
  let scale = 1, dpr = 1, running = false, visible = true, last = 0, debris = null, debrisTimer = 0;
  const live = []; // Motion controls to stop on destroy

  // --- cover the scene box with the image's exact aspect (hole kept in frame)
  function layout() {
    // Span from main's left edge to the viewport's right edge (clientWidth excludes the scrollbar, 100vw does not).
    scene.style.width = `${document.documentElement.clientWidth - scene.parentElement.getBoundingClientRect().left}px`;
    // End the scene just below the hero so the sections after it sit on plain ground.
    const hero = scene.parentElement.querySelector(".hero");
    if (hero) scene.style.height = `${Math.round(hero.getBoundingClientRect().bottom - scene.getBoundingClientRect().top + 90)}px`;
    const w = scene.clientWidth, h = scene.clientHeight;
    const s = Math.max(w / IMG_W, h / IMG_H) * 1.06; // 6% bleed so the drift never shows an edge
    const cw = IMG_W * s, ch = IMG_H * s;
    Object.assign(cam.style, { width: `${cw}px`, height: `${ch}px`, left: `${Math.min(0, (w - cw) * 0.15)}px`, top: `${(h - ch) * 0.4}px` });
    scale = s; dpr = Math.min(devicePixelRatio || 1, 1.25);
    // Lens copy only shows a band around the photon ring and inner disc, pivoting on the hole.
    const hx = HOLE.x * s, hy = HOLE.y * s;
    const mask = `radial-gradient(circle at ${hx}px ${hy}px, transparent ${112 * s}px, #000 ${132 * s}px, #000 ${190 * s}px, transparent ${260 * s}px)`;
    lens.style.maskImage = mask; lens.style.webkitMaskImage = mask;
    lens.style.transformOrigin = `${hx}px ${hy}px`;
    canvas.width = Math.round(cw * dpr); canvas.height = Math.round(ch * dpr);
    if (!running) draw(0);
  }

  // --- orbital population on the disc plane
  const orbit = (a) => 0.9 * Math.pow(a / 150, -1.5); // rad/s at energy 1 before global slow-down
  const bodies = [];
  for (let i = 0; i < 150; i++) {
    const a = 150 + Math.pow(Math.random(), 1.6) * 560;
    bodies.push({ a, b: a * FLAT * rand(0.9, 1.1), phi: rand(0, 6.283), len: rand(0.02, 0.07), w: rand(0.5, 1.3), alpha: rand(0.05, 0.22) });
  }
  const ring = [];
  for (let i = 0; i < 46; i++) ring.push({ r: rand(126, 152), phi: rand(0, 6.283), len: rand(0.05, 0.16), w: rand(0.5, 1.1), alpha: rand(0.06, 0.2) });

  function arcOnDisc(a, b, from, to) {
    ctx.beginPath();
    ctx.ellipse(HOLE.x * scale, HOLE.y * scale, a * scale, b * scale, TILT, from, to);
    ctx.stroke();
  }
  // Far side of the disc (sin < 0) is hidden where it passes behind the shadow.
  function occluded(a, b, phi) {
    if (Math.sin(phi) >= 0) return false;
    const x = a * Math.cos(phi), y = b * Math.sin(phi);
    const px = x * COS - y * SIN, py = x * SIN + y * COS;
    return Math.hypot(px, py) < HOLE.r;
  }

  function draw(dt) {
    const light = document.documentElement.dataset.theme === "light";
    const e = 0.55 + 0.45 * (state.energy ?? 1);                       // loading/running speed up, complete settles
    const boost = state.pulse > 0 && state.pulse < 1 ? 1 + 0.9 * (1 - state.pulse) : 1; // refresh pulse
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.globalCompositeOperation = light ? "multiply" : "lighter";
    const ink = light ? "20,24,27" : "255,255,255";
    ctx.lineCap = "round";
    for (const p of bodies) {
      p.phi += orbit(p.a) * e * dt * 0.12;
      if (occluded(p.a, p.b, p.phi)) continue;
      const near = Math.sin(p.phi) > 0 ? 1 : 0.55; // near side of the disc is brighter
      ctx.strokeStyle = `rgba(${ink},${(p.alpha * near * boost).toFixed(3)})`; ctx.lineWidth = p.w * Math.max(0.8, scale);
      arcOnDisc(p.a, p.b, p.phi, p.phi + p.len);
    }
    for (const q of ring) {
      q.phi += orbit(q.r) * e * dt * 0.1;
      ctx.strokeStyle = `rgba(${ink},${(q.alpha * boost).toFixed(3)})`; ctx.lineWidth = q.w * Math.max(0.8, scale);
      ctx.beginPath(); ctx.arc(HOLE.x * scale, HOLE.y * scale, q.r * scale, q.phi, q.phi + q.len); ctx.stroke();
    }
    if (debris) {
      const { a, b, start, span, p } = debris, head = start + span * p, fade = Math.sin(Math.PI * p);
      ctx.strokeStyle = `rgba(${ink},${(0.35 * fade).toFixed(3)})`; ctx.lineWidth = 1.2 * Math.max(0.8, scale);
      arcOnDisc(a, b, head - 0.18, head);
    }
    ctx.globalCompositeOperation = "source-over";
  }

  function loop(t) {
    if (!running) return;
    const dt = Math.min(0.05, (t - (last || t)) / 1000); last = t;
    draw(dt);
    requestAnimationFrame(loop);
  }

  // --- camera: each leg picks a new tiny target, so the path never repeats. Keyframes are explicit
  // (from the last target) so Motion never has to infer a starting transform.
  const camAt = { x: 0, y: 0, scale: 1 }, lensAt = { rotate: 0, opacity: 0.38 };
  function driftCamera() {
    if (!running || !M) return;
    const to = { x: rand(-9, 9), y: rand(-6, 6), scale: rand(1.0, 1.03) };
    const c = M.animate(cam, { x: [camAt.x, to.x], y: [camAt.y, to.y], scale: [camAt.scale, to.scale] }, { duration: rand(16, 26), ease: "easeInOut" });
    Object.assign(camAt, to); live.push(c); c.then(driftCamera);
  }
  // --- lens: sub-degree rotation of the ring-masked copy about the hole centre
  function shearLens() {
    if (!running || !M) return;
    const to = { rotate: rand(-0.7, 0.7), opacity: rand(0.28, 0.5) };
    const c = M.animate(lens, { rotate: [lensAt.rotate, to.rotate], opacity: [lensAt.opacity, to.opacity] }, { duration: rand(11, 19), ease: "easeInOut" });
    Object.assign(lensAt, to); live.push(c); c.then(shearLens);
  }
  // --- debris: rare faint arc along an outer orbit
  function scheduleDebris() {
    clearTimeout(debrisTimer);
    if (!running || !M) return; // no timers while paused; start() schedules again
    debrisTimer = setTimeout(() => {
      if (!running) return;
      const a = rand(360, 640);
      debris = { a, b: a * FLAT, start: rand(0.2, 1.2), span: rand(0.5, 0.9), p: 0 };
      live.push(M.animate(0, 1, { duration: rand(3.5, 5.5), ease: "easeInOut", onUpdate: (v) => { if (debris) debris.p = v; }, onComplete: () => { debris = null; scheduleDebris(); } }));
    }, rand(16000, 38000));
  }

  function start() {
    if (running || reduced() || document.hidden || !visible) return;
    running = true; last = 0; requestAnimationFrame(loop); driftCamera(); shearLens(); scheduleDebris();
  }
  // Completing (not stopping) the current legs while unseen keeps the next leg continuous with the last.
  function stop() { running = false; clearTimeout(debrisTimer); live.splice(0).forEach((c) => c.complete?.()); debris = null; }

  const onVis = () => (document.hidden ? stop() : start());
  const io = new IntersectionObserver(([en]) => { visible = en.isIntersecting; visible ? start() : stop(); });
  const ro = new ResizeObserver(layout);
  layout(); io.observe(scene); ro.observe(scene); addEventListener("resize", layout);
  document.addEventListener("visibilitychange", onVis);
  start();

  return () => { stop(); io.disconnect(); ro.disconnect(); removeEventListener("resize", layout); document.removeEventListener("visibilitychange", onVis); };
}
