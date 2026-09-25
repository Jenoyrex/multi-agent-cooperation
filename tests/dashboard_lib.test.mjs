// Run: node --test tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import { dotStack, esc, filterRows, fmt, outcomeShares, pct, phase, signed } from "../dashboard/static/lib.js";

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
