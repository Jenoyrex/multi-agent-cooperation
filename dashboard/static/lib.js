// Pure helpers (no DOM) - unit-tested in tests/dashboard_lib.test.mjs.

export const OUTCOMES = ["agreed", "walked_away", "timeout", "invalid_action"];
export const OUTCOME_LABEL = { agreed: "Agreed", walked_away: "Walked away", timeout: "Timeout", invalid_action: "Invalid action" };
export const CONDITIONS = ["baseline_v1", "structured_v1"];
export const FAMILY_LABEL = { claude: "Claude", openai: "GPT" };

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
/** The full run is complete only when the server's design check says so (every preregistered cell once), never by row count. */
export const fullComplete = (full) => full.design_complete === true;

export function phase(snap) {
  const { pilot, full } = snap.progress;
  const running = snap.runs.some((r) => r.status === "running");
  if (fullComplete(full)) return { key: "full-complete", label: "Full run complete", tone: "good" };
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

/**
 * Design-cell index 0..7 in the preregistered order (condition x Claude seat x
 * first mover, as PILOT_CELLS enumerates them), or null for rows outside it.
 */
export function cellIndex(r) {
  const c = CONDITIONS.indexOf(r.condition);
  if (c < 0 || !["A", "B"].includes(r.claude_seat) || !["A", "B"].includes(r.first_mover)) return null;
  return c * 4 + (r.claude_seat === "A" ? 0 : 2) + (r.first_mover === "A" ? 0 : 1);
}

/**
 * Which register slots hold a recorded negotiation. Slot = instance * 8 + cell.
 * Records completion only, never outcome, so nothing is compared by condition.
 */
export function registerSlots(rows, seeds) {
  const filled = new Set();
  for (const r of rows) {
    const inst = seeds.indexOf(r.seed), cell = cellIndex(r);
    if (inst >= 0 && cell !== null) filled.add(inst * 8 + cell);
  }
  return filled;
}

/** Pilot -> full run -> analysis stages, from recorded data only. */
export function labStages(snap) {
  const { pilot, full } = snap.progress;
  const running = (m) => snap.runs.some((r) => r.mode === m && r.status === "running");
  const stage = (p, m) => (p.done >= p.target ? "complete" : running(m) ? "running" : p.done > 0 ? "partial" : "ready");
  const pilotState = stage(pilot, "pilot");
  const fullState = fullComplete(full) ? "complete" : full.done > 0 || running("full") ? (running("full") ? "running" : "partial") : "locked";
  return {
    pilot: pilotState,
    full: fullState,
    analysis: pilot.done + full.done === 0 ? "awaiting" : fullComplete(full) ? "pending" : "sealed",
    empty: pilot.done + full.done === 0,
  };
}

export function timeAgo(epochSeconds, now = Date.now() / 1000) {
  if (!epochSeconds) return "—";
  const s = Math.max(0, now - epochSeconds);
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

/** Seconds from a Retry-After header (delta-seconds form only), or null. */
export function retryAfterSeconds(value) {
  const s = /^\s*\d{1,5}\s*$/.test(value ?? "") ? Number.parseInt(value, 10) : NaN;
  return s > 0 ? s : null;
}

/**
 * What a visitor reads when the server refuses to start a run. The server's own message for
 * 4xx answers (it is written for visitors); never the body of a 5xx, which could be technical.
 */
export function refusalMessage(status, body, retryAfter = null) {
  if (status >= 500) return "The server hit a problem and did not start the run. Please try again in a moment.";
  const said = typeof body?.error === "string" && body.error ? body.error : null;
  if (status === 429) {
    const s = retryAfterSeconds(retryAfter);
    return `${said ?? "Too many runs were started recently."}${s ? ` You can start again in about ${s} s.` : ""}`;
  }
  return said ?? `The server refused the request (${status}).`;
}

// ------------------------------------------------------------------ workbench analytics (pure, from stored trials only)
export const completedTrials = (trials) => (trials ?? []).filter((t) => t.status === "completed");

/** Descriptive summary of negotiation records. Nothing is imputed: rates and means are null when n = 0. */
export function summarize(trials) {
  const done = completedTrials(trials), n = done.length;
  const outcomes = Object.fromEntries(OUTCOMES.map((o) => [o, done.filter((t) => t.outcome === o).length]));
  const rate = (o) => (n ? outcomes[o] / n : null);
  return { n, excluded: (trials ?? []).length - n, outcomes, agreement_rate: rate("agreed"), walk_rate: rate("walked_away"),
    timeout_rate: rate("timeout"), invalid_rate: rate("invalid_action"),
    rwe: mean(done.map((t) => t.rwe)), ew: mean(done.map((t) => t.ew)), rounds: mean(done.map((t) => t.rounds)) };
}

/** Type-7 (linear) quantile of a sorted array, as numpy's default. */
export function quantile(sorted, p) {
  if (!sorted.length) return null;
  const h = (sorted.length - 1) * p, lo = Math.floor(h);
  return sorted[lo] + (h - lo) * ((sorted[lo + 1] ?? sorted[lo]) - sorted[lo]);
}

/** Box-plot statistics with Tukey whiskers (1.5 IQR); null for no values. */
export function boxStats(values) {
  const v = values.filter((x) => x !== null && x !== undefined && !Number.isNaN(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const q1 = quantile(v, 0.25), median = quantile(v, 0.5), q3 = quantile(v, 0.75), iqr = q3 - q1;
  const inside = v.filter((x) => x >= q1 - 1.5 * iqr && x <= q3 + 1.5 * iqr);
  return { n: v.length, min: v[0], max: v.at(-1), q1, median, q3, mean: mean(v), lo: inside[0], hi: inside.at(-1),
    outliers: v.filter((x) => x < inside[0] || x > inside.at(-1)) };
}

/** Running outcome counts after each completed negotiation, in run order. */
export function cumulativeOutcomes(trials) {
  const run = Object.fromEntries(OUTCOMES.map((o) => [o, 0]));
  return completedTrials(trials).sort((a, b) => a.index - b.index).map((t, i) => { run[t.outcome] += 1; return { k: i + 1, ...run }; });
}

/** Sankey links between consecutive stages: stages are functions trial -> node id. Counts only completed trials. */
export function flows(trials, stages) {
  const links = new Map();
  for (const t of completedTrials(trials)) {
    for (let i = 0; i + 1 < stages.length; i++) {
      const key = `${i}\u0000${stages[i](t)}\u0000${stages[i + 1](t)}`;
      links.set(key, (links.get(key) ?? 0) + 1);
    }
  }
  return [...links].map(([key, n]) => { const [stage, from, to] = key.split("\u0000"); return { stage: +stage, from, to, n }; });
}

/** Count of each value, in first-seen order. */
export const tally = (values) => values.reduce((m, v) => m.set(v, (m.get(v) ?? 0) + 1), new Map());

/** The navigation link each route belongs to. Routes not listed have a link of their own name. */
export const SECTION = { "": "start", simulation: "simulations", live: "try", result: "try", report: "reports", example: "study",
  negotiation: "study", structured: "study", experiments: "study", results: "study", methodology: "study", configuration: "study", "run-locally": "help" };
/** Navigation link of a route. */
export const navOf = (name) => SECTION[name] ?? name;

/** Negotiations in a wizard draft: every combination of the design. */
export const designSize = (d) => d.repetitions * d.conditions.length * (d.swap_seats ? 2 : 1) * (d.swap_first_mover ? 2 : 1)
  * (1 + (d.extra_envs?.length ?? 0)) * Math.max(1, d.personas?.length ?? 0);

/** A wizard draft as the request the server validates. Half-typed prices and empty caps are left out. */
export function simBody(d) {
  const { extra_envs: extra = [], consented: _c, prices = {}, caps = {}, ...rest } = d;
  return { ...rest, ...(extra.length ? { environments: [d.environment, ...extra] } : {}),
    prices: Object.fromEntries(Object.entries(prices).filter(([, p]) => p?.every((v) => v != null))),
    caps: Object.fromEntries(Object.entries(caps).filter(([, v]) => v != null)) };
}

/** A summary from the server as words: the value, then its interval and n. There is no interval to show for fewer than two repetitions. */
export function fmtCI(s, unit = "share") {
  const f = (v) => (unit === "turns" ? v.toFixed(1) : `${(v * 100).toFixed(0)}%`);
  if (s?.mean == null) return { value: "—", detail: "not observed" };
  return { value: f(s.mean), detail: s.ci ? `95% CI ${f(s.ci[0])} to ${f(s.ci[1])}, n = ${s.n}` : `n = ${s.n}, too few for an interval` };
}

/** What an experiment's data came from, in words. Experiments saved before the sandbox carry only a data kind. */
export function sourceSummary(x) {
  const labels = x.source_labels ?? x.config?.source_labels;
  if (labels) return [...new Set(Object.values(labels))].join(" vs ");
  const kind = x.data_kind ?? x.config?.data_kind;
  return kind === "simulated" ? "Rule-based (simulated)" : kind === "local-llm" ? "Local LLM" : "Local agents";
}

/** Which onboarding steps are done, from real records only (built-ins do not count as the user's own). */
/** Scale non-negative numbers to whole numbers summing to exactly 100 (largest remainder; equal split when all are 0). */
export function normalize100(values) {
  const v = values.map((x) => (Number.isFinite(x) && x > 0 ? x : 0)), total = v.reduce((a, b) => a + b, 0);
  const raw = total ? v.map((x) => (x * 100) / total) : v.map(() => 100 / v.length);
  const out = raw.map(Math.floor);
  const order = raw.map((x, i) => [x - Math.floor(x), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  const deficit = 100 - out.reduce((a, b) => a + b, 0);
  for (let k = 0; k < deficit; k++) out[order[k % order.length][1]] += 1;
  return out;
}

/** Moves recorded in negotiation transcripts. An offer is "rejected" when the other agent answers it with a
 *  counter-offer or by walking away; an offer still standing when a negotiation ends is neither. */
export function actionCounts(trials) {
  const c = { offers: 0, accepted: 0, rejected: 0, walkaways: 0, invalid: 0 };
  for (const t of completedTrials(trials)) {
    (t.events ?? []).forEach((e, i, all) => {
      const next = all[i + 1];
      if (e.action === "OFFER") { c.offers += 1; if (next && next.actor !== e.actor && ["OFFER", "WALK_AWAY"].includes(next.action)) c.rejected += 1; }
      else if (e.action === "ACCEPT") c.accepted += 1;
      else if (e.action === "WALK_AWAY") c.walkaways += 1;
      else if (e.action === "INVALID") c.invalid += 1;
    });
  }
  return c;
}
