# Completion plan — Cooperation Lab

Start-main: `633af313bc49869214423525ac80e6a1152fca5f` (origin/main, 2026-11-11). Work branch: `feat/completion`
(worktree `../mini-project-complete`). Frozen: `docs/spec.md`, `src/` — proved by
`git diff --stat 633af31...main -- docs/spec.md src/`.

## Loop 0 — audit (2026-11-11)

| Item | Finding |
|---|---|
| Branches | 13 local + origin; only `docs/readme-at-a-glance` (PR #1, open, 1 commit) is not on main |
| Worktrees | main folder (`ui/simple`, clean); `mini-project-sandbox` (`sandbox-v2`, clean, merged as PR #2); `mini-project-ui` (`ui-simplify`, **3 uncommitted files, left untouched**); `.kilo/worktrees/reflective-snail` (detached `46d1758` "Implement Phase 2 Agent Lab", already in main's history) |
| refs/original | none |
| Live demo | `/healthz` → `{"status": "ok"}`; it does not report a commit (see C-05) |
| sandbox-v2 | merged (PR #2) |
| finish-plan, references, team, budget-plan, run-checklist, viva-qa docs | **not present on any branch** → written in Loop 5 |
| Baseline tests | 725 pytest + 24 node = **749**, all passing (Python 3.12.4, start-main) |

## Open items

| ID | What | Loop | Exit check | Status |
|---|---|---|---|---|
| C-01 | Python 3.13+ `UnicodeEncodeError` test in `tests/test_lab.py` | 1 | passes on 3.12 and 3.14 | ✅ |
| C-02 | Dated correction in PROGRESS.md: the statistical tests are written (`src/evaluation/inference.py`) | 1 | entry present | ✅ |
| C-03 | README claims match the code | 1/5 | spot-checked list in Loop 5 note | ✅ |
| C-04 | PR #1 (`docs/readme-at-a-glance`) is stale | 6 | in APPROVAL BATCH | APPROVAL |
| C-05 | `/healthz` does not report the deployed commit; Render's dashboard does | 6 | checked through the Render deploy log instead (no code change: YAGNI) | ✅ |
| K-01 | Specific provider errors: invalid key, quota/billing, rate limit, model not found, timeout, local server not running; never the key | 2 | `test_each_provider_failure_gets_its_own_message_and_no_key` (9 cases), `test_a_run_that_fails_explains_why...` | ✅ |
| K-02 | "How to get your API key" panel per provider: console, "a chat subscription is not an API key", "you pay your provider", pricing link | 2 | `test_every_api_preset_links_its_official_console...`, `test_help_page_links...` | ✅ |
| K-03 | Key save modes: this session only (default) or the data folder's `.env` (warning; never in git) | 2 | `test_session_only_is_the_default...`, `test_save_to_env_writes_one_line...`, `test_saving_into_a_repository_needs_env_to_be_ignored`, `test_this_repositorys_env_is_gitignored` | ✅ |
| K-04 | Live token count during a run; tokens per agent after it | 2 | `test_tokens_are_reported_per_agent_and_live_spend_matches` + journey 2 | ✅ |
| K-05 | Cap stop is clean and shown | 2 | `test_the_cap_stops_cleanly...`, `test_cap_reached_stops_cleanly...` | ✅ |
| K-06 | Key-leak test over DB, exports, logs, HTTP | 2 | `test_dummy_key_appears_nowhere` (DB bytes, exports, API, logs) | ✅ |
| K-07 | PUBLIC_DEMO lockout for every key/model route | 2 | `test_public_demo_has_no_key_or_model_route` + journey 5 | ✅ |
| K-08 | Research study page + docs: the pilot uses your own `.env` keys; the dry run shows cost; nothing paid without `--confirm` | 2 | text present | ✅ |
| F-01 | Core features end to end (agents, resources, experiments, analysis, exports, help, install) | 3 | 5 browser journeys | ✅ |
| R-01 | Reliability, accessibility, performance, security review | 4 | Loop 4 note | ✅ |
| D-01 | Documentation set | 5 | files present, claims checked | ✅ |
| S-01 | Ship: PRs merged, deploy live, pip install | 6 | Loop 6 note | |
| F-02 | Prompt-agent form kept the previous connection's model after switching connection | 3 | browser check | ✅ |
| R-02 | Zero-cost refusal shown as "Unexpected error" in Try-it and Agent Lab | 4 | `test_zero_cost_refusal_is_explained_not_an_unexpected_error` | ✅ |
| R-03 | Performance and old-data loading | 4 | `tests/test_many_experiments.py` (120 experiments) | ✅ |

## Loop notes

**Loop 1 (integrate, leftovers).** sandbox-v2 is already on main (PR #2); nothing finished is unmerged except stale PR #1.
Fixed the 3.13+ test (`UnicodeEncodeError` is a subclass; the test now matches `Unicode\w*Error`). Added a dated
correction to PROGRESS.md. README: removed "Live demo: not deployed yet" (contradicted the top), updated the key, team and
"no real model" claims; docs/workbench.md agent table now lists prompt, remote HTTP and Python kinds.

**Loop 2 (bring your own key).** `providers.classify()` gives one message per failure (invalid key, no credit/billing,
forbidden, rate limit, model not found, provider 5xx, timeout, network, local server not running) for Test connection
and for runs (via the SDK error kept as `__cause__` by the frozen retry loop). Key help panel per provider with official
links (checked 2026-11-11). Save modes: session (default) or the data folder's `.env`, refused if git could commit it.
Forget all keys. Live tokens against the cap; tokens per agent. Research study page and README say the pilot uses your
own `.env` keys and the dry run is free. 25 new tests in `tests/test_byok.py`.

**Loop 3 (core features, browser).** All 5 journeys pass at 1280×800 with zero app console errors: (1) sample → live →
report; (2) fake-key API connection: wrong key → "Your API key was rejected" (key redacted), right key → Find models →
Test → prompt agent → estimate with prices, cap, consent → live tokens → tokens per agent; (3) keyless fake local
server vs rule-based with a resource event → Resource events reaction view (+ a real local Ollama `llama3.2` connection
test, free); (4) sweep over 3 resource sets → Compare groups with 95% CI and n → 10 exports, all labelled, no key,
UI number traced to `behaviour.csv`; (5) PUBLIC_DEMO: model/key pages locked with explanations, key routes absent
(405/404), Run-it-yourself page. No overflow at 390 px (21 routes + tabs) or 1280×666, light and dark.

**Loop 4 (reliability, a11y, security).** Refresh during a run keeps the live view; Stop leaves a clean cancelled
experiment whose analysis loads. Old experiments without the new fields load (test). 120 saved experiments: every list
route well under 3 s (test). Focus-visible and reduced-motion rules present; sampled text contrast passes AA in both
themes (badge 4.6:1 light). Security review: key handling, 127.0.0.1 default, same-origin, demo lockout, Python
subprocess (allowlisted env, timeout), remote URLs (no query/credentials), 16 KB body cap: no new issue. Fixed the
zero-cost "Unexpected error". Not fixed: the study pages report `scrollWidth` 518 px at 390 px but cannot scroll
sideways (decorative background layer, out of scope).

**Loop 5 (docs).** README (BYO key table, subscription ≠ key, save modes, errors, study keys, team and guide),
docs/references.md (VERIFIED/TO CONFIRM), team.md (from git history), budget-plan.md (dry-run figures),
run-checklist.md, viva-qa.md (15 Q&As), sandbox-metrics.md (per-agent and live usage).

## Blockers

## APPROVAL BATCH
