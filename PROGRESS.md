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
