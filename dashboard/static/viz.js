// Analytics charts for the Workbench (SVG strings). Shared conventions with charts.js:
// every mark carries data-tip (hover + keyboard focus via the global tooltip layer),
// series marks carry data-s so an interactive legend can hide them, marks for one
// negotiation carry data-trial so a click opens its transcript, and every chart is
// paired with a data table by its caller. Nothing here computes a statistic: callers
// pass values already derived from stored records (lib.js, or the server's analytics).
import { esc, fmt } from "./lib.js";

const W = typeof innerWidth !== "undefined" && innerWidth < 600 ? 360 : 560;
const tab = (n) => (n <= 120 ? ' tabindex="0"' : "");
const svg = (w, h, label, body) => `<svg class="viz" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}; see data table">${body}</svg>`;
const pctT = (v) => `${Math.round(v * 100)}%`;

/** Legend whose items toggle their series on and off (aria-pressed). */
export const toggleLegend = (items) =>
  `<div class="legend ilegend" role="group" aria-label="Show or hide series">${items.map(([key, label, color]) =>
    `<button type="button" data-series="${esc(key)}" aria-pressed="true"><i class="sw" style="--c:${color}"></i>${esc(label)}</button>`).join("")}</div>`;

/** Donut of parts [{key, label, count, color}] with the total in the middle. */
export function donut(parts, { label = "Outcomes", unit = "negotiations" } = {}) {
  const total = parts.reduce((a, p) => a + p.count, 0), cx = 100, cy = 100, r = 84, ir = 56;
  if (!total) return svg(200, 200, label, `<circle cx="${cx}" cy="${cy}" r="${(r + ir) / 2}" fill="none" stroke="var(--sunk)" stroke-width="${r - ir}"/><text x="${cx}" y="${cy + 4}" text-anchor="middle">no data</text>`);
  let a0 = -Math.PI / 2, out = "";
  const pt = (a, rad) => `${cx + rad * Math.cos(a)} ${cy + rad * Math.sin(a)}`;
  parts.filter((p) => p.count).forEach((p, i) => {
    const a1 = a0 + (p.count / total) * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0;
    const tip = `${p.label}: ${p.count} of ${total} (${pctT(p.count / total)})`;
    // A single 100% part is a whole ring: a near-closed arc is ambiguous in SVG, so draw it as a stroked circle.
    out += p.count === total
      ? `<circle cx="${cx}" cy="${cy}" r="${(r + ir) / 2}" data-anim="dot" style="fill:none;stroke:${p.color};stroke-width:${r - ir}" data-tip="${esc(tip)}" data-filter-outcome="${esc(p.key)}" tabindex="0"/>`
      : `<path d="M ${pt(a0, r)} A ${r} ${r} 0 ${big} 1 ${pt(a1, r)} L ${pt(a1, ir)} A ${ir} ${ir} 0 ${big} 0 ${pt(a0, ir)} Z" class="mark" data-anim="dot" style="fill:${p.color};--i:${i}" data-tip="${esc(tip)}" data-filter-outcome="${esc(p.key)}" tabindex="0"/>`;
    a0 = a1;
  });
  out += `<text class="big" x="${cx}" y="${cy + 6}" text-anchor="middle">${total}</text><text x="${cx}" y="${cy + 24}" text-anchor="middle">${esc(unit)}</text>`;
  return svg(200, 200, label, out);
}

/** Lines over integer x: series [{key, label, color, points:[{x, y, n}]}]. */
export function lineChart(series, { yDomain = [0, 1], xLabel = "", yLabel = "", yFmt = pctT } = {}) {
  const L = 44, R = 16, T = 10, B = xLabel ? 40 : 24, H = 230, pw = W - L - R, ph = H - T - B;
  const xs = series.flatMap((s) => s.points.map((p) => p.x)), x0 = Math.min(1, ...xs), x1 = Math.max(x0 + 1, ...xs);
  const X = (x) => L + ((x - x0) / (x1 - x0)) * pw, Y = (y) => T + ph - ((y - yDomain[0]) / (yDomain[1] - yDomain[0])) * ph;
  let out = "";
  for (let i = 0; i <= 4; i++) { const v = yDomain[0] + (i / 4) * (yDomain[1] - yDomain[0]); out += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${yFmt(v)}</text>`; }
  for (let x = x0; x <= x1; x++) out += `<text x="${X(x)}" y="${T + ph + 16}" text-anchor="middle">${x}</text>`;
  out += `<line class="axis" x1="${L}" x2="${W - R}" y1="${T + ph}" y2="${T + ph}"/>`;
  if (xLabel) out += `<text class="axt" x="${L + pw / 2}" y="${H - 4}" text-anchor="middle">${esc(xLabel)}</text>`;
  if (yLabel) out += `<text class="axt" transform="translate(11 ${T + ph / 2}) rotate(-90)" text-anchor="middle">${esc(yLabel)}</text>`;
  let i = 0;
  const placed = []; // end labels, nudged apart so converging lines never print over each other
  for (const s of series) {
    const pts = s.points.filter((p) => p.y !== null && p.y !== undefined);
    if (!pts.length) continue;
    let ly = Y(pts.at(-1).y) - 8;
    while (placed.some((y) => Math.abs(y - ly) < 14)) ly += 14;
    placed.push(ly);
    out += `<g data-s="${esc(s.key)}"><path class="line" data-anim="line" d="${pts.map((p, k) => `${k ? "L" : "M"}${X(p.x)} ${Y(p.y)}`).join(" ")}" style="stroke:${s.color}"/>`;
    for (const p of pts) out += `<circle class="mark" data-anim="dot" style="fill:${s.color};--i:${i++}" cx="${X(p.x)}" cy="${Y(p.y)}" r="4.5" data-tip="${esc(`${s.label}, offer ${p.x}: ${yFmt(p.y)}${p.n != null ? ` (n = ${p.n})` : ""}`)}" tabindex="0"/>`;
    const last = pts.at(-1);
    out += `<text class="val" x="${Math.min(X(last.x) + 7, W - R)}" y="${ly}" text-anchor="${X(last.x) > W - 90 ? "end" : "start"}">${esc(s.label)}</text></g>`;
  }
  return svg(W, H, `Line chart${yLabel ? ` of ${yLabel}` : ""}`, out);
}

/** Scatter of [{x, y, color, s, tip, trial}] on [0,1]²; identical points merge and grow, so none hides another. */
export function scatter(points, { xLabel = "", yLabel = "", diagonal = "" } = {}) {
  const L = 46, R = 14, T = 10, B = 42, size = Math.min(W, 420), H = size, pw = size - L - R, ph = H - T - B;
  const X = (v) => L + v * pw, Y = (v) => T + ph - v * ph;
  let out = "";
  for (let i = 0; i <= 4; i++) {
    const v = i / 4;
    out += `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="${T}" y2="${T + ph}"/><line class="grid" x1="${L}" x2="${L + pw}" y1="${Y(v)}" y2="${Y(v)}"/>`;
    out += `<text x="${X(v)}" y="${T + ph + 16}" text-anchor="middle">${fmt(v, 2)}</text><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${fmt(v, 2)}</text>`;
  }
  if (diagonal) out += `<line class="zero" x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}"/><text x="${X(0.97)}" y="${Y(0.97) + 16}" text-anchor="end">${esc(diagonal)}</text>`;
  out += `<text class="axt" x="${L + pw / 2}" y="${H - 6}" text-anchor="middle">${esc(xLabel)}</text><text class="axt" transform="translate(12 ${T + ph / 2}) rotate(-90)" text-anchor="middle">${esc(yLabel)}</text>`;
  const groups = new Map();
  for (const p of points) {
    const k = `${p.s}|${p.x.toFixed(3)}|${p.y.toFixed(3)}`;
    const g = groups.get(k) ?? { ...p, n: 0, trials: [] };
    g.n += 1; g.trials.push(p.trial); groups.set(k, g);
  }
  let i = 0;
  for (const g of groups.values()) {
    const tip = g.n > 1 ? `${g.tip}\n${g.n} negotiations at this point (#${g.trials.slice(0, 6).map((t) => t + 1).join(", #")}${g.n > 6 ? "…" : ""})` : g.tip;
    // Later series are drawn as rings, so a point shared with an earlier series stays visible around it.
    const look = g.s === points[0].s ? `fill:${g.color};fill-opacity:.85` : `fill:none;stroke:${g.color};stroke-width:2.5`;
    out += `<circle class="mark" data-anim="dot" data-s="${esc(g.s)}" data-trial="${g.trials[0]}" style="${look};--i:${i++}" cx="${X(g.x)}" cy="${Y(g.y)}" r="${Math.min(14, 4.5 + 2.2 * Math.sqrt(g.n - 1))}" data-tip="${esc(tip)}"${tab(groups.size)}/>`;
  }
  return svg(size, H, `Scatter plot of ${xLabel} against ${yLabel}`, out);
}

/** Horizontal box plots: groups [{label, color, stats}] where stats = boxStats(...) or null. */
export function boxPlot(groups, { domain = [0, 1], axis = "", tickFmt = (v) => fmt(v, 2) } = {}) {
  const L = Math.max(96, Math.max(...groups.map((g) => g.label.length)) * 6.6 + 16), R = 16, row = 46, H = groups.length * row + (axis ? 44 : 28);
  const X = (v) => L + ((v - domain[0]) / (domain[1] - domain[0])) * (W - L - R), plot = groups.length * row;
  let out = "";
  for (let i = 0; i <= 4; i++) { const v = domain[0] + (i / 4) * (domain[1] - domain[0]); out += `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="0" y2="${plot}"/><text x="${X(v)}" y="${plot + 16}" text-anchor="middle">${tickFmt(v)}</text>`; }
  if (axis) out += `<text class="axt" x="${L + (W - L - R) / 2}" y="${plot + 36}" text-anchor="middle">${esc(axis)}</text>`;
  groups.forEach((g, gi) => {
    const y = gi * row + row / 2, s = g.stats;
    out += `<text class="lbl" x="0" y="${y - 2}">${esc(g.label)}</text><text x="0" y="${y + 13}">n = ${s ? s.n : 0}</text>`;
    if (!s) { out += `<text x="${L}" y="${y + 4}">no observations</text>`; return; }
    const tip = `${g.label} (n = ${s.n})\nmedian ${tickFmt(s.median)} · mean ${tickFmt(s.mean)}\nmiddle half ${tickFmt(s.q1)}–${tickFmt(s.q3)} · range ${tickFmt(s.min)}–${tickFmt(s.max)}`;
    out += `<g data-s="${esc(g.key ?? g.label)}"><line class="whisk" x1="${X(s.lo)}" x2="${X(s.hi)}" y1="${y}" y2="${y}" style="stroke:${g.color}"/>`;
    out += `<rect class="box" data-anim="bar" x="${X(s.q1)}" y="${y - 11}" width="${Math.max(2, X(s.q3) - X(s.q1))}" height="22" rx="4" style="fill:color-mix(in srgb, ${g.color} 22%, var(--panel));stroke:${g.color}" data-tip="${esc(tip)}" tabindex="0"/>`;
    out += `<line class="mean" x1="${X(s.median)}" x2="${X(s.median)}" y1="${y - 11}" y2="${y + 11}"/>`;
    s.outliers.forEach((o) => { out += `<circle cx="${X(o)}" cy="${y}" r="3" style="fill:none;stroke:${g.color}" data-tip="${esc(`${g.label}: outlier ${tickFmt(o)}`)}"/>`; });
    out += `</g>`;
  });
  return svg(W, H, `Box plots${axis ? ` of ${axis}` : ""}`, out);
}

/** Heatmap: rows × cols, cell(r, c) -> {v in [0,1] | null, tip}. Five sequential steps of one hue. */
export function heatmap(rows, cols, cell, { label = "Heatmap" } = {}) {
  const L = Math.max(90, Math.max(...rows.map((r) => r.label.length)) * 6.6 + 14), T = 26, ch = 34, cw = Math.max(52, (W - L) / cols.length);
  const H = T + rows.length * ch + 4, w = L + cols.length * cw;
  let out = cols.map((c, j) => `<text class="lbl" x="${L + j * cw + cw / 2}" y="16" text-anchor="middle">${esc(c.label.length > 12 ? `${c.label.slice(0, 11)}…` : c.label)}</text>`).join("");
  rows.forEach((r, i) => {
    out += `<text class="lbl" x="0" y="${T + i * ch + ch / 2 + 4}">${esc(r.label)}</text>`;
    cols.forEach((c, j) => {
      const { v, tip } = cell(r, c), step = v == null ? null : Math.min(4, Math.floor(v * 5));
      out += `<rect x="${L + j * cw + 1}" y="${T + i * ch + 1}" width="${cw - 2}" height="${ch - 2}" rx="5" style="fill:${step == null ? "var(--panel-2)" : `var(--seq-${step})`}" data-tip="${esc(tip)}" tabindex="0"/>`;
      out += `<text x="${L + j * cw + cw / 2}" y="${T + i * ch + ch / 2 + 4}" text-anchor="middle" style="fill:${step == null ? "var(--muted)" : step >= 3 ? "#fff" : "var(--ink)"};font-weight:600">${v == null ? "—" : pctT(v)}</text>`;
    });
  });
  return svg(w, H, label, out);
}

/** Radar over axes [{key, label}] for series [{key, label, color, values}]; axes with a missing value in any series are dropped. */
export function radar(axes, series) {
  const use = axes.filter((a) => series.every((s) => s.values[a.key] != null));
  const S = 300, c = S / 2, r = 98;
  if (use.length < 3) return `<p class="note">Too few measured dimensions for a profile (needs 3, has ${use.length}).</p>`;
  const P = (i, v) => { const a = -Math.PI / 2 + (i / use.length) * Math.PI * 2; return [c + Math.cos(a) * r * v, c + Math.sin(a) * r * v]; };
  let out = "";
  [0.25, 0.5, 0.75, 1].forEach((v) => { out += `<polygon class="grid" fill="none" points="${use.map((_, i) => P(i, v).join(",")).join(" ")}"/>`; });
  use.forEach((a, i) => {
    const [x, y] = P(i, 1), [lx, ly] = P(i, 1.2);
    out += `<line class="grid" x1="${c}" y1="${c}" x2="${x}" y2="${y}"/><text class="lbl" x="${lx}" y="${ly + 4}" text-anchor="${Math.abs(lx - c) < 8 ? "middle" : lx > c ? "start" : "end"}">${esc(a.label)}</text>`;
  });
  let k = 0;
  for (const s of series) {
    out += `<g data-s="${esc(s.key)}"><polygon points="${use.map((a, i) => P(i, s.values[a.key]).join(",")).join(" ")}" style="fill:${s.color};fill-opacity:.14;stroke:${s.color};stroke-width:2"/>`;
    use.forEach((a, i) => { const [x, y] = P(i, s.values[a.key]); out += `<circle class="mark" data-anim="dot" style="fill:${s.color};--i:${k++}" cx="${x}" cy="${y}" r="4" data-tip="${esc(`${s.label}, ${a.label}: ${pctT(s.values[a.key])}`)}" tabindex="0"/>`; });
    out += "</g>";
  }
  return `<svg class="viz radar" viewBox="-70 -10 ${S + 140} ${S + 20}" role="img" aria-label="Radar profile; see data table">${out}</svg>`;
}

/** Sankey: stages [[{id, label, color}]], links [{stage, from, to, n}] between stage i and i+1. */
export function sankey(stages, links, { label = "Flow" } = {}) {
  const H = 260, nw = 12, gap = 10, colX = (i) => 4 + (i / (stages.length - 1)) * (W - 130 - nw);
  const total = links.filter((l) => l.stage === 0).reduce((a, l) => a + l.n, 0);
  if (!total) return `<p class="note">No completed negotiations to trace.</p>`;
  const nodes = new Map();
  stages.forEach((col, i) => {
    const val = (n) => Math.max(links.filter((l) => l.stage === i && l.from === n.id).reduce((a, l) => a + l.n, 0), links.filter((l) => l.stage === i - 1 && l.to === n.id).reduce((a, l) => a + l.n, 0));
    const live = col.map((n) => ({ ...n, v: val(n) })).filter((n) => n.v);
    const k = (H - gap * (live.length - 1)) / total;
    let y = 0;
    live.forEach((n) => { nodes.set(`${i}:${n.id}`, { ...n, x: colX(i), y, h: n.v * k, out: 0, in: 0, k }); y += n.v * k + gap; });
  });
  let out = "";
  links.forEach((l, i) => {
    const a = nodes.get(`${l.stage}:${l.from}`), b = nodes.get(`${l.stage + 1}:${l.to}`);
    if (!a || !b) return;
    const h = l.n * a.k, y0 = a.y + a.out, y1 = b.y + b.in, x0 = a.x + nw, x1 = b.x, m = (x0 + x1) / 2;
    a.out += h; b.in += l.n * b.k;
    out += `<path class="flow" data-anim="dot" style="fill:${a.color};--i:${i}" d="M${x0} ${y0} C${m} ${y0} ${m} ${y1} ${x1} ${y1} L${x1} ${y1 + h} C${m} ${y1 + h} ${m} ${y0 + h} ${x0} ${y0 + h} Z" data-tip="${esc(`${a.label} → ${b.label}: ${l.n} negotiation${l.n === 1 ? "" : "s"}`)}" tabindex="0"/>`;
  });
  for (const n of nodes.values()) {
    out += `<rect x="${n.x}" y="${n.y}" width="${nw}" height="${Math.max(1, n.h)}" rx="3" style="fill:${n.color}"/>`;
    out += `<text class="lbl" x="${n.x + nw + 6}" y="${n.y + Math.min(n.h / 2, 12) + 4}">${esc(n.label)}</text><text x="${n.x + nw + 6}" y="${n.y + Math.min(n.h / 2, 12) + 18}">${n.v}</text>`;
  }
  return svg(W, H + 6, label, out);
}

/** Bipartite network: agents [{key, label, color}] on the edges, resources [{id, label}] in the middle,
 *  edges [{agent, resource, w in [0,1], tip}] drawn with thickness by w. */
export function network(agents, resources, edges) {
  const H = Math.max(200, resources.length * 52 + 40), ax = [70, W - 70], rx = W / 2;
  const ry = (i) => 30 + (i + 0.5) * ((H - 60) / resources.length), ay = H / 2;
  let out = "";
  edges.forEach((e, i) => {
    const ai = agents.findIndex((a) => a.key === e.agent), ri = resources.findIndex((r) => r.id === e.resource);
    if (ai < 0 || ri < 0 || e.w == null) return;
    const x0 = ax[ai] + (ai ? -22 : 22), y1 = ry(ri), m = (x0 + rx) / 2;
    out += `<path class="edge" data-s="${esc(e.agent)}" data-anim="dot" style="stroke:${agents[ai].color};stroke-width:${1 + 10 * e.w};--i:${i}" d="M${x0} ${ay} C${m} ${ay} ${m} ${y1} ${rx + (ai ? 34 : -34)} ${y1}" data-tip="${esc(e.tip)}" tabindex="0"/>`;
  });
  resources.forEach((r, i) => { out += `<rect x="${rx - 34}" y="${ry(i) - 14}" width="68" height="28" rx="14" class="node"/><text class="lbl" x="${rx}" y="${ry(i) + 4}" text-anchor="middle">${esc(r.label.length > 10 ? `${r.label.slice(0, 9)}…` : r.label)}</text>`; });
  agents.forEach((a, i) => { out += `<circle cx="${ax[i]}" cy="${ay}" r="22" style="fill:${a.color}"/><text x="${ax[i]}" y="${ay + 5}" text-anchor="middle" style="fill:#fff;font-weight:700;font-size:14px">${esc(a.key)}</text><text class="lbl" x="${ax[i]}" y="${ay + 42}" text-anchor="middle">${esc(a.label.length > 18 ? `${a.label.slice(0, 17)}…` : a.label)}</text>`; });
  return svg(W, H, "Network of agents and the resources they ended with", out);
}

const ACT_FILL = { OFFER: null, ACCEPT: "var(--good)", WALK_AWAY: "var(--warning)", INVALID: "var(--critical)" };
/** One row per negotiation, one cell per turn: who moved and what they did. rows [{trial, label, cells:[{action, color, tip}]}]. */
export function timeline(rows, maxTurns) {
  const L = 48, ch = 15, gap = 3, cw = Math.max(10, Math.min(30, (W - L) / maxTurns - 2)), H = 22 + rows.length * (ch + gap);
  let out = "";
  for (let t = 1; t <= maxTurns; t++) out += `<text x="${L + (t - 1) * (cw + 2) + cw / 2}" y="12" text-anchor="middle">${t}</text>`;
  rows.forEach((r, i) => {
    const y = 20 + i * (ch + gap);
    out += `<g class="tlrow" data-trial="${r.trial}"><text x="0" y="${y + 11}">${esc(r.label)}</text>`;
    r.cells.forEach((c, t) => {
      const fill = ACT_FILL[c.action] ?? c.color;
      out += c.action === "OFFER"
        ? `<rect x="${L + t * (cw + 2)}" y="${y}" width="${cw}" height="${ch}" rx="3" style="fill:${fill}" data-tip="${esc(c.tip)}"/>`
        : `<rect x="${L + t * (cw + 2)}" y="${y}" width="${cw}" height="${ch}" rx="3" style="fill:var(--panel);stroke:${fill};stroke-width:2" data-tip="${esc(c.tip)}"/>`;
    });
    out += `</g>`;
  });
  return svg(Math.max(W, L + maxTurns * (cw + 2)), H, "Turn-by-turn timeline of each negotiation", out);
}

/** Stacked area of running shares: rows [{k, ...counts}], keys [{key, label, color}]. */
export function stackedArea(rows, keys, { xLabel = "" } = {}) {
  const L = 40, R = 12, T = 8, B = xLabel ? 38 : 22, H = 200, pw = W - L - R, ph = H - T - B, n = rows.length;
  if (!n) return `<p class="note">No completed negotiations yet.</p>`;
  const X = (k) => L + (n === 1 ? pw / 2 : ((k - 1) / (n - 1)) * pw), Y = (v) => T + ph - v * ph;
  let out = "";
  [0, 0.5, 1].forEach((v) => { out += `<line class="grid" x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end">${pctT(v)}</text>`; });
  const base = rows.map(() => 0);
  keys.forEach((key) => {
    const lo = [...base], hi = rows.map((r, i) => (base[i] += r[key.key] / r.k));
    if (!rows.some((r) => r[key.key])) return;
    const top = rows.map((r, i) => `${X(r.k)} ${Y(hi[i])}`), bottom = rows.map((r, i) => `${X(r.k)} ${Y(lo[i])}`).reverse();
    out += `<path data-s="${esc(key.key)}" class="area" style="fill:${key.color}" d="M${top.join(" L")} L${bottom.join(" L")} Z"/>`;
  });
  rows.forEach((r) => {
    const x = X(r.k), w = n === 1 ? pw : pw / (n - 1);
    out += `<rect class="hit" x="${x - w / 2}" y="${T}" width="${w}" height="${ph}" data-tip="${esc(`After ${r.k} negotiation${r.k === 1 ? "" : "s"}\n${keys.map((k) => `${k.label}: ${r[k.key]}`).join("\n")}`)}"/>`;
  });
  out += `<text x="${L}" y="${T + ph + 15}">1</text><text x="${W - R}" y="${T + ph + 15}" text-anchor="end">${n}</text>`;
  if (xLabel) out += `<text class="axt" x="${L + pw / 2}" y="${H - 4}" text-anchor="middle">${esc(xLabel)}</text>`;
  return svg(W, H, "Running outcome shares", out);
}

/** Grouped horizontal bars for comparing a measure across a few groups: rows [{label, bars:[{key, label, v, color, tip}]}]. */
export function groupedBars(rows, { domain = [0, 1], tickFmt = pctT } = {}) {
  const L = Math.max(110, Math.max(...rows.map((r) => r.label.length)) * 6.6 + 16), R = 46, bh = 14, per = Math.max(...rows.map((r) => r.bars.length)), rowH = per * (bh + 4) + 16;
  const H = rows.length * rowH + 22, X = (v) => L + ((v - domain[0]) / (domain[1] - domain[0])) * (W - L - R);
  let out = "";
  for (let i = 0; i <= 4; i++) { const v = domain[0] + (i / 4) * (domain[1] - domain[0]); out += `<line class="grid" x1="${X(v)}" x2="${X(v)}" y1="0" y2="${H - 22}"/><text x="${X(v)}" y="${H - 6}" text-anchor="middle">${tickFmt(v)}</text>`; }
  let k = 0;
  rows.forEach((r, i) => {
    const y = i * rowH + 6;
    out += `<text class="lbl" x="0" y="${y + 12}">${esc(r.label)}</text>`;
    r.bars.forEach((b, j) => {
      const by = y + j * (bh + 4);
      if (b.v == null) { out += `<text data-s="${esc(b.key)}" x="${L + 4}" y="${by + 11}">not measured</text>`; return; }
      out += `<g data-s="${esc(b.key)}"><rect data-anim="bar" style="fill:${b.color};--i:${k++}" x="${X(domain[0])}" y="${by}" width="${Math.max(1, X(b.v) - X(domain[0]))}" height="${bh}" rx="4" data-tip="${esc(b.tip)}" tabindex="0"/>`;
      out += `<text class="val" x="${X(b.v) + 5}" y="${by + 11}">${tickFmt(b.v)}</text></g>`;
    });
  });
  return svg(W, H, "Grouped bar chart", out);
}
