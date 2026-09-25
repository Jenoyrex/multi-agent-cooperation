// Pure helpers (no DOM) - unit-tested in tests/dashboard_lib.test.mjs.

export const OUTCOMES = ["agreed", "walked_away", "timeout", "invalid_action"];
export const OUTCOME_LABEL = { agreed: "Agreed", walked_away: "Walked away", timeout: "Timeout", invalid_action: "Invalid action" };
export const CONDITIONS = ["baseline_v1", "structured_v1"];
export const FAMILY_LABEL = { claude: "Claude", openai: "OpenAI" };

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

/** Number or em dash; never prints NaN or a fabricated 0. */
export function fmt(x, digits = 3) {
  return x === null || x === undefined || Number.isNaN(x) ? "—" : Number(x).toFixed(digits);
}
export const pct = (x, digits = 0) => (x === null || x === undefined ? "—" : `${(x * 100).toFixed(digits)}%`);
export const signed = (x, digits = 3) => (x === null || x === undefined ? "—" : `${x > 0 ? "+" : x < 0 ? "−" : "±"}${Math.abs(x).toFixed(digits)}`);
export const short = (s, n = 8) => (s ? String(s).replace(/^sha256:/, "").slice(0, n) : "—");

export function familyOf(model) {
  const f = String(model ?? "").split(":")[0];
  return FAMILY_LABEL[f] ? f : null;
}
export const modelLabel = (model) => FAMILY_LABEL[familyOf(model)] ?? model;

/** Filter negotiation summaries. Empty/"all" filter values match anything. */
export function filterRows(rows, f = {}) {
  const any = (v) => v === undefined || v === "" || v === "all";
  const q = String(f.seed ?? "").trim();
  return rows.filter((r) =>
    (any(f.mode) || r.mode === f.mode) &&
    (any(f.condition) || r.condition === f.condition) &&
    (any(f.modelA) || r.family_A === f.modelA || r.model_A === f.modelA) &&
    (any(f.modelB) || r.family_B === f.modelB || r.model_B === f.modelB) &&
    (any(f.firstMover) || r.first_mover === f.firstMover) &&
    (any(f.outcome) || r.outcome === f.outcome) &&
    (q === "" || String(r.seed).includes(q) || String(r.key).includes(q)));
}

/** Unique values of a field, for building filter options from real data only. */
export const uniq = (rows, key) => [...new Set(rows.map((r) => r[key]).filter((v) => v !== null && v !== undefined))].sort();

/** Experiment phase from recorded data only. */
export function phase(snap) {
  const { pilot, full } = snap.progress;
  const running = snap.runs.some((r) => r.status === "running");
  if (full.done >= full.target) return { key: "full-complete", label: "Full run complete", tone: "good" };
  if (full.done > 0 || snap.runs.some((r) => r.mode === "full")) return { key: "full", label: running ? "Full run in progress" : "Full run incomplete", tone: running ? "good" : "warn" };
  if (pilot.done >= pilot.target) return { key: "pilot-complete", label: "Pilot complete", tone: "good" };
  if (pilot.done > 0 || snap.runs.some((r) => r.mode === "pilot")) return { key: "pilot", label: running ? "Pilot in progress" : "Pilot incomplete", tone: running ? "good" : "warn" };
  return { key: "none", label: "No experimental data collected yet", tone: "muted" };
}

/**
 * Dot-plot layout: values are snapped to bins of width `bin` and stacked,
 * so identical values (e.g. the many 0s of unconditional RWE) stay visible.
 * Returns [{value, bin, level}] in input order.
 */
export function dotStack(values, bin) {
  const seen = new Map();
  return values.map((value) => {
    const b = Math.round(value / bin);
    const level = seen.get(b) ?? 0;
    seen.set(b, level + 1);
    return { value, bin: b * bin, level };
  });
}

export const mean = (xs) => {
  const v = xs.filter((x) => x !== null && x !== undefined);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

/** Outcome counts -> ordered [{outcome, count, share}] over n. */
export function outcomeShares(counts, n) {
  return OUTCOMES.map((o) => ({ outcome: o, count: counts?.[o] ?? 0, share: n ? (counts?.[o] ?? 0) / n : 0 }));
}

export function timeAgo(epochSeconds, now = Date.now() / 1000) {
  if (!epochSeconds) return "—";
  const s = Math.max(0, now - epochSeconds);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
