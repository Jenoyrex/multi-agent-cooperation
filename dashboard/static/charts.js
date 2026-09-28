// SVG research figures (strings). Every mark carries data-tip for the hover
// and focus layer; every figure ships a data table for keyboard/AT readers.
import { dotStack, esc, fmt, OUTCOME_LABEL } from "./lib.js";

// Narrow screens get a narrower coordinate space so figure text is not scaled below ~11px.
const W = typeof innerWidth !== "undefined" && innerWidth < 600 ? 340 : 480;
const focusable = (n) => (n <= 80 ? ' tabindex="0"' : "");

/** Figure with a numbered caption underneath, as in a paper. */
export function figure(num, title, caption, svg, legend = "", table = "") {
  return `<figure class="fig">${legend}${svg}
    <figcaption><b>Figure ${num}.</b> <b>${esc(title)}.</b> ${caption}</figcaption>${table}</figure>`;
}

export const legend = (items) =>
  `<div class="legend" aria-hidden="true">${items.map(([label, color]) => `<span><i class="sw" style="--c:${color}"></i>${esc(label)}</span>`).join("")}</div>`;

export function dataTable(headers, rows) {
  return `<details class="data"><summary>Data table</summary><div class="table-wrap"><table>
    <thead><tr>${headers.map((h, i) => `<th${i ? ' class="num"' : ""}>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ""}>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div></details>`;
}

/**
 * Horizontal dot plot, one row per group: [{label, color, points:[{v, tip}], mean}].
 * Stacked dots keep ties visible; a tick and a direct label mark the mean.
 */
export function dotPlot(groups, { domain = [0, 1], bin = 0.02, ticks = 5, zero = false, fmtTick = (x) => fmt(x, 1), meanFmt = (x) => fmt(x), width = W, axis = "" } = {}) {
  // Label gutter sized to the longest group label (~6.6px per condensed glyph at 13px).
  const L = Math.max(108, Math.max(...groups.map((g) => g.label.length)) * 6.6 + 18), R = 14, r = 4.5, step = 2 * r + 1;
  const x = (v) => L + ((v - domain[0]) / (domain[1] - domain[0])) * (width - L - R);
  const laid = groups.map((g) => ({ ...g, dots: dotStack(g.points.map((p) => p.v), bin) }));
  const heights = laid.map((g) => Math.max(40, (Math.max(0, ...g.dots.map((d) => d.level)) + 1) * step + 24));
  const plotH = heights.reduce((a, b) => a + b, 0);
  const H = plotH + (axis ? 44 : 28);
  const total = groups.reduce((a, g) => a + g.points.length, 0);
  let y0 = 0, i = 0, out = "";
  for (let t = 0; t <= ticks; t++) {
    const v = domain[0] + ((domain[1] - domain[0]) * t) / ticks;
    out += `<line class="grid" x1="${x(v)}" x2="${x(v)}" y1="0" y2="${plotH}"/><line class="axis" x1="${x(v)}" x2="${x(v)}" y1="${plotH}" y2="${plotH + 5}"/><text x="${x(v)}" y="${plotH + 18}" text-anchor="middle">${fmtTick(v)}</text>`;
  }
  out += `<line class="axis" x1="${L}" x2="${width - R}" y1="${plotH}" y2="${plotH}"/>`;
  if (axis) out += `<text class="axt" x="${L + (width - L - R) / 2}" y="${plotH + 38}" text-anchor="middle">${esc(axis)}</text>`;
  if (zero) out += `<line class="zero" data-anim="zero" x1="${x(0)}" x2="${x(0)}" y1="0" y2="${plotH}"/>`;
  laid.forEach((g, gi) => {
    const base = y0 + heights[gi] - 12;
    out += `<text class="lbl" x="0" y="${base - 3}">${esc(g.label)}</text><text x="0" y="${base + 12}">n = ${g.points.length}</text>`;
    g.dots.forEach((d, k) => {
      const pt = g.points[k];
      out += `<circle class="mark" data-anim="dot" style="fill:${pt.color ?? g.color};--i:${i++}" cx="${x(d.bin)}" cy="${base - d.level * step}" r="${r}" data-tip="${esc(pt.tip)}"${focusable(total)}/>`;
    });
    if (g.mean !== null && g.mean !== undefined) {
      const top = base - (Math.max(0, ...g.dots.map((d) => d.level)) + 1) * step - 2;
      const mx = x(g.mean), end = mx > width - 80;
      out += `<line class="mean" data-anim="mean" x1="${mx}" x2="${mx}" y1="${top}" y2="${base + r + 3}"/><text class="val" data-anim="mean" x="${mx + (end ? -5 : 5)}" y="${top + 9}" text-anchor="${end ? "end" : "start"}">mean ${meanFmt(g.mean)}</text>`;
    }
    y0 += heights[gi];
  });
  return `<svg class="viz" viewBox="0 -8 ${width} ${H + 8}" role="img" aria-label="Dot plot${axis ? ` of ${esc(axis)}` : ""}; see data table">${out}</svg>`;
}

/** 100% stacked horizontal bars: [{label, n, parts:[{key, count, share, color}]}]. */
export function stackBars(rows) {
  const L = 108, R = 14, h = 22, gap = 18;
  const H = rows.length * (h + gap) + 14;
  const total = W - L - R;
  let out = "", i = 0;
  [0, .25, .5, .75, 1].forEach((t) => { out += `<line class="grid" x1="${L + t * total}" x2="${L + t * total}" y1="0" y2="${H - 18}"/><text x="${L + t * total}" y="${H - 4}" text-anchor="middle">${t * 100}%</text>`; });
  rows.forEach((row, ri) => {
    const y = ri * (h + gap);
    out += `<text class="lbl" x="0" y="${y + 15}">${esc(row.label)}</text>`;
    if (!row.n) { out += `<text x="${L}" y="${y + 15}">no observations</text>`; return; }
    let cx = L;
    row.parts.forEach((p) => {
      if (!p.count) return;
      const w = Math.max(0, p.share * total - 2);
      const tip = `${row.label}, ${OUTCOME_LABEL[p.key] ?? p.key}: ${p.count} of ${row.n} (${(p.share * 100).toFixed(0)}%)`;
      out += `<rect class="bar-h" data-anim="bar" style="fill:${p.color};--i:${i++}" x="${cx}" y="${y}" width="${w}" height="${h}" rx="2" data-tip="${esc(tip)}" tabindex="0"/>`;
      if (w > 30) out += `<text class="val" data-anim="mean" style="fill:${p.ink ?? "var(--ground)"}" x="${cx + 7}" y="${y + 15}">${p.count}</text>`;
      cx += p.share * total;
    });
  });
  return `<svg class="viz" viewBox="0 0 ${W} ${H}" role="img" aria-label="Outcome shares; see data table">${out}</svg>`;
}

/** Vertical count histogram over integer categories (e.g. rounds 1..10). */
export function histogram(counts, cats, color, label, axis = "") {
  const L = 30, B = axis ? 36 : 22, H = 132, bw = (W - L) / cats.length, plot = H - B;
  const max = Math.max(1, ...cats.map((c) => counts[c] ?? 0));
  let out = `<line class="axis" x1="${L}" x2="${W}" y1="${plot}" y2="${plot}"/><line class="grid" x1="${L}" x2="${W}" y1="6" y2="6"/>`;
  out += `<text x="${L - 6}" y="10" text-anchor="end">${max}</text><text x="${L - 6}" y="${plot}" text-anchor="end">0</text>`;
  cats.forEach((c, i) => {
    const n = counts[c] ?? 0, bh = ((plot - 6) * n) / max;
    if (n) out += `<rect class="bar-v" data-anim="col" style="fill:${color};--i:${i}" x="${L + i * bw + 2}" y="${plot - bh}" width="${bw - 4}" height="${bh}" rx="2" data-tip="${esc(`${label}: ${n} ended at round ${c}`)}" tabindex="0"/>`;
    out += `<text x="${L + i * bw + bw / 2}" y="${plot + 15}" text-anchor="middle">${c}</text>`;
  });
  if (axis) out += `<text class="axt" x="${L + (W - L) / 2}" y="${H - 3}" text-anchor="middle">${esc(axis)}</text>`;
  return `<svg class="viz" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} histogram; see data table">${out}</svg>`;
}
