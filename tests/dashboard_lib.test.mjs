// Run: node --test tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import { cellIndex, dotStack, esc, filterRows, fmt, fullComplete, labStages, outcomeShares, pct, phase, refusalMessage, registerSlots, retryAfterSeconds, signed } from "../dashboard/static/lib.js";

const rows = [
  { key: "pilot:1", mode: "pilot", condition: "baseline_v1", family_A: "claude", family_B: "openai", model_A: "claude:x", model_B: "openai:y", first_mover: "A", outcome: "agreed", seed: 30000 },
  { key: "pilot:2", mode: "pilot", condition: "structured_v1", family_A: "openai", family_B: "claude", model_A: "openai:y", model_B: "claude:x", first_mover: "B", outcome: "timeout", seed: 30001 },
  { key: "full:3", mode: "full", condition: "structured_v1", family_A: "claude", family_B: "openai", model_A: "claude:x", model_B: "openai:y", first_mover: "A", outcome: "invalid_action", seed: 40002 },
];

test("filterRows: empty and 'all' match everything", () => {
  assert.equal(filterRows(rows, {}).length, 3);
  assert.equal(filterRows(rows, { condition: "all", outcome: "" }).length, 3);
});

test("filterRows: combines every filter", () => {
  assert.deepEqual(filterRows(rows, { condition: "structured_v1" }).map((r) => r.key), ["pilot:2", "full:3"]);
  assert.deepEqual(filterRows(rows, { mode: "pilot", modelA: "openai" }).map((r) => r.key), ["pilot:2"]);
  assert.deepEqual(filterRows(rows, { modelB: "openai", firstMover: "A", outcome: "agreed" }).map((r) => r.key), ["pilot:1"]);
  assert.deepEqual(filterRows(rows, { seed: "4000" }).map((r) => r.key), ["full:3"]);
  assert.deepEqual(filterRows(rows, { seed: " full:" }).map((r) => r.key), ["full:3"]);
});

test("formatters never invent a number", () => {
  assert.equal(fmt(null), "—");
  assert.equal(fmt(undefined), "—");
  assert.equal(fmt(NaN), "—");
  assert.equal(fmt(0), "0.000");
  assert.equal(pct(null), "—");
  assert.equal(pct(0.5), "50%");
  assert.equal(signed(0.25), "+0.250");
  assert.equal(signed(-0.25), "−0.250");
  assert.equal(signed(null), "—");
});

test("esc neutralises model-authored HTML", () => {
  assert.equal(esc(`<img src=x onerror="a">'`), "&lt;img src=x onerror=&quot;a&quot;&gt;&#39;");
  assert.equal(esc(null), "");
});

test("dotStack stacks identical values instead of hiding them", () => {
  const d = dotStack([0, 0, 0, 0.5, 0.505], 0.02);
  assert.deepEqual(d.map((x) => x.level), [0, 1, 2, 0, 1]);
  assert.equal(d[4].bin, 0.5);
});

test("outcomeShares keeps the fixed outcome order and zero-fills", () => {
  const s = outcomeShares({ timeout: 1, agreed: 3 }, 4);
  assert.deepEqual(s.map((x) => [x.outcome, x.count]), [["agreed", 3], ["walked_away", 0], ["timeout", 1], ["invalid_action", 0]]);
  assert.equal(s[0].share, 0.75);
  assert.equal(outcomeShares(undefined, 0)[0].share, 0);
});

test("phase is derived from recorded data only", () => {
  const snap = (pilot, full, runs = []) => ({ progress: { pilot: { done: pilot, target: 24 }, full: { done: full, target: 480, design_complete: full === 480 } }, runs });
  assert.equal(phase(snap(0, 0)).label, "No experimental data collected yet");
  assert.equal(phase(snap(5, 0, [{ mode: "pilot", status: "running" }])).label, "Pilot in progress");
  assert.equal(phase(snap(5, 0, [{ mode: "pilot", status: "budget_exhausted" }])).label, "Pilot incomplete");
  assert.equal(phase(snap(24, 0, [{ mode: "pilot", status: "completed" }])).label, "Pilot complete");
  assert.equal(phase(snap(24, 480)).label, "Full run complete");
});

test("cellIndex follows the preregistered cell order (condition x Claude seat x first mover)", () => {
  const c = (condition, claude_seat, first_mover) => cellIndex({ condition, claude_seat, first_mover });
  assert.deepEqual([c("baseline_v1", "A", "A"), c("baseline_v1", "A", "B"), c("baseline_v1", "B", "A"), c("baseline_v1", "B", "B")], [0, 1, 2, 3]);
  assert.equal(c("structured_v1", "A", "A"), 4);
  assert.equal(c("structured_v1", "B", "B"), 7);
  assert.equal(c("baseline_plain_prompting", "A", "A"), null);
  assert.equal(c("baseline_v1", null, "A"), null);
});

test("registerSlots places rows by instance and cell, ignoring foreign seeds", () => {
  const seeds = [30000, 30001, 30002];
  const slots = registerSlots([
    { seed: 30001, condition: "structured_v1", claude_seat: "B", first_mover: "A" },
    { seed: 99999, condition: "baseline_v1", claude_seat: "A", first_mover: "A" },
  ], seeds);
  assert.deepEqual([...slots], [8 + 6]);
});

test("labStages: empty lab is ready / locked / awaiting", () => {
  const snap = (p, f, runs = []) => ({ progress: { pilot: { done: p, target: 24 }, full: { done: f, target: 480, design_complete: f === 480 } }, runs });
  assert.deepEqual(labStages(snap(0, 0)), { pilot: "ready", full: "locked", analysis: "awaiting", empty: true });
  assert.equal(labStages(snap(3, 0, [{ mode: "pilot", status: "running" }])).pilot, "running");
  assert.equal(labStages(snap(3, 0)).pilot, "partial");
  assert.deepEqual(labStages(snap(24, 10)), { pilot: "complete", full: "partial", analysis: "sealed", empty: false });
  assert.equal(labStages(snap(24, 480)).full, "complete");
  assert.equal(labStages(snap(24, 480)).analysis, "pending");
});

test("retryAfterSeconds: whole positive seconds only", () => {
  assert.equal(retryAfterSeconds("23"), 23);
  assert.equal(retryAfterSeconds(" 7 "), 7);
  for (const bad of [null, undefined, "", "0", "-5", "1.5", "abc", "Wed, 21 Oct 2015 07:28:00 GMT", "999999"]) assert.equal(retryAfterSeconds(bad), null, bad);
});

test("refusalMessage: every refusal a start can get, in visitor words", () => {
  const limited = "Too many runs were started on this demo in the last minute. Try again shortly.";
  assert.equal(refusalMessage(429, { error: limited }, "23"), `${limited} You can start again in about 23 s.`);
  assert.equal(refusalMessage(429, {}, null), "Too many runs were started recently.");
  assert.equal(refusalMessage(409, { error: "Another evaluation is still running. Wait for it to finish." }), "Another evaluation is still running. Wait for it to finish.");
  assert.equal(refusalMessage(400, { error: "The public demo runs scripted test agents only." }), "The public demo runs scripted test agents only.");
  assert.equal(refusalMessage(403, {}), "The server refused the request (403).");
  assert.equal(refusalMessage(400, { error: 42 }), "The server refused the request (400).");  // only text is shown
});

test("refusalMessage: a 5xx body is never shown, whatever it holds", () => {
  for (const body of [{ error: "internal error" }, { error: "Traceback ... /home/app/dashboard/server.py" }, {}, null]) {
    const m = refusalMessage(500, body);
    assert.equal(m, "The server hit a problem and did not start the run. Please try again in a moment.");
    assert.ok(!/internal|Traceback|\/home\//.test(m));
  }
  assert.equal(refusalMessage(503, { error: "busy" }), refusalMessage(500, {}));
});

test("480 full-run rows are not complete unless the server's design check passed", () => {
  const snap = { progress: { pilot: { done: 24, target: 24 }, full: { done: 480, target: 480, design_complete: false } }, runs: [] };
  assert.deepEqual(labStages(snap), { pilot: "complete", full: "partial", analysis: "sealed", empty: false });
  assert.notEqual(phase(snap).key, "full-complete");
  assert.equal(fullComplete({ done: 481, target: 480 }), false);
});

// ------------------------------------------------------------------ workbench analytics helpers
import { boxStats, completedTrials, cumulativeOutcomes, designSize, flows, fmtCI, navOf, quantile, SECTION, simBody, sourceSummary, summarize, tally } from "../dashboard/static/lib.js";

test("designSize multiplies sweeps and personas into the design", () => {
  const d = { repetitions: 3, conditions: ["a", "b"], swap_seats: true, swap_first_mover: false };
  assert.equal(designSize(d), 12);
  assert.equal(designSize({ ...d, extra_envs: ["x", "y"], personas: [] }), 36);
  assert.equal(designSize({ ...d, extra_envs: [], personas: [{}, {}] }), 24);
});

test("simBody sends what the server validates: no half-typed prices, empty caps or UI state", () => {
  const body = simBody({ environment: "e", extra_envs: ["f"], consented: ["api_cost"], prices: { m: [1, 2], n: [1, null] }, caps: { max_tokens: 5, max_cost: null }, repetitions: 1 });
  assert.deepEqual(body.environments, ["e", "f"]);
  assert.deepEqual(body.prices, { m: [1, 2] });
  assert.deepEqual(body.caps, { max_tokens: 5 });
  assert.ok(!("consented" in body) && !("extra_envs" in body));
  assert.ok(!("environments" in simBody({ environment: "e", extra_envs: [] })));
});

test("fmtCI states n and never shows an interval it does not have", () => {
  assert.deepEqual(fmtCI({ mean: 0.5, ci: [0.25, 0.75], n: 12 }), { value: "50%", detail: "95% CI 25% to 75%, n = 12" });
  assert.deepEqual(fmtCI({ mean: 0.5, ci: null, n: 1 }), { value: "50%", detail: "n = 1, too few for an interval" });
  assert.equal(fmtCI({ mean: 3.25, ci: [3, 4], n: 2 }, "turns").value, "3.3");
  assert.equal(fmtCI({ mean: null, n: 0 }).detail, "not observed");
});

test("sourceSummary names what the data came from", () => {
  assert.equal(sourceSummary({ source_labels: { X: "Local model: m", Y: "Rule-based (simulated)" } }), "Local model: m vs Rule-based (simulated)");
  assert.equal(sourceSummary({ source_labels: { X: "Rule-based (simulated)", Y: "Rule-based (simulated)" } }), "Rule-based (simulated)");
  assert.equal(sourceSummary({ data_kind: "simulated" }), "Rule-based (simulated)");
  assert.equal(sourceSummary({ data_kind: "local-agents" }), "Local agents");
});

const T = [
  { index: 0, status: "completed", outcome: "agreed", rwe: 0.9, ew: 0.4, rounds: 3, condition: "baseline_v1" },
  { index: 2, status: "completed", outcome: "walked_away", rwe: 0, ew: 0, rounds: 10, condition: "structured_v1" },
  { index: 1, status: "completed", outcome: "agreed", rwe: 0.7, ew: 0.3, rounds: 5, condition: "baseline_v1" },
  { index: 3, status: "aborted", reason: "agent_failure" },
];

test("summarize: describes completed negotiations only and never invents a rate", () => {
  const s = summarize(T);
  assert.equal(s.n, 3); assert.equal(s.excluded, 1);
  assert.deepEqual(s.outcomes, { agreed: 2, walked_away: 1, timeout: 0, invalid_action: 0 });
  assert.equal(s.agreement_rate, 2 / 3); assert.equal(s.walk_rate, 1 / 3); assert.equal(s.invalid_rate, 0);
  assert.ok(Math.abs(s.rwe - 1.6 / 3) < 1e-12); assert.equal(s.rounds, 6);
  const empty = summarize([]);
  assert.equal(empty.n, 0); assert.equal(empty.agreement_rate, null); assert.equal(empty.rwe, null);
  assert.equal(completedTrials(undefined).length, 0);
});

test("quantile and boxStats match numpy's linear method and Tukey whiskers", () => {
  assert.equal(quantile([1, 2, 3, 4], 0.25), 1.75);
  assert.equal(quantile([], 0.5), null);
  const b = boxStats([1, 2, 3, 4, 100, null]);
  assert.equal(b.n, 5); assert.equal(b.median, 3); assert.equal(b.q1, 2); assert.equal(b.q3, 4);
  assert.deepEqual(b.outliers, [100]); assert.equal(b.hi, 4); assert.equal(b.lo, 1);
  assert.equal(boxStats([null]), null);
});

test("cumulativeOutcomes runs in negotiation order; flows count each transition once", () => {
  const c = cumulativeOutcomes(T);
  assert.deepEqual(c.map((r) => [r.k, r.agreed, r.walked_away]), [[1, 1, 0], [2, 2, 0], [3, 2, 1]]);
  const f = flows(T, [(t) => t.condition, (t) => t.outcome]);
  assert.deepEqual(f.sort((a, b) => b.n - a.n), [{ stage: 0, from: "baseline_v1", to: "agreed", n: 2 }, { stage: 0, from: "structured_v1", to: "walked_away", n: 1 }]);
  assert.deepEqual([...tally(["a", "b", "a"])], [["a", 2], ["b", 1]]);
});

test("navOf: every pre-redesign route still belongs to one of six sections", () => {
  const old = ["", "agents", "environments", "simulations", "simulation", "analytics", "reports", "report", "settings", "try", "live", "result", "compare", "lab",
    "study", "example", "experiments", "negotiation", "results", "structured", "methodology", "configuration"];
  for (const r of old) assert.ok(SECTION[r], `route ${r} has no section`);
  assert.equal(new Set(Object.values(SECTION).filter((s) => s !== "settings")).size, 6);
  assert.equal(navOf("simulations"), "mine");
  assert.equal(navOf("simulations", "new/review"), "new");
  assert.equal(navOf("experiments"), "study"); // research data collection, not the Workbench list
  assert.equal(navOf("nope"), "");
});

import { actionCounts, normalize100 } from "../dashboard/static/lib.js";

test("normalize100: whole numbers that always sum to exactly 100", () => {
  assert.deepEqual(normalize100([1, 1, 1]), [34, 33, 33]);
  assert.deepEqual(normalize100([50, 30, 20]), [50, 30, 20]);
  assert.deepEqual(normalize100([0, 0]), [50, 50]);
  assert.deepEqual(normalize100([3, NaN, -2, 1]), [75, 0, 0, 25]);
  for (const v of [[7, 13, 2, 9, 1, 5], [1, 2], [99, 1, 1]]) assert.equal(normalize100(v).reduce((a, b) => a + b, 0), 100);
});

test("actionCounts: an offer answered by a counter-offer or a walk-away counts as rejected", () => {
  const ev = (actor, action) => ({ actor, action });
  const c = actionCounts([
    { status: "completed", events: [ev("A", "OFFER"), ev("B", "OFFER"), ev("A", "ACCEPT")] },
    { status: "completed", events: [ev("A", "OFFER"), ev("B", "WALK_AWAY")] },
    { status: "completed", events: [ev("B", "INVALID")] },
    { status: "aborted", events: [ev("A", "OFFER")] },
  ]);
  assert.deepEqual(c, { offers: 3, accepted: 1, rejected: 2, walkaways: 1, invalid: 1 });
});
