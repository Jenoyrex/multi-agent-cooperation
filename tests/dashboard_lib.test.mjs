// Run: node --test tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import { cellIndex, dotStack, esc, filterRows, fmt, labStages, outcomeShares, pct, phase, refusalMessage, registerSlots, retryAfterSeconds, signed } from "../dashboard/static/lib.js";

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
  const snap = (pilot, full, runs = []) => ({ progress: { pilot: { done: pilot, target: 24 }, full: { done: full, target: 480 } }, runs });
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
  const snap = (p, f, runs = []) => ({ progress: { pilot: { done: p, target: 24 }, full: { done: f, target: 480 } }, runs });
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
