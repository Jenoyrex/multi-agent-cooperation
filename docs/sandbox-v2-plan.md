# Sandbox v2 plan: Cooperation Lab as an open, local AI-agent sandbox

Status: exit checks met on branch `sandbox-v2` (worktree `../mini-project-sandbox`). Nothing is pushed.

Branch note: `sandbox-v2` starts from `ui-simplify` (f6c8a5e). `ui-simplify` had uncommitted Loop 2 work and unticked
exit checks. Commits were requested on `sandbox-v2` only, so that work was copied here and finished here. The
`ui-simplify` worktree and the `docs/readme-refresh` tree were left untouched.

## a) Features

Each feature's location and test are in the "Feature table" at the end.

| ID | Feature |
|---|---|
| S01 | `pip install` + `cooperation-lab` command; `--host`, `--port`, `--no-browser`; free port; opens browser |
| S02 | Data folder in the user's home directory (`~/.cooperation-lab`, or `$COOPERATION_LAB_HOME`) |
| S03 | Prompt agent: connection + model + persona + temperature |
| S04 | HTTP agent: loopback as before; non-loopback only in local mode with consent |
| S05 | Python file agent: subprocess, timeout, no inherited keys, explicit consent; inspect-only in the demo |
| S06 | Local models: Ollama, LM Studio, custom OpenAI-compatible URL on loopback; model list from the server |
| S07 | BYO key: OpenAI, Anthropic, Gemini, custom OpenAI-compatible (base URL + key) |
| S08 | "Test connection": one tiny request, only when clicked |
| S09 | Key manager: add/remove, masked; memory or `.env` only; loopback + same-origin endpoints |
| S10 | Consent: estimated tokens/cost from prices the user enters; "Run (uses your API key, you pay)" |
| S11 | Hard per-experiment token and spending cap; stops cleanly |
| S12 | Retries, timeouts, rate limit, per-run token/cost recording |
| S13 | Two agents now; data model can grow to more (labels, seats and profiles are keyed, never positional) |
| S14 | Sweeps: same agents x resource sets (x conditions/personas) x repetitions, seeds recorded |
| S15 | Resource events, protocol `sandbox_v1` (docs/sandbox-protocol.md) |
| S16 | Behaviour profile per agent (docs/sandbox-metrics.md) |
| S17 | Reaction view: demand before vs after a resource event |
| S18 | Statistics: 95% bootstrap CIs, n, paired comparisons, small-n warnings, "exploratory" labels |
| S19 | Plain-language findings that never claim more than the data shows |
| S20 | Exports (CSV/JSON/MD) with data source labels and no keys |
| S21 | Data source label on every experiment |
| S22 | PUBLIC_DEMO=1 disables keys, providers, local models, Python and non-loopback calls |
| S23 | "Run it on your computer" page |
| S24 | Simplified UI: at most 6 top-level nav items, one primary action per page |
| S25 | Docs: README split, workbench, protocol, metrics, Help glossary |
| S26 | Frozen research study unchanged |

## b) Architecture

New package `sandbox/`. It may import from `src/`, never modifies it. Existing `workbench/`, `lab/` and `dashboard/`
gain small hooks only.

| Module | Job |
|---|---|
| `sandbox/cli.py` | `cooperation-lab` entry point; sets the data folder; binds 127.0.0.1 unless `--host` |
| `sandbox/policy.py` | One place for every gate: demo, loopback request, URL rules |
| `sandbox/keys.py` | In-memory vault, env and `.env` lookup, masking, `redact()` |
| `sandbox/providers.py` | Provider presets, saved connections, test connection, model list |
| `sandbox/model_agent.py` | `PromptAgent`: AgentView + persona to provider, strict parse |
| `sandbox/python_agent.py`, `sandbox/python_runner.py` | Parent and child of the Python file subprocess |
| `sandbox/engine.py` | `sandbox_v1` engine, used only for experiments with resource events |
| `sandbox/experiment.py` | Sweep, event, persona, price and cap validation; data source labels; estimate; consent token; budget |
| `sandbox/analysis.py` | Behaviour profiles, reactions, sweep comparison, findings |
| `sandbox/api.py` | `/api/models/*` routes and the estimate route |
| `dashboard/static/sandboxui.js` | Connections page, Run-it-yourself page, consent panel, analysis panel |

**Engines.** No events: the frozen `NegotiationSession`, unchanged. With events: `sandbox/engine.py`, which reuses
`validate_allocation`, `compute_utility`, `compute_optimal_welfare`, `AgentView`, `Budget` and `NegotiationRecord`.
A test checks both engines produce the same record when there are no events.

**Provider request shapes, from current official docs (checked 2026-11-04).**

| Provider | Base URL | Structured output | Source |
|---|---|---|---|
| Ollama | `http://localhost:11434/v1` | `response_format`; docs do not name the type, so `json_schema` is unconfirmed | docs.ollama.com/api/openai-compatibility, /capabilities/structured-outputs |
| LM Studio | `http://localhost:1234/v1` | `json_schema`; "Not all models are capable" | lmstudio.ai/docs/developer/openai-compat/structured-output |
| OpenAI | `https://api.openai.com/v1` | `json_schema` on chat completions, specific models | developers.openai.com/api/docs/guides/structured-outputs |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | Via OpenAI SDK; compatibility "still in beta" | aistudio.google.com/docs/openai |
| Anthropic | SDK default | `output_config.format` json_schema | platform.claude.com/docs/en/build-with-claude/structured-outputs |
| Groq (custom) | `https://api.groq.com/openai/v1` | User chooses per connection | console.groq.com/docs/openai |
| OpenRouter (custom) | `https://openrouter.ai/api/v1` | `json_schema` on some models; user chooses | openrouter.ai/docs/api-reference/overview |

Reasons for departing from the study agents:

- **Anthropic does not use a forced tool.** Current docs say forced `tool_choice` returns a 400 on Claude Opus 5.5,
  Sonnet 5.5 and Fable 5.1, so the sandbox uses `output_config.format`.
- **Temperature is optional and not sent unless the user sets it.** Whether every current model accepts it could not
  be confirmed, and a wrong value would abort runs.
- **Structured-output mode is a per-connection setting** (`json_schema`, `json_object`, `none`), because support varies
  by model on Ollama, LM Studio, Groq and OpenRouter. Whatever the mode, parsing is strict: `json.loads` then the frozen
  `parse_action`. A failure is `invalid_action`, never repaired.
- **Gemini needs no Google SDK.** Its OpenAI-compatible endpoint is enough, so nothing is under "ASK ME" for it.

**Data model.** Everything is additive JSON in the existing documents, so old experiments load unchanged:

- `config.environments` (sweep; old docs fall back to `[config.environment]`), `config.events`, `config.personas`,
  `config.prices`, `config.caps`, `config.source_labels`, `config.sandbox_protocol`.
- Each trial: `env_index`, `env_name`, `persona`, `usage`, `final_pool`, `final_valuation`, `events_fired`,
  `pre_event_optimal_welfare`.
- New `connections` table, created by the existing `CREATE TABLE IF NOT EXISTS`. Holds base URL and settings, never a key.
- New agent kinds: `prompt`, `http_remote`, `python_file`. Existing kinds and ids stay as they are.
- Cap stops reuse status `cancelled` plus `stop_reason`, because old databases have a CHECK constraint on status.

**API routes.** `GET /api/models/connections`; `POST /api/models/connections`, `/connections/ID/delete`, `/keys`,
`/keys/delete`, `/test`, `/list`; `POST /api/workbench/simulations/estimate`.

**UI.** Nav: Home, New experiment, My experiments, Agents & models, Research study, Help. Additions: connections page,
new agent types, resources step (sweep, events), consent panel on Review, analysis panel, `#/run-locally`.

## c) Enforcement and tests

| Rule | How it is enforced | Test |
|---|---|---|
| Keys never stored or returned | Vault in memory; `.env` read with `dotenv_values`, never written; connections hold no key; `redact()` at `store.put`, `Handler._json` and traceback printing | Dummy key run through a fake server that echoes it in errors; search DB bytes, every export, API responses and captured logs |
| Key endpoints local only | `/api/models/*` needs loopback client, local Host and, on POST, an exact same-origin `Origin` | Forged Host, missing Origin, foreign Origin, non-loopback client |
| Consent | Start refused unless the consent token matches the recomputed hash of the design, and each required consent is true | Missing, stale and swapped-design tokens |
| Cost cap | Frozen `Budget` with a worst-case reserve per call, shared across the experiment; a token cap is mandatory for API models | Cap reached stops cleanly, no call after the stop |
| Local binding | CLI and server default 127.0.0.1 | CLI test on a real port |
| Public demo | Routes absent, kinds not executable, refused in `validate`, and again at runtime in every builder | Each feature attacked through HTTP and directly |
| Python isolation | Subprocess with an allowlisted environment and per-turn timeout; never imported | Child prints its environment; dummy key absent; hang is killed |
| Privacy | `AgentView` / `SandboxView` built by the engine; requests captured at the fake server | Opponent's distinctive values absent from every request, HTTP body and Python view |

Limit stated honestly in the UI: a Python file agent runs with the user's own permissions. It cannot inherit keys through
its environment, but it could read files the user can read, including `.env`. That is why it needs explicit consent.

## d) Zero-cost policy split

- **Research runner:** unchanged. `src/zero_cost.py`, its `ALLOW_PAID_PROVIDER_CALLS` guard and the study agents stay
  locked. The research-locked `claude`, `openai` and `gemini` agent kinds stay locked.
- **Sandbox:** builds its own clients in `sandbox/`. Loopback local models always allowed in local mode. BYO-key
  providers only in local mode, with consent. `PUBLIC_DEMO` blocks both.
- **`src/zero_cost.py` does not change.** `lab/adapters.py` gets an `allow_remote` flag used only by `http_remote`.
- **Our budget:** tests use a fake OpenAI-compatible server on 127.0.0.1 and a stub Anthropic client. No paid call is made.
  Ollama and LM Studio are not running on this machine, and nothing will be installed.

## e) Acceptance checklist (= exit checks)

- [x] Every S-feature ticked with location and test
- [x] `pip install` + `cooperation-lab` works in a fresh venv; binds 127.0.0.1 by default (Windows; from the local path)
- [x] Local-model, BYO-key (fake server), prompt, HTTP and Python agents complete a negotiation in tests
- [x] No key in DB, exports, logs or browser responses (test)
- [x] PUBLIC_DEMO=1: no model, key, Python or non-loopback feature can run (tests)
- [x] Resource events, sweeps and behaviour profiles work, with documented definitions and CI/n shown
- [x] `git diff --stat main...sandbox-v2 -- docs/spec.md src/` is empty
- [x] All tests pass; no console errors; no overflow at 390 px
- [x] Zero paid API calls

## Blockers

None yet.

## Loop notes

**Loop 0: plan.** This document. Provider docs checked; two departures from the study agents recorded above.

**Loop 1: installable app.** `cooperation-lab` entry point, home data folder, packaging fixed (the old config could not
ship `src.*`). Verified in a fresh venv: `pip install .`, command starts on 127.0.0.1, health 200, static files and
experiments work, data lands in the data folder, uninstall removes the command. Not verified on macOS or Linux (no such
machine here). `pip install git+https://...` needs the branch pushed, so it was tested from the local path.

**Loops 2 to 4: models, agents, resources (backend).** Committed together because the modules depend on one another.
Fake-server and stub tests cover local and BYO-key agents, test connection, 401/429/timeout/malformed output, caps,
consent, key leaks, all agent types, privacy, the demo lockout, events and sweeps. pytest 692 at this point. UI comes in Loop 6.

**Loop 5: analysis (backend).** Behaviour profiles, event reactions, sweep comparison, findings, labelled exports; chart numbers recomputed from `metrics.csv` in a test. pytest 703, node 20.

**Loop 6: UI and public site.** Six-item nav (Agents & models), Model connections page, prompt/HTTP/Python agent flows, sweep,
event and persona editors, consent panel on Review, behaviour profile, comparison and reaction views, report sections, Run-it-yourself
page, demo lockouts. Verified in headless Chromium: four journeys (local model; BYO key to consent and run; sweep + event to
analysis and report; Python file with consent), 17 routes x 3 viewports x 2 themes, analysis views at 390 px and 1280x666, demo
routes. Fixed along the way: a console 400 on an empty model, wrong copy, a clipped price column.

**Loop 7: docs and safety review.** README split, docs/workbench.md, Help glossary. Read-only review of keys, consent, demo lockout,
subprocess and binding found and fixed:

- A remote HTTP URL could carry a secret in its query string into reports and exports. Refused now.
- A client on another machine (with `--host`) could save a Python file for the local user to run later. Saving is local only now.
- Report and provenance said zero-cost mode was on for API-key runs. Corrected, and the report no longer says no LLM was run.
- The CLI never set the `.env` search folder, and an installed app had no Agent Lab secret. Both fixed.

Left as stated limits, not bugs: a Python file runs with the user's permissions (it could read `.env`), which the consent text says;
stopping one kills that process but not any children it started; `--host` exposes saved experiments, including Python source, to
anyone who can reach the port, which the CLI warns about.

## Known unverified

- **No real model has been run.** Ollama and LM Studio are not on this machine, and no paid call was made.
- **Ollama:** its docs confirm `response_format` but do not name `json_schema`. If a model misbehaves, a custom connection can use JSON mode.
- **Gemini:** docs show structured output through the SDK's `parse` helper; the wire format and `max_tokens` were not confirmed by a real call.
- **macOS and Linux** install: not run. **`pip install git+https://...`**: needs a push; the same install was tested from the local path.
- **Chrome extension** was not connected, so browser checks used headless Chromium over CDP.

## Feature table

| ID | Feature | Location | Test |
|---|---|---|---|
| S01 | `cooperation-lab` command | `sandbox/cli.py`, `pyproject.toml` | `test_sandbox_cli.py`; fresh-venv run |
| S02 | Home data folder | `sandbox/cli.py` | `test_command_serves_on_loopback...` |
| S03 | Prompt agent | `sandbox/model_agent.py`, `workbench/agents.py` | `test_local_model_agent_plays...`; journey 1 |
| S04 | HTTP agents, remote with consent | `lab/adapters.py`, `workbench/agents.py` | `test_remote_http_agent_needs_consent...` |
| S05 | Python file agent | `sandbox/python_agent.py`, `python_runner.py` | `test_python_file_*`; journey 4 |
| S06 | Local models | `sandbox/providers.py` | `test_presets_match...`, `test_model_list...` (fake server) |
| S07 | BYO key providers | `sandbox/providers.py`, `model_agent.py` | `test_byo_key_agent...`, `test_anthropic_*` (stub) |
| S08 | Test connection | `sandbox/providers.py`, `sandboxui.js` | `test_connection_test_*` |
| S09 | Key manager | `sandbox/keys.py`, `api.py`, `sandboxui.js` | `test_key_routes...`, `test_dummy_key_appears_nowhere` |
| S10 | Consent and estimate | `sandbox/experiment.py`, `sandboxui.js` | `test_consent_is_for_this_exact_design`; journey 2 |
| S11 | Hard caps | `sandbox/experiment.py` (frozen `Budget`) | `test_cap_reached_stops_cleanly...` |
| S12 | Retries, timeouts, rate limit, usage | `sandbox/model_agent.py`, `analysis.py` | `test_rate_limit...`, `test_timeout...`, `test_cost_and_tokens_add_up` |
| S13 | Data model can grow | `workbench/simulations.py`, `analysis.py` (keyed by label) | `test_old_saved_experiments...` |
| S14 | Sweeps | `sandbox/experiment.py`, `workbench/simulations.py` | `test_sweep_*`, `test_personas_*`; journey 3 |
| S15 | Resource events | `sandbox/engine.py`, docs/sandbox-protocol.md | `test_sandbox_events.py` |
| S16 | Behaviour profile | `sandbox/analysis.py`, docs/sandbox-metrics.md | `test_metrics_by_hand` and others |
| S17 | Reaction view | `sandbox/analysis.py`, `sandboxui.js` | `test_reaction_by_hand`; journey 3 |
| S18 | CIs, n, pairing, small-n | `sandbox/analysis.py` | `test_uncertainty_is_counted...`, `test_small_samples...` |
| S19 | Plain-language findings | `sandbox/analysis.py` | `test_findings_state_uncertainty...` |
| S20 | Labelled exports, no keys | `workbench/export.py` | `test_every_export_carries...`, `test_chart_numbers_equal...` |
| S21 | Data source label | `sandbox/experiment.py` | `test_local_model_agent...`, `test_byo_key_agent...` |
| S22 | Public demo lockout | `sandbox/policy.py`, `dashboard/server.py`, `workbench/api.py` | `test_public_demo_locks_out...`, `test_public_demo_host_runs_without...`, `test_public_demo.py` |
| S23 | Run-it-yourself page | `sandboxui.js` | Browser checks, local and demo |
| S24 | Simplified UI | `index.html`, `lib.js`, `app.js` | `navOf` node test; browser sweep |
| S25 | Docs | README, docs/* , Help | Read through |
| S26 | Frozen study unchanged | No change to `src/`, `docs/spec.md` | `test_the_frozen_study_is_untouched` |
