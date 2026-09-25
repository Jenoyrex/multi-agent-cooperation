// Small SVG chart builders (strings). Every mark carries a data-tip for the
// hover layer and every chart ships a data table for keyboard/AT readers.
import { dotStack, esc, fmt, OUTCOME_LABEL } from "./lib.js";

const W = 480;

export function figure(title, note, svg, legend = "", table = "") {
  return `<figure class="chart"><figcaption><b>${esc(title)}</b><span>${esc(note)}</span></figcaption>
    ${legend}${svg}${table}</figure>`;
}

export const legend = (items) =>
  `<div class="legend">${items.map(([label, color]) => `<span><i class="sw" style="background:${color}"></i>${esc(label)}</span>`).join("")}</div>`;

export function dataTable(headers, rows) {
  return `<details class="data"><summary>Show data table</summary><div class="table-wrap"><table>
    <thead><tr>${headers.map((h, i) => `<th${i ? ' class="num"' : ""}>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ""}>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div></details>`;
}

/**
 * Horizontal dot plot, one row per group. groups: [{label, color, points:[{v, tip}], mean}]
 * Stacked dots keep ties visible; a vertical tick marks the group mean.
 */
export function dotPlot(groups, { domain = [0, 1], bin = 0.02, ticks = 5, zero = false, fmtTick = (x) => fmt(x, 1), width = W } = {}) {
  const W = width, L = 104, R = 12, r = 5, step = 2 * r + 1;
  const x = (v) => L + ((v - domain[0]) / (domain[1] - domain[0])) * (W - L - R);
  const laid = groups.map((g) => ({ ...g, dots: dotStack(g.points.map((p) => p.v), bin) }));
  const heights = laid.map((g) => Math.max(34, (Math.max(0, ...g.dots.map((d) => d.level)) + 1) * step + 18));
  const H = heights.reduce((a, b) => a + b, 0) + 26;
  let y0 = 0, i = 0, out = "";
  for (let t = 0; t <= ticks; t++) {
    const v = domain[0] + ((domain[1] - domain[0]) * t) / ticks;
    out += `<line class="grid" x1="${x(v)}" x2="${x(v)}" y1="0" y2="${H - 22}"/><text x="${x(v)}" y="${H - 6}" text-anchor="middle">${fmtTick(v)}</text>`;
  }
  if (zero) out += `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="0" y2="${H - 22}"/>`;
  laid.forEach((g, gi) => {
    const base = y0 + heights[gi] - 12;
    out += `<text class="lbl" x="0" y="${base - 2}">${esc(g.label)}</text>`;
    out += `<text x="0" y="${base + 11}">n = ${g.points.length}</text>`;
    out += `<line class="axis" x1="${L}" x2="${W - R}" y1="${base + r + 2}" y2="${base + r + 2}"/>`;
    g.dots.forEach((d, k) => {
      out += `<circle class="mark pop" style="fill:${g.color};--i:${i++}" cx="${x(d.bin)}" cy="${base - d.level * step}" r="${r}" data-tip="${esc(g.points[k].tip)}"/>`;
    });
    if (g.mean !== null && g.mean !== undefined) {
      const top = base - (Math.max(0, ...g.dots.map((d) => d.level)) + 1) * step;
      out += `<line class="mean" x1="${x(g.mean)}" x2="${x(g.mean)}" y1="${top}" y2="${base + r + 6}" data-tip="${esc(`${g.label} mean ${fmt(g.mean)}`)}"/>`;
    }
    y0 += heights[gi];
  });
  return `<svg class="viz" viewBox="0 -4 ${W} ${H + 4}" role="img" aria-label="Dot plot">${out}</svg>`;
}

/** 100% stacked horizontal bars. rows: [{label, n, parts:[{key, count, share, color}]}] */
export function stackBars(rows) {
  const L = 104, R = 12, h = 22, gap = 16;
  const H = rows.length * (h + gap);
  let out = "", i = 0;
  rows.forEach((row, ri) => {
    const y = ri * (h + gap);
    out += `<text class="lbl" x="0" y="${y + 15}">${esc(row.label)}</text>`;
    let cx = L;
    const total = W - L - R;
    row.parts.forEach((p) => {
      if (!p.count) return;
      const w = Math.max(0, p.share * total - 2);
      const tip = `${row.label} · ${OUTCOME_LABEL[p.key] ?? p.key}: ${p.count} of ${row.n} (${(p.share * 100).toFixed(0)}%)`;
      out += `<rect class="grow" style="fill:${p.color};--i:${i++}" x="${cx}" y="${y}" width="${w}" height="${h}" rx="3" data-tip="${esc(tip)}"/>`;
      if (w > 34) out += `<text class="val" style="fill:var(--page)" x="${cx + 7}" y="${y + 15}">${p.count}</text>`;
      cx += p.share * total;
    });
  });
  return `<svg class="viz" viewBox="0 0 ${W} ${H}" role="img" aria-label="Outcome distribution">${out}</svg>`;
}

/** Vertical count histogram over integer categories (e.g. rounds 1..10). */
export function histogram(counts, cats, color, label) {
  const L = 28, B = 20, H = 120, bw = (W - L) / cats.length;
  const max = Math.max(1, ...cats.map((c) => counts[c] ?? 0));
  let out = `<line class="axis" x1="${L}" x2="${W}" y1="${H - B}" y2="${H - B}"/>`;
  out += `<text x="0" y="10">${max}</text><text x="0" y="${H - B}">0</text>`;
  cats.forEach((c, i) => {
    const n = counts[c] ?? 0, bh = ((H - B - 6) * n) / max;
    if (n) out += `<rect class="growy" style="fill:${color};--i:${i}" x="${L + i * bw + 2}" y="${H - B - bh}" width="${bw - 4}" height="${bh}" rx="3" data-tip="${esc(`${label} · ${c} rounds: ${n}`)}"/>`;
    out += `<text x="${L + i * bw + bw / 2}" y="${H - 4}" text-anchor="middle">${c}</text>`;
  });
  return `<svg class="viz" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)} histogram">${out}</svg>`;
}
