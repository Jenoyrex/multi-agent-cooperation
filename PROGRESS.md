# PROGRESS — pilot readiness (Stages 1–5)

Mission: cleanup → readiness audit → zero-cost dry run → user go/no-go → approved
24-negotiation pilot. The 480-negotiation full run is out of scope.
Roles are tracked per stage: the main session acts as the cleanup worker, auditor,
and preparation worker; an independent read-only subagent reviews the cleanup diff.

**Real API calls made so far: NONE. Pilot spend: $0.**

Environment: Windows 11, Git Bash. `python` is the Microsoft Store stub; `py` = Python 3.12.4.
Node v25.2.1. Installed SDKs: anthropic 1.8.0, openai 3.19.2 (match the pins in pyproject.toml).

---

## Stage 1 — Cleanup and Git checkpoint (Graph A)

### Loop 1 — gather (2026-10-28)
- `git status --short --branch`: `main...origin/main [ahead 1]`, staged `D dashboard/static/blackhole.js`, untracked `.impeccable/`.
- `results/` empty (mock `smoke_test.db` already removed); `experiments/pilots/` empty; `.env` absent; `.env.example` tracked.
- `blackhole.js`: no reference in any HTML/JS/CSS/Python/test/build file; `index.html` loads only `app.js`;
  no dynamic `import()`. The only match is `.impeccable/hook.cache.json` (tool edit log, not a code reference).
- `.impeccable/`: `config.json` (Impeccable design-linter ignore rule) and `hook.cache.json` (per-session
  edit cache with absolute local paths `C:\Users\...`). Local tool state, not research material, so it is ignored, not deleted.
- Writers of outputs: the pilot writes only `results/pilot.db` (SQLite, default rollback journal). No code writes
  JSONL or anything under `experiments/pilots/`.

### Loop 1 — act
- `.gitignore`: added `results/*.db-journal`, `results/*.jsonl`, `experiments/pilots/`, `.impeccable/`.
  `git ls-files -ci --exclude-standard` is empty, so no tracked file becomes ignored. Spec, source, tests and
  `.env.example` are confirmed not ignored.
- Kept the staged deletion of `dashboard/static/blackhole.js`.

### Loop 1 — verify
See the stage gate below.

---

## Stage 2 — Experiment-readiness audit (Graph B)

Files inspected: docs/spec.md (§3, §5, §8.3–8.6, §8.10–8.11, §8.15), src/experiments/{config,pilot,runner}.py,
src/negotiation/{protocol,usage}.py, src/agents/{generation,claude_agent,openai_agent}.py,
src/environment/valuations.py, src/evaluation/metrics.py, src/storage/db.py, scripts/run_pilot.py,
tests/test_pilot.py, pyproject.toml, .env.example.

### Requirements-to-evidence table

| # | Frozen requirement | Evidence | Status | Residual risk |
|---|---|---|---|---|
| R1 | Outcomes agreed / walked_away / timeout / invalid_action | `protocol.py:20` Literal; `_finalize` paths | OK | DB has no CHECK constraint on `outcome` (minor, see F5) |
| R2 | Valid final-turn OFFER → timeout | `protocol.py:214-221` (validated first, then `is_final_turn` → timeout) | OK | — |
| R3 | Malformed/invalid final-turn OFFER → invalid_action/invalid_allocation | `protocol.py:205-213` run before the final-turn check | OK | — |
| R4 | ACCEPT needs an opponent's standing offer, else illegal_accept | `protocol.py:186-191` (`None` or own offer) | OK | — |
| R5 | WALK_AWAY → walked_away on any turn | `protocol.py:198-203` | OK | — |
| R6 | Valid non-final OFFER becomes standing | `protocol.py:222-223` | OK | — |
| R7 | invalid_reason ∈ {malformed_output, invalid_allocation, illegal_accept}; no illegal_action | `protocol.py:22-26`; `git grep illegal_action` → none in src | OK | — |
| R8 | No strategic retries; transport failures handled as infrastructure | `generation.py:105-133` retries only 408/409/429/5xx/connection; `runner.py:187-198` reruns only `transport_failure` | OK | — |
| R9 | RWE and EW score failures as 0; allocation metrics agreed-only | `metrics.py:16-60` (`has_valid_allocation`, RWE `0.0 if swe is None`, EW 0.0) | OK | — |
| R10 | Pilot seeds 30000–30002; 24 negotiations; 8 cells (condition × Claude seat × first mover) | `pilot.py:31-56`; `tests/test_pilot.py::test_pilot_matrix_is_exactly_the_preregistered_24` | OK | — |
| R11 | First mover independent of generation | `runner.py:31-38` (policy + index only); pilot uses a fixed A/B per cell per spec §8.4 | OK | — |
| R12 | SHA-256 seed derivation, never `hash()` | `valuations.py:15-26` | OK | — |
| R13 | Approved runtime (models, temp 1.0, 1024 out, Claude thinking disabled + effort medium, OpenAI no effort, no seed, timeout 120, retries 3, backoff 2.0, max_rounds 10, reruns 2) | `config.py:178-223` `check_approved_runtime`; called in `prepare_pilot` (`pilot.py:118`) and `run_batch` (`runner.py:163-164`) before any call; `.env.example` matches | OK | Values come from `.env` at run time; any mismatch raises before calls |
| R14 | Prices $3/$15 (Claude), $2/$8 (OpenAI) | `config.py:191-194`; `test_approved_prices_are_the_user_supplied_ones` | OK | Prices were supplied by the user; current provider pricing not re-verified (no network) |
| R15 | SDK pins anthropic 1.8.0 / openai 3.19.2 | `pyproject.toml`; installed versions match | OK | — |
| R16 | max_rounds < 2 rejected | `config.py:258-262`; `protocol.py:118-119` | OK | — |
| R17 | One shared $20 cap across all 8 batches, retries and reruns | One `Budget` from `prepare_pilot` passed to every `run_batch` (`pilot.py:134-139`, `runner.py:173`); `test_token_cap_stops_the_pilot...` shows batch 2 stopped by batch 1's spend | OK within one invocation | **F1** (cross-invocation) |
| R18 | Reserve covers worst-case billed usage incl. retries | `Budget.next_call_reserve`: attempts (1+max_retries=4) × (20000 in + 1024 out) at the priciest model, checked before every turn (`protocol.py:169-171`); failed/discarded attempts charged worst case (`usage.py:327-334`); known usage charged once | OK | F2 |
| R19 | 20,000-token per-call input limit | Reserve uses the ceiling; a call that *reports* > 20,000 input tokens stops all further calls (`usage.py:323-326`) | OK (post-hoc) | F2 |
| R20 | Dry run is provider-free | `run_pilot.py:55-57` returns before `run_pilot`; `prepare_pilot` builds agents with `client=None`; SDK clients are created lazily only in `generate_response`; no DB is created | OK | Verified empirically in Stage 3 with network blocked |
| R21 | Pilot data cannot be mistaken for full-run data | `mode='pilot'` on every run, separate DB, disjoint seeds; `test_pilot_records_are_distinguishable...` | OK | — |
| R22 | No overwrite of prior pilot data | `prepare_pilot` refuses an existing DB (`pilot.py:106-107`) | OK | — |
| R23 | Provenance stored per run | `runner.py:75-99` (protocol id, spec sha256, prompt hash, code version, deps, full config, prices); raw outputs in `calls_json` | OK | `code_version` is the git commit, so the pilot should run from a clean, committed tree |

### Findings
- **F1 — MAJOR (procedural), budget across invocations.** The $20 cap is enforced in memory for one
  `run_pilot.py --confirm` invocation. There is no persistent ledger. A second invocation must use a new DB
  (an existing DB is refused) and would start with a fresh $20. There is also no resume: a stopped pilot cannot
  continue its remaining cells in the same DB. *Impact:* total pilot spend across more than one invocation is
  not bounded by code. *Smallest safe options:* (a) operating rule: exactly one `--confirm` invocation; any
  further invocation needs explicit user approval with `--max-cost-usd` set to $20 minus the spend recorded in
  the first DB; (b) a code change adding a persistent spend ledger and resume. That is an engineering change,
  not a protocol change, but it needs review and tests. Decision requested at the Stage 4 gate.
- **F2 — MINOR, input ceiling is checked after the call.** No pre-call token count is made. Bound by
  construction: input = system prompt + transcript of ≤ 9 prior turns × ≤ 1024 output tokens, so roughly ≤ 11–13k
  tokens < 20,000. A breach would be detected and would stop further calls; the overshoot is at most one call.
- **F3 — INFO, worst case vs. cap.** The plan's worst case (every negotiation 10 rounds, every call 4 attempts at
  20k input + 1024 output) is about $59, above $20. The cap is therefore binding in the worst case: the pilot
  would stop with `budget_exhausted` instead of overspending. The stopping point is when spent + $0.30144
  (Claude per-call reserve) would exceed $20.
- **F4 — INFO, interrupt.** Ctrl-C or a crash during a provider call loses that call's usage from accounting
  (run marked `failed`). Spend is still bounded by the next-call reserve plus at most one call.
- **F5 — MINOR, DB schema.** `outcome` and `invalid_reason` have no SQL CHECK constraint; validity is enforced
  in code (Literal types and protocol paths). Pilot review must check these values explicitly.
- **F6 — BLOCKER for Stage 5 only: `.env` absent.** No API keys and no generation settings are present, so a
  paid pilot cannot start. This does not affect Stages 1–3.

---

## Stage 1 gate — PASS

| Command | Result |
|---|---|
| `PYTHONPATH=. py -m pytest -q -p no:cacheprovider` | exit 0, **458 passed** (112 s) |
| `node --test tests/dashboard_lib.test.mjs` | exit 0, **13 passed** |
| `PYTHONPATH=. py scripts/run_smoke_test.py` | exit 0; 5 mock negotiations. Regenerated `results/smoke_test.db` was checked as mock-only (models `mock-greedy-A/B`, 0 API calls) and removed again |
| Independent reviewer (read-only subagent) | **APPROVE**. Confirmed: no reference to blackhole.js or its classes; no tracked file newly ignored; no frozen file touched; nothing secret or generated added |

Commits (local only, **not pushed**; `1de0b27` not amended):
- `2a4a064` Remove unused black-hole hero animation from dashboard
- `f5e3bce` Ignore local tool state and generated run artifacts

`main...origin/main [ahead 3]`.

## Stage 3 — Dry run and preflight (Graph C)

### Provider-free proof
1. Code path: `run_pilot.py:55-57` returns before `run_pilot()`; `prepare_pilot` builds agents with `client=None`;
   SDK clients are created only inside `generate_response` (`claude_agent.py:60-66`, `openai_agent.py:61-67`);
   the DB directory and file are created only after `--confirm`.
2. Empirical check: the dry run ran through a scratchpad wrapper (not in the repo) that blocks every non-loopback socket
   connection and replaces `anthropic.(Async)Anthropic` and `openai.(Async)OpenAI` with tripwires. No API keys were in
   the environment (`env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY`). No tripwire fired.
3. Because `.env` is absent, the generation settings were supplied from the **non-secret lines of the tracked
   `.env.example`** (the approved values). The real `.env` must be re-validated by repeating the dry run before `--confirm`.

### Dry-run command and output (exit 0)
```
env -u ANTHROPIC_API_KEY -u OPENAI_API_KEY <CLAUDE_*/OPENAI_* from .env.example> PYTHONPATH=. \
  py <scratchpad>/dryrun_guarded.py . --max-input-tokens-per-call 20000 --max-cost-usd 20
```
- 24 lines: cells 1–8 × seeds 30000, 30001, 30002.
  Cells 1–4 are baseline_v1 and 5–8 are structured_v1. Within each condition: (Claude=A, fm=A), (Claude=A, fm=B), (Claude=B, fm=A), (Claude=B, fm=B).
- `budget: max_total_tokens=None max_cost_usd=20.0 max_input_tokens_per_call=20000`
- `worst case without transport reruns: calls_per_model 120, worst_case_tokens 20,183,040, worst_case_cost_usd 59.30496`
- `database: ...\results\pilot.db` (does not exist; `results/` remained empty after the dry run)
- `check_condition_label` and `check_approved_runtime` passed for all 8 cells inside `prepare_pilot`. Any model,
  temperature, token, thinking, effort, timeout, retry, backoff, max_rounds or rerun mismatch would have raised.

### Budget arithmetic (from code, not observed data)
- Per-call reserve = max over models of 4 attempts × (20,000 in + 1,024 out):
  Claude 4 × ($0.060 + $0.01536) = **$0.30144**; GPT 4 × ($0.040 + $0.008192) = $0.192768.
- A turn starts only if spent + $0.30144 ≤ $20, so recorded spend cannot exceed $20 within the invocation.
- Labelled assumption, not a measurement: with about 1,500 input tokens per call, full 1,024-token outputs, every
  negotiation at 10 rounds and no retries, cost ≈ 120 × $0.01986 + 120 × $0.01119 ≈ **$3.73**. Real usage is unknown until the pilot runs.

### Resume and restart behaviour
- Every negotiation is written to SQLite as it completes; aborted attempts go to `aborted_negotiations`; each run row ends
  `completed | incomplete | budget_exhausted | failed`.
- The pilot stops at the first batch that does not finish all 3 negotiations. There is no in-place resume (finding F1).

### Remaining blockers and risks
- **F6 (blocker for Stage 5): `.env` absent.** The user must create it from `.env.example` with their keys. The values must equal the approved ones.
- **F1 (major, procedural): no cross-invocation budget ledger or resume.** Recommended: the single-invocation operating
  rule (option a). Accept it, or ask for option (b), at the Stage 4 gate.
- F2–F5: minor or informational (see Stage 2).

### Stage 3 gate — PASS, subject to the F1 decision at Stage 4. No paid call made.

## Stage 4 — Go/no-go gate: AWAITING USER

Spend boundary requested: one invocation, ≤ $20 recorded spend (hard in-process cap), 24 negotiations, seeds 30000–30002.

Exact steps once authorised:
1. User creates `.env` from `.env.example` and fills in both keys (never committed; `.gitignore:7`).
2. Re-run the dry run with the real `.env` (no `--confirm`): `PYTHONPATH=. py scripts/run_pilot.py --max-input-tokens-per-call 20000 --max-cost-usd 20`
3. Clean tree check (`git status`), so the run's `code_version` is a committed state.
4. `PYTHONPATH=. py scripts/run_pilot.py --max-input-tokens-per-call 20000 --max-cost-usd 20 --confirm`

## Stage 5 — NOT STARTED (no authorisation, no credentials)

---

## Remediation of F1 / F2 / F5 (user request, 2026-10-28)

Graph order: F5 (storage), then F1 (pilot driver), with F2 investigated alongside F1. Then an independent review, then commits.
No real API call was made. No frozen research decision was changed: outcomes, invalid reasons, protocol engine, prompts,
metrics, seeds, cells, approved runtime, `max_transport_reruns=2` and the `Budget` accounting rules in `usage.py` are untouched.

### Graph F5 — storage constraints. Loop 1: PASS
- Change: `src/storage/db.py` adds CHECK constraints: `outcome` ∈ the 4 frozen outcomes; `invalid_reason` ∈ the 3 frozen
  reasons; a reason present iff `invalid_action`; an allocation present iff `agreed`; utilities 0 on failures;
  `first_mover` ∈ {A, B}; `runs.status` and the abort `reason` are enumerated. `SCHEMA_VERSION` 2 → 3; no database with data exists.
- Tests: `tests/test_schema_constraints.py` (15). Mutation check: with the constraints reverted, 9 tests fail.
- Full suite: **473 passed**. Commit **`16977be`**.

### Graph F1 — persistent shared budget, resume, single invocation. Loop 1 (fix: validate before taking the lock), loop 2: PASS
Design (src/experiments/pilot.py, src/storage/db.py, scripts/run_pilot.py):
- **The budget lives in the pilot DB.** `budget_caps` holds the caps fixed at creation. `budget_ledger` holds every
  `Budget.add` call, written immediately (`PersistentBudget`). A resume replays the ledger through the unchanged `Budget.add`,
  so spend and any stop reason return exactly as they were.
- **Crash safety.** Each run left `running` or `failed` is charged one worst-case call (4 × (20,000 + 1,024) tokens at the priciest model),
  which covers a call in flight or not yet written to the ledger when the process died.
- **No reset.** `--resume` must use the same caps (otherwise refused). A fresh start is refused if the DB exists or if another pilot DB
  exists in the same directory. Only one invocation at a time: exclusive SQLite lock on `<db>.lock`, released by the OS if the process dies.
- **No duplicates.** A resume runs only (cell, seed) pairs without a record, as contiguous batches per cell; a fresh DB still
  gives exactly the 8 preregistered batches of 3. A resume refuses DBs containing duplicate pairs, non-pilot runs, or runs whose
  protocol id, spec hash, prompt hash, generation settings or prices differ from the current ones.
- **Transport policy kept.** A pair with transport-failure aborts and no record is `blocked`. It is never re-run
  automatically, so it never gets more than the approved 2 reruns. It needs an explicit decision.
- **Dry run unchanged in kind.** `prepare_pilot` only reads (read-only SQLite URI). The CLI dry run with `--resume` prints the
  remaining plan and the spend already charged.
- Tests: `tests/test_pilot_resume.py` (9): restart cannot reset or raise the cap; crash then resume (lost call charged, only missing
  pairs run, 24 unique pairs, resume dry run writes nothing); in-turn retries charged once, persisted and replayed; exhausted
  transport reruns block the pair and the resume skips it; concurrent invocation refused; duplicate/foreign/provenance-changed
  records refuse a resume; an input-ceiling breach stops the pilot and survives a restart. All 37 existing `test_pilot.py` tests pass unchanged.
- Mutation check (each safeguard disabled in turn): no ledger replay → 4 fail; no crash charge → 1 fail; no duplicate check → 1 fail;
  no blocking → 1 fail; no lock → 1 fail. File restored afterwards.
- `.gitignore`: `results/*.lock`.

### Graph F2 — input-token ceiling timing. Investigated: no engine change justified
- Finding confirmed: the 20,000 ceiling is checked after each call, from the provider-reported usage (`usage.py:323-326`).
- A pre-call check needs a token count. There is no local tokenizer (`tiktoken` not installed), Anthropic counting is a network
  call, and a character-based bound would refuse legitimate prompts (10 turns × 1,024 tokens of messages is far more than 20,000 characters).
- Structural bound: system prompt ≈ 2.4k characters; transcript ≤ 9 prior turns × ≤ 1,024 output tokens, so about 10–12k input tokens.
  That is under 20,000 unless the other provider's tokenizer expands the text by more than about 1.6×.
- Guarantee kept and now tested across restarts: a breach stops all further calls, and the stop is replayed on resume
  (`test_an_input_ceiling_breach_stops_the_pilot_and_survives_a_restart`). Residual risk: one call can exceed its reserve; that excess is
  charged at the actual reported usage.

### Verification (F1/F2)
| Command | Result |
|---|---|
| `PYTHONPATH=. py -m pytest -q -p no:cacheprovider` | exit 0, **482 passed** (119 s) |
| Guarded CLI dry run (no `--confirm`, network blocked, no keys) | exit 0; same 24-pair plan; `already charged: 0 tokens, $0.0000`; worst case $59.30496; nothing written to `results/` |
| Guarded CLI dry run with `--resume`, no pilot DB | refused: `... is not an existing pilot database; there is no pilot to resume.` |

### Independent review (read-only subagent): APPROVE, nothing blocking
It verified: no frozen file changed; a fresh DB gives the same 8 configs (base 30000, n=3) in cell order as HEAD; no spend
under-count path (at most one call in flight, covered by the crash charge, which is never persisted so never double-counted); no
duplicate or over-rerun path; the dry run writes nothing (read-only URI, no lock or mkdir without `--confirm`); the tests pin exact totals.
Four non-blocking suggestions, handled in loop 3:
1. The "blocked" message overstated the cause (a crash during a rerun also blocks a pair). Message corrected: "a transport
   failure is recorded and no outcome; never re-run automatically, needs an explicit decision".
2. The fresh-budget guard only sees `*.db` in the same directory. **Kept as a documented procedural risk:** a pilot started
   under another name or directory gets a fresh $20 cap. Use only the default `results/pilot.db`.
3. Ledger not cross-checked on replay. Now each ledger row stores its own charge and the replay must reproduce it
   (a running total would include the unpersisted crash charge and misfire). A tampered row refuses the resume (new test case).
4. `run_pilot` built extra agents just to size the budget. It now reuses the budget from `prepare_pilot`.

### Final verification (loop 3) and commits
| Command | Result |
|---|---|
| `PYTHONPATH=. py -m pytest -q -p no:cacheprovider` | exit 0, **483 passed** (129 s) |
| `node --test tests/dashboard_lib.test.mjs` | exit 0, **13 passed** |
| `PYTHONPATH=. py scripts/run_smoke_test.py` | exit 0; mock-only DB (5 negotiations, `mock-greedy-A/B`, 0 API calls) checked and removed |
| Guarded CLI dry run (fresh) | exit 0; 24 pairs; $0 charged; worst case $59.30496; `results/` empty afterwards |

Commits (local, **not pushed**; `1de0b27` untouched): `16977be` (F5), `b1f2535` (F1 + F2 test), and this log update.

### Updated findings status
- F1: **resolved in code** (`b1f2535`). Residual procedural risk: a pilot under a different DB name or directory.
- F2: **investigated**; post-call detection kept and tested across restarts; residual risk of one oversized call is accepted.
- F3/F4: unchanged (informational). F4's lost-call accounting is now covered by the crash charge.
- F5: **resolved** (`16977be`).
- F6: **open, blocks Stage 5**: `.env` absent.

### Updated Stage 4 steps (zero-cost, before authorising)
1. User creates `.env` from `.env.example` (both keys; values identical to the approved ones).
2. `git status` clean; `results/` contains no `pilot.db`.
3. Dry run with the real `.env`: `PYTHONPATH=. py scripts/run_pilot.py --max-input-tokens-per-call 20000 --max-cost-usd 20`.
   Expect 24 pairs, `already charged ... $0.0000`, and no refusal.
4. On authorisation only: the same command plus `--confirm`. After any stop: inspect, then (with approval) the same command plus
   `--resume --confirm`. Never a new `--db`.

---

## Zero-cost mandate (user request, 2026-10-28): the project must cost exactly $0

The paid pilot is withdrawn as a milestone. No credentials were created or requested, no provider was called, and no paid execution happened or was authorised.
`ALLOW_PAID_PROVIDER_CALLS` is never set; `.env` is absent; `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are not set.

### Graph G1 — state check. PASS
- `git status`: clean, `main...origin/main [ahead 7]`, HEAD `6415cd2`, `1de0b27` present and unamended; `results/` and `experiments/pilots/` empty.
- Re-verified, not trusted from the summary: `PYTHONPATH=. py -m pytest -q -p no:cacheprovider` → **483 passed** (128 s);
  `node --test tests/dashboard_lib.test.mjs` → **13 passed**.

### Graph G2 — provider-call audit and zero-cost guard. Loops 1–3: PASS
Every outbound path found (`git grep` for SDK constructors, `http.client`, `urllib`, `socket`, `requests`/`httpx`):

| Path | Reaches | Gate |
|---|---|---|
| `ClaudeAgent._get_client` (pilot `--confirm`, real smoke test, dashboard sandbox models, Agent Lab `claude` kind) | Anthropic API | `require_paid_calls` before `import anthropic` |
| `OpenAIAgent._get_client` (same callers) | OpenAI API | `require_paid_calls` before `import openai` |
| `lab/adapters.HTTPAgent._post` (Agent Lab `http` agents and the probe endpoint) | any URL | `require_loopback` before any connection or DNS lookup (loopback agents stay allowed) |
| `run_pilot()` without stub clients | both APIs | `require_paid_calls` before any file or lock exists |
| `scripts/run_real_smoke_test.py main()` | both APIs | `require_paid_calls` before any agent exists |
| `provenance.code_version` | local `git` subprocess | no network |

- New `src/zero_cost.py`: `ZeroCostViolation` (a `RuntimeError`, never a `TransportError`, so it never triggers reruns);
  the opt-in is the exact value `ALLOW_PAID_PROVIDER_CALLS=1`, which the $0 policy forbids; `network_tripwire()` blocks non-loopback
  `connect`/`connect_ex`/`getaddrinfo` for the whole process and records each attempt.
- `tests/conftest.py`: the opt-in is removed for every test, so zero-cost mode is always on in tests.
- Loop 2 finding: `httpx` passes hostnames to `getaddrinfo` as **bytes**. The tripwire now decodes them (it had blocked them anyway, and would also have blocked bytes loopback names).
- Loop 3 finding: the full suite showed 2 failures, both in `test_lab.py::test_idna_invalid_hostname_is_an_agent_failure`. Zero-cost mode refuses
  remote HTTP agents before the IDNA check those tests exercise. Resolution, with no assertion weakened: that test now runs with the opt-in, inside the
  tripwire and asserting no network attempt, so it still checks IDNA errors become agent failures. A new test checks default mode refuses the
  same URLs before any network call. Running it inside the tripwire showed the tripwire intercepted `getaddrinfo` before the real IDNA encoding. It now
  encodes first, like the real resolver (verified: real `getaddrinfo` raises `UnicodeError` locally for those names), with a test for it.
- Tests: `tests/test_zero_cost.py` (13), plus 3 new parametrized cases in `test_lab.py`. Mutation check: removing each of the 5 gates in turn fails 1–2 tests each; files restored.

### Graph G3 — offline simulation pilot (SIMULATED DATA). PASS
- `src/experiments/simulation.py` + `scripts/run_offline_simulation.py`: the frozen pilot matrix (8 cells × seeds 30000–30002 = 24, the same condition
  labels, seat and first-mover swaps) with two deterministic `MockAgent`s named `SIMULATED-mock-X/Y`, `runs.mode='simulation'`, inside the network tripwire.
  The script refuses to run if the opt-in is set and never deletes a database that is not purely simulated.
- Labels: every CSV row has `data_label=SIMULATED`; `summary.json` carries `data_label`; `REPORT.md` opens with the disclaimer and states that no empirical
  LLM experiment was run and the research question is unanswered. The analysis (`load_rows`) refuses any DB with a non-simulation run or a non-mock agent.
  Output lives in `artifacts/offline_simulation/`, outside `results/`, so the research dashboard (which reads `results/*.db`) never sees it.
- Command: `PYTHONPATH=. py scripts/run_offline_simulation.py` → exit 0; 24 negotiations; integrity problems 0; network attempts blocked 0; API keys present: False.
- Reproducible: a second run gave byte-identical files (sha256): `negotiations.csv` f6e57efd343d86b1f3d3aea3c6e3e309ed57c6dfa65106f9c8bc0d56422216c4,
  `summary.json` df674ec82c8d9a5bda2a3d4fc3843b64c35353172501c9f9048cbbaf89a4cada, `REPORT.md` 11518e2c81ed3c43f743f56e8d6ecaeffafa9e6678490eb7dfcfa31278c7f8a2 (after the review rewording below).
- Simulated outcome (mock agents only): 24/24 agreed; mean RWE 0.689, mean EW 0.443 in **both** condition labels. Every paired structured − baseline
  difference is exactly 0, as expected by construction (mocks ignore instructions); a test shows that check is not vacuous.

### Graph G4 — local pipeline validation. PASS
Covered end to end without network: negotiation and outcome classification (`test_protocol.py`), metrics (`test_metrics.py`), persistence and CHECK
constraints (`test_storage.py`, `test_schema_constraints.py`), budget ledger, recovery and resume (`test_pilot_resume.py`, stub clients only), zero-cost
guard (`test_zero_cost.py`), simulation, independent record re-checks (instance regenerated from its seed, optimum and utilities recomputed), analysis
and report generation (`test_offline_simulation.py`, 13 tests, including tamper detection, label checks and committed-artifact equality).

### Graph G5 — local inference assessment (read-only, nothing downloaded). Not pursued
- Hardware: Intel i7-10750H (6 cores), 15.8 GB RAM, NVIDIA RTX 2060 with **4 GB VRAM**, 74 GB free disk.
- Software: no Ollama, llama.cpp, LM Studio, PyTorch, Transformers, vLLM or GPT4All; `onnxruntime` is installed but no model weights exist on disk.
- Feasibility: a small quantized model (about 1–4B parameters) would fit, but it needs a runtime and roughly 1–5 GB of weights to be downloaded (not done;
  would need your approval), plus local compute time and power.
- Scientific caveat: the preregistered design fixes Claude Sonnet 4.6 and GPT-4.1 (§8.15). A local model run would be an exploratory deviation, not the
  preregistered experiment, and could not answer the research question as stated. **Default remains the offline mock simulation.**

### Independent review (read-only subagent): APPROVE, nothing blocking
It confirmed: SDK clients are built only in the two gated `_get_client` methods, and every caller (sandbox, Lab kinds, probe, pilot, smoke test) goes through them;
`HTTPAgent._post` is gated before any connection; `ZeroCostViolation` is never converted into a `TransportError` (no reruns) and ends Lab/sandbox runs
as stopped; the tripwire is sound for this codebase; the `test_lab.py` change is a legitimate adaptation; the labels are unambiguous and invisible to
the research dashboard; no frozen file changed; 151 targeted tests pass; it reproduced the artifacts byte-for-byte.
Non-blocking notes and how they were handled:
- (a) REPORT.md asserted "no API key was present" as a fixed sentence. Reworded to what the guard guarantees; artifacts regenerated (REPORT.md hash above).
- (b) `"simulation"` is not in the `Mode` type hint in `config.py`. **Left unchanged on purpose**: `config.py` is the frozen runtime file, the
  Literal is not enforced at runtime, and a type checker would flag the call in `simulation.py`.
- (c) A zero-cost refusal in Lab or sandbox shows as "Unexpected error". It still fails closed; a clearer message is listed under limitations.
- (d) Out of scope, reported to the user: a separate git worktree `.kilo/worktrees/reflective-snail` (detached at `46d1758`, excluded via
  `.git/info/exclude`) holds older, **ungated** code. Do not run anything from it. A loopback HTTP agent could itself call paid APIs; the guard cannot see past it.

### Final verification
| Command | Result |
|---|---|
| `PYTHONPATH=. py -m pytest -q -p no:cacheprovider` | exit 0, **512 passed** (128 s) |
| `node --test tests/dashboard_lib.test.mjs` | exit 0, **13 passed** |
| `PYTHONPATH=. py scripts/run_smoke_test.py` | exit 0; mock-only DB (5 negotiations, 0 API calls) checked and removed |
| `PYTHONPATH=. py scripts/run_offline_simulation.py` (twice) | exit 0; identical hashes; 0 integrity problems; 0 network attempts |

### What has and has not been demonstrated
- **Software validation:** demonstrated. The pipeline runs, classifies, stores, re-checks, analyses and reports correctly offline, and fails closed on any paid or remote call.
- **Simulation results:** demonstrated only as a plumbing check; mock-agent numbers carry no information about LLM negotiation.
- **Empirical LLM results:** **none.** No real-model pilot or experiment was run. H1–H3 are untested and the research question remains empirically unanswered.

### Remaining limitations
- The preregistered §8.9 inferential analysis (sign-flip permutation, bootstrap CI, Holm) is not implemented; the simulation analysis is descriptive only.
- The simulation personas produce only `agreed` outcomes; the other outcome paths are covered by unit tests, not by this run.
- Answering the research question needs real Claude and GPT calls, which cost money and are out of scope under the $0 policy.
- The paid code paths still exist, are tested only with stub clients, and are locked behind the opt-in.
- In the Lab and sandbox, a zero-cost refusal is reported as a generic "Unexpected error" rather than a dedicated message.

---

## Workbench platform (2026-10-28): PARTIAL, backend only
- G1 audit done: stdlib server plus vanilla-JS SPA with vendored Motion, no build step (no npm, so shadcn/React do not apply). Impeccable skill context loaded (no PRODUCT.md/DESIGN.md; the incumbent design system is the authority).
- Implemented `workbench/` (agents adapter registry with locked Claude/GPT/Gemini, PriorityAgent, environments with JSON/text import and field-level errors, SQLite store in sandbox_runs/, simulation runner on the unchanged NegotiationSession, analytics with evidence, exports), plus API routes in dashboard/server.py behind the existing guards.
- Tests: `tests/test_workbench.py` 9 passed; full suite **521 passed**; JS **13 passed**; no network attempts (tripwire).
- **Not done:** the Workbench UI pages (agents, environments, simulation builder, live workspace, analytics, export), the design critique, the docs (docs/workbench.md), and an independent review.

### Workbench UI and completion (2026-10-28, continued)
- **G3–G7 UI** in `dashboard/static/workbench.js` (one module plugged into `app.js`, which passes in its own helpers, router and Motion), with a `Workbench` navigation group and a token-based CSS section:
  overview with honest capability table; agent library (create, edit, duplicate, two-step delete; locked providers read-only);
  environment builder (visual editor, text/JSON import or file open, live server-side validation with field highlighting, preview with optimum);
  simulation builder (design size and warnings before running); live workspace (engine turns, seats, inventory, progress, cancel);
  negotiations table with transcripts; analytics (by condition, outcome figure, agent comparison, utility dot plot, resource priorities, evidence-linked observations); log; exports (server-built, labelled).
- **Browser verification** (Chrome on 127.0.0.1): overview, builder, a 24-negotiation run followed live, analytics, environment import→preview→load→save,
  agent validate→save→delete, light theme; no console errors. Fixed in loops: oversized figure, stale tab count, missing "still running" notice,
  focus stolen by live validation, ambiguous observation wording, the seat-mixed points label, and evidence truncation shown as a total.
  **Not verified:** phone width visually (window resize did not apply; the server's anti-framing headers correctly refuse an iframe probe).
- **Impeccable** (v4.3.1, cached engine; no download): context loaded, craft floor read, detector run once. Its only finding on new code was a layout-animated progress bar, now transform-based.
  The 4 remaining warnings are pre-existing (lines 762–848, previously acknowledged).
- **Independent review:** REQUEST CHANGES, one blocking item. Local HTTP agents were flagged as not reading instructions, but they receive the condition's instructions,
  which made 4 statements wrong. Fixed (`reads_instructions=True`; cost label now based on agent category). Non-blocking items also fixed: concession n, "largest/smallest share"
  wording, cancel after the last negotiation, the 12 KB import limit matching the server, CSV formula neutralising, a non-string id now gives 400 not 500, README label wording. The fixes are covered by
  regression tests; the reviewer was not re-run.
- **Verification:** `pytest` **526 passed**; `node --test` **13 passed**; offline simulation hashes unchanged; frozen files unchanged since `1de0b27`; no provider call, no network beyond 127.0.0.1.
- **Remaining:** responses to scarcity not analysed; no inferential tests; bilateral protocol only; phone layout not visually verified; Impeccable update (v4.8.0) not applied.

---

## Research-quality pass (2026-10-28, uncommitted by instruction)

- **G2 analysis:** `src/evaluation/inference.py`, pure Python, implementing spec §8.9 and §8.11: instance-level paired d, sign-flip permutation (10k),
  percentile bootstrap (10k), Wilcoxon sensitivity check (exact when untied and n ≤ 50, otherwise tie-corrected normal), Holm across H1–H3, and the invalid-action
  sensitivity analysis. Open details fixed before any full-run data: `ANALYSIS_SEED = 20260921`, type-7 percentiles.
  Wired into the research dashboard only for the exact complete full run (`full_design_complete`; review B1) and into `scripts/run_analysis.py` (refuses otherwise).
  `tests/test_inference.py` (22): known-answer synthetic datasets, hand-computed toy values, scipy as a test-only Wilcoxon oracle, the Holm reversal,
  nested repeats, and the dashboard lock.
- **G3 behaviour:** `workbench/analytics.py`: scarcity response (quantity terciles plus Spearman, with a per-unit value confound caveat), concession curve,
  value split, position (opener/responder, seat), per-condition agent measures, and exploratory inference (a fixed environment = one instance).
  Fixed misleading wording found by running the demo: uniform claims are now named as uniform; alignment separates strict wins from ties.
  `tests/test_workbench_analytics.py` (9).
- **G4 review (fresh, read-only):** the original HTTP-instructions defect is confirmed fixed with nothing related left wrong; no custom prompt reaches any agent.
  Blocking B1 (dashboard lock counted rows rather than checking the design) and B2 (stale "not run" text beside the tests table) are fixed with tests. Also fixed: CSV `\t`/`\r` neutralising,
  scarcity confound caveat, alignment wording, interpretation wording at d = 0, "Receives instructions" column.
- **G5 browser:** real narrow viewport via a same-origin popup. Chrome's minimum gave a 451 CSS px layout, **not 390 px**. All 10 Workbench routes and all 5
  simulation tabs (including the new sections and an expanded transcript) have no page-level horizontal overflow; 7 wide tables scroll inside their wrappers.
  Fixed: back link 22 → 24 px target; row focus outline inset. Desktop dark and light checked. Keyboard Enter expands transcripts and keeps focus. Reduced motion is
  covered by the global rule and the motion checks. Found and stopped 2 stale dev-server processes that had been answering on the same port.
- **G6 demo:** `scripts/run_workbench_demo.py`, 80 simulated negotiations end to end through the validated API; output in `sandbox_runs/workbench_demo/`.
- **G7:** `pytest` **571 passed**; `node --test` **13 passed**; `node --check` all 5 static JS files ok; offline simulation byte-identical to committed artifacts;
  demo run twice with 6 deterministic files identical; frozen files unchanged; no secrets; no network-capable imports; nothing committed or pushed.

---

## Continuation: review, fixes, 390 px verification (2026-10-29, uncommitted by instruction)

- **Correction to the entry above:** "B1 ... fixed" was not true. `full_design_complete` only counted rows (480, 8 per seed), so 480 baseline-only rows or a duplicated cell unlocked the §8.9 tests. It now requires every seed × condition × Claude seat × first mover cell exactly once, and one fixed Claude-vs-GPT model pairing. `scripts/run_analysis.py` and the dashboard both use it. The UI's "complete" now comes from the server flag `progress.full.design_complete`, never from the row count.
- **Fresh read-only review (subagent), 3 passes.** Fixed with regression tests (each mutation-checked: reverting the fix fails its test):
  - Cancel during the last planned negotiation was recorded as `completed`.
  - Wrong JSON types (list ids, `agents` not an object, non-string condition/pace/kind) gave 500 instead of 400.
  - A crafted `/api/negotiation` key (`²`, a 20-digit id) dropped the connection; `do_GET` now always answers, with the security headers.
  - The CSV helper turned `""` into `'`.
  - The workbench cancel route skipped the public-demo read-only check.
  - Provenance always recorded `zero_cost_mode: true`.
  - Per-condition concession had no n (now computed and shown).
  - Exploratory inference (~1.7 s) reran on every 400 ms poll: it is memoised (`inference.analyse_cached`) and skipped while a simulation runs.
  - The HTTP-instructions regression test now checks behaviour: the observation equals `system_instructions(view, condition)` exactly, and notes and name never appear.
- **390 px:** a genuine 390 CSS px layout viewport via headless Chrome and the DevTools protocol (innerWidth = clientWidth = visualViewport = 390; `(max-width: 390px)` matches). No page-level overflow on 38 Workbench states (light and dark) or 12 research routes.
  - Fixed: expanded transcripts ran off the scrolling table's edge (now sticky, capped to the visible width); evidence buttons were 17 px wide; swap toggles were 22 px tall.
  - Keyboard: 40 tab stops, all with a visible focus ring. Reduced motion: 0 animated or transitioning elements.
- **Verification:** see the final report for counts. Frozen files are unchanged versus HEAD. The demo was run twice: 6 deterministic files identical; `config.json`/`report.md` differ only in ids and timestamps. The offline simulation is byte-identical to the committed artifacts. No network, no installs, no spend, nothing committed.

---

## Final pre-commit review (2026-10-29, uncommitted by instruction)

Read-only review of all 23 changed or new files. The statistics were re-checked independently:
- **Sign-flip:** within Monte Carlo error of exact enumeration.
- **Wilcoxon:** matches scipy (exact and tie-corrected normal).
- **Bootstrap:** matches numpy's linear percentile.
- **Holm:** matches a hand-worked example.
- **Nesting and §8.11:** confirmed.

Four defects were found and fixed. Each code fix has a regression test that fails when the fix is reverted. No frozen research file changed and the research design is unchanged.
- **Confirmatory-analysis eligibility** (`full_design_complete`, `dashboard/data.py`) now also requires the approved §8.15 snapshots on every row (`claude:claude-sonnet-4-6` vs `openai:gpt-4.1-2025-04-14`) and `MAX_ROUNDS` = 10. Before, a complete design with other model ids or another round limit unlocked the tests. The runner already refuses those configurations; the gate now enforces them too.
- **The demo** (`scripts/run_workbench_demo.py`) refuses to delete the Workbench's own database. Before, `--out sandbox_runs` would have unlinked `sandbox_runs/workbench.db`.
- **Concession wording** (`workbench/analytics.py`) now says "raised what it asked for" when an agent hardened. Before, it said "lowered ... by -0.12".
- **Documentation:** `docs/workbench.md` and `scripts/run_analysis.py` describe the complete-design gate accurately: every cell once, approved snapshots, `MAX_ROUNDS` 10.

**Verification.** `pytest` 597 passed; `node --test` 14 passed; `node --check` ok on all static JS; `git diff --check` clean.
- Demo run twice: 6 deterministic files byte-identical.
- Offline simulation: byte-identical to the committed artifacts.
- No secrets, network or provider imports, installs, or spending.

**What this does and does not show.** Every test passed, but that validates software only.
- **No empirical results:** no real-model pilot or full run exists, so H1–H3 remain untested and the research question unanswered.
- **Synthetic runs are not evidence:** that includes the rule-based Workbench demo, the offline simulation and the synthetic analysis fixtures. They say nothing about Claude, GPT, Gemini or any commercial LLM.

---

## Platform UI/UX transformation (2026-10-30, uncommitted by instruction)

Redesign of the whole dashboard into a guided multi-agent experimentation platform. Research protocol, frozen files, seeds, prompts, hypotheses, metrics and `results/` are untouched. No provider call, no paid API, no network beyond 127.0.0.1 (one exception, at build time: the OFL-licensed Inter font was downloaded once from jsDelivr and is now self-hosted).

- **Skills used:** apple-design (typography, materials, motion, reduced motion), dataviz (form choice, palette re-validated on the new surfaces with its validator: all checks pass in light and dark), impeccable (context, craft floor, detector run once), claude-in-chrome (visual verification). Graphify was inspected and not used: it builds code knowledge graphs offline, not in-page charts. Impeccable's concept-roll was skipped because the brief pins the direction; PRODUCT.md records the brief.
- **Design system:** new tokens (Apple-style neutrals, one accent, radii 8/12/16), Inter self-hosted (SF on Apple devices), light/dark/system themes with a cross-fade switch, manual reduce-motion, print styles. The decorative particle background now runs only on the research replay, where it encodes turns.
- **Information architecture:** Workspace (Start, Agents, Resources, Experiments, Analytics, Reports), Playground (Try, Compare runs, Agent Lab), Research study (Overview moved to `#/study`, Data collection, Results, Methodology, Configuration), Settings.
- **New functionality:** guided Start page from real records with a one-click sample experiment; three agent paths; **Python upload** (`workbench/inspect_agent.py`: `ast` only, never executed; `python_upload` kind is never runnable; upload routes accept 128 KB bodies, others keep 16 KB; starter template, tested against the engine); resource builder with units, categories, sliders, balance-to-100 and a who-values-what view (`meta` is labels only); five-step experiment wizard; live monitoring (counters, offers/rejections, outcome tape, transcript, stop); per-experiment analytics with 12 chart types, condition filter, click-to-filter, legend toggles and click-through to transcripts; pooled Analytics and two-experiment Compare (labelled exploratory); printable Reports with CSV/JSON/MD; Settings.
- **Defects found and fixed in browser loops:** stale server on :8765 with an old CSP hash blocked the theme script (moved verification to :8770; the old process was left running); unwired legend toggles; overlapping scatter series (later series now rings); oversized text in wide charts; colliding line labels; a full-ring donut drawn as a bitten disc; new resource sets opening in an error state; fieldset grid and `max-content` summaries widening phones; legacy empty states laid out sideways; duplicated copy; a `normalize100` deficit loop (caught by its unit test).
- **Verification:** `pytest` **617 passed** (597 + 20 new); `node --test` **20 passed** (14 + 6 new); `node --check` all static JS; `git diff --check` clean. Chrome: all 32 routes render with no JS errors; full journey run (upload compatible and rejected files, wizard, 24-negotiation live run, analytics interactions, report, exports, provider test that sends nothing). True 390 px viewport via the DevTools protocol: 30 routes and 3 experiment tabs × light and dark, no page overflow after fixes. Reduce motion: 0 animated elements.
- **Limitations:** uploaded agents cannot run (no isolated sandbox; serve them as local HTTP agents instead); charts have no zoom; PDF is the browser's print-to-PDF; pooled analytics reads the newest 30 experiments; Inter is a stand-in for SF Pro off Apple devices (detector flags it as overused); Barlow font files remain on disk unused.
