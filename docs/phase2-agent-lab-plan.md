# Phase 2: Agent Lab, plan and architecture

Status: vertical slice implemented, with the information boundary sealed
(design balanced-4-distinct/v2; see "What exists now"). This document
does not change the Phase-1 preregistration in `docs/spec.md`; nothing here
applies to the controlled experiment.

## 1. Goal

Let a third party bring two agents, put them in the same controlled
negotiation environment, repeat the negotiation under a balanced design,
and read objective, evaluator-computed measurements of what happened.

    CONTROL THE ENVIRONMENT -> LET AGENTS ACT -> RECORD EVERYTHING -> EVALUATE OBJECTIVELY

The lab reports measurements. It never ranks agents or declares a winner.

## 2. The Phase-1 boundary (frozen, not modified by Phase 2)

| Component | Files | Phase 2 uses it as |
|---|---|---|
| Protocol engine, outcomes, invalid-action and final-turn rules | `src/negotiation/protocol.py` | called unchanged: `NegotiationSession(agent_A, agent_B, EvaluatorState)` |
| Privacy boundary | `src/agents/base.py` (`AgentView`, `Agent`) | the only input an adapter ever receives |
| Action schema, error types | `src/agents/schema.py` | adapters raise `InvalidAgentOutputError` / `TransportError` exactly as Phase-1 agents do |
| Repair-free output parsing | `src/agents/parsing.py` | the HTTP adapter feeds the endpoint's JSON through `parse_action` |
| Prompts and instruction variants | `src/agents/prompting.py` | read-only: the same text is included in the HTTP observation |
| Claude / GPT agents, approved settings | `src/agents/claude_agent.py`, `openai_agent.py`, `src/experiments/config.py` | instantiated with `APPROVED_SCIENTIFIC` / `APPROVED_OPERATIONAL`, as the sandbox already does |
| Instance generation, optimum | `src/environment/*` | called unchanged to build seeded scenarios |
| Phase-1 metrics | `src/evaluation/metrics.py` | reused through `dashboard/data.summarize` |
| Runner, pilot, storage, `results/` | `src/experiments/runner.py`, `pilot.py`, `src/storage/db.py` | **not used** by the lab; the lab never opens a research database |
| Preregistration | `docs/spec.md` | unchanged |

Research Mode and Agent Lab share the engine and nothing else.

## 3. Architecture

    Agent Lab (lab/)                         Research Mode (unchanged)
    ----------------                         -------------------------
    AgentSpec (JSON, validated)              ExperimentConfig
       |  build_agent()                      run_batch / run_pilot
       v                                     results/*.db
    Agent adapter (src.agents.base.Agent)
       - ScriptedAgent  = MockAgent
       - ProviderAgent  = ClaudeAgent / OpenAIAgent (server keys, approved settings)
       - HTTPAgent      = external endpoint, strict JSON contract
       |
       v
    NegotiationSession (frozen engine)  <- EvaluatorState from a keyed hidden seed
       |
       v
    NegotiationRecord -> outcome metrics (Phase-1 functions)
                      -> behavioral metrics (lab/metrics.py)
       |
       v
    Evaluation report (sandbox_runs/lab/<id>.json, exploratory)

MAS survey (Dorri et al., 2018) framing, used only as vocabulary: an agent
receives an observation of its environment, acts, and the interaction
history conditions its next action. In this lab the observation is the
`AgentView`, the action is a `NegotiationAction`, the history is the public
transcript, and evaluation happens outside the agents in a controlled
environment.

## 4. Adapter interface

Every adapter is a subclass of the frozen `Agent` ABC:

    async def generate_response(self, view: AgentView) -> NegotiationAction

Expected failures surface as `InvalidAgentOutputError` (becomes the frozen
`invalid_action` outcome) or `TransportError` (aborts the negotiation; never
a strategic outcome). This is the contract Phase-1 agents already follow, so
the engine needs no changes. Any other exception is treated as an
unexpected server error: the evaluation stops, the trial is not recorded,
and its block is excluded.

### 4.1 HTTP agent contract (version `negotiation-lab/1`)

The server POSTs one JSON observation per turn:

    {
      "contract": "negotiation-lab/1",
      "role": "A",
      "resource_pool": {"widgets": 20, ...},
      "own_valuation": {"widgets": 1.5, ...},      # points per unit, own only
      "round": 3, "max_rounds": 10, "rounds_remaining": 7, "is_final_round": false,
      "allowed_actions": ["OFFER", "ACCEPT", "WALK_AWAY"],
      "standing_offer": {"by": "B", "round": 2, "allocation": {"A": {...}, "B": {...}}} | null,
      "transcript": [{"round": 1, "actor": "A", "action": "OFFER", "allocation": {...}, "message": "..."}],
      "instructions": "<the exact Phase-1 system text for this role and variant>",
      "prompt": "<the exact Phase-1 user-turn text for this view>"
    }

and expects:

    {"action_type": "OFFER" | "ACCEPT" | "WALK_AWAY",
     "allocation": {"widgets": 12, ...} | null,     # units YOU receive (OFFER only)
     "message": "short public message" | null}

`allocation` is single-sided, exactly as Phase-1 models answer; the other
side is `pool - mine` (`parse_action`, no repair). Every field of the
observation is derived from the `AgentView` alone, so the endpoint cannot
receive the opponent's valuation, the optimum, seeds or evaluator state:
those values are not in scope when the observation is built.

`allowed_actions` is informational: the engine still enforces the rules
(an ACCEPT with nothing to accept is `illegal_accept`; an OFFER on the final
round ends in `timeout`), so the contract cannot weaken them.

`instructions` and `prompt` carry the same text the Phase-1 models receive,
so an LLM-backed endpoint can be given information-equivalent prompts (the
"standardized prompts across models" principle of Lorè & Heydari, 2024).

### 4.2 Providers

| Provider | Status |
|---|---|
| Scripted test agent (`MockAgent`) | available; rule-based, ignores instructions |
| Anthropic Claude Sonnet 4.6 | available with the server's `ANTHROPIC_API_KEY`, approved settings only |
| OpenAI GPT-4.1 | available with the server's `OPENAI_API_KEY`, approved settings only |
| Custom HTTP endpoint | available; any language, any model behind it |
| Google Gemini, Ollama | **not implemented**. Either can be connected today through a small HTTP wrapper; native adapters are a later step |

## 5. Security model

Each item says what is enforced in code. §11 lists what cannot be.

- **No code upload, no code execution.** An agent is a JSON `AgentSpec`
  (`kind` + parameters). There is no path that evaluates, imports or runs
  user-supplied code.
- **Provider keys stay server-side.** They come from the server's `.env` and
  are never sent to the browser; `/api/sandbox/info` reports only whether
  each key is present.
- **Endpoint tokens are session-scoped.** An optional bearer token is held in
  memory on the adapter object for one evaluation. It is removed from the
  stored spec, never written to disk, never returned by any endpoint, never
  placed in transcripts or call records, never logged, and sent only to its
  own endpoint. Over `http://` to another machine it travels unencrypted
  (the UI says so).
- **Scenario secret.** Hidden scenarios are keyed with `LAB_SCENARIO_SECRET`
  (§7.2). The secret and every derived seed stay in server memory: they are
  never returned, stored, logged, placed in provenance or sent to an agent.
  Evaluations refuse to start without it.
- **Sealed API while any evaluation runs.** `GET /api/lab/evaluations` and
  `GET /api/lab/evaluations/<id>` return, for every evaluation, only: id,
  created, status, planned, blocks, resource types, strategy, agent kind and
  label, and finished/aborted counts. The running evaluation also shows its
  current position and, per trial, only position, block, status, outcome,
  rounds, seats and first mover. Other evaluations show no trials at all. No
  seed, secret, key or set id, URL, config, fingerprint, pool, valuation,
  utility, transcript, summary or provenance is served until no evaluation
  runs. The listing never contains endpoint URLs.
- **Host pinning.** Every `/api/*` request (GET and POST) must name this
  machine in its `Host` header (`127.0.0.1:<port>` or `localhost:<port>`),
  so a DNS-rebinding page can neither read nor start anything. POSTs also
  need same-origin JSON and a body of at most 16 KiB; every POST is answered,
  unexpected errors with a plain 500.
- **HTTP endpoint limits.** `http`/`https` only; no credentials in the URL; a
  valid port; redirects never followed (any non-2xx is a transport failure);
  replies capped at 64 KiB and public messages at 1000 characters. The
  per-turn deadline (default 20 s, max 60 s) bounds the turn. TCP connect
  and every socket read use the remaining time as their timeout; once the
  connection is up, a watchdog shuts the socket at the deadline, so
  slow-drip headers or bodies cannot stretch a turn. DNS resolution and the
  TLS handshake happen before the watchdog is armed: there an asyncio
  backstop ends the turn at deadline + 0.5 s, but the worker thread may stay
  busy until the resolver or handshake returns. Requests run on a dedicated
  8-thread pool that `asyncio.run` never waits for.
- **Reply normalization.** Anything that cannot be read as an action (not
  UTF-8, not JSON, too deeply nested, wrong JSON type, numbers too long to
  parse) is the frozen `malformed_output`; it can never escape the engine as
  an unexpected error.
- **Server-side requests (SSRF).** The server POSTs to the URL a user
  supplies, including loopback addresses (so local agents work). It listens
  on 127.0.0.1 only (hard-coded) and only a same-origin page on this machine
  can start requests, so the operator is the only one who can aim them.
  Before exposing the lab to anyone else, add an outbound allow-list.

## 6. Data model

    Evaluation (sandbox_runs/lab/<id>.json, kind = "agent-lab")
      config:     agents X/Y (sanitized: kind, label, url, timeout), blocks, resource types, strategy, design
      provenance: design, protocol id, code version, prompt hash, dependency versions,
                  environment parameters, contract, key id, set id, max rounds, strategy, agents
      trials[]:   position, block, cell, seats {A: X|Y, B: X|Y}, first_mover,
                  status (finished | aborted), started_at, ended_at, scenario fingerprint,
                  pool, both valuations, best possible total,
                  finished: outcome + Phase-1 metrics + per-agent utility and behavior,
                            public transcript events, call diagnostics, observed model ids
                  aborted:  class (agent_failure | provider_failure | budget_exhausted),
                            agent and seat whose call failed, attributed (true for agent_failure only)
      summary:    blocks (planned, complete, excluded with reasons, not run),
                  attempted / used / aborted counts, aborts by class,
                  per-agent failures (attempted, count, rate, seat-A/seat-B split),
                  complete-block aggregates, cell balance check

Agent identity (X, Y) is tracked separately from seat (A, B). Derived
seeds are never stored; the fingerprint (sha256 of the canonical pool and
both valuations) identifies a scenario without revealing how it was drawn.

## 7. Evaluation design: balanced-4-distinct/v2

### 7.1 Blocks of four hidden scenarios

An evaluation is B blocks (1 to 10). Every block has 4 cells, each with its
own freshly generated scenario:

| Cell | Seat A | Seat B | First mover | Scenario |
|---|---|---|---|---|
| c1 | X | Y | A | s(b, c1) |
| c2 | X | Y | B | s(b, c2) |
| c3 | Y | X | A | s(b, c3) |
| c4 | Y | X | B | s(b, c4) |

Each agent sits in seat A twice and seat B twice per block, and each seat
moves first once. Every scenario is used in exactly one negotiation, so no
agent ever negotiates the same scenario twice. That closes the seat-swap
leak: an agent's memory never holds a valuation that belongs to a later
opponent. The four cells run in a keyed random order, recorded as
`position`, so later positions are not tied to one cell.

All scenarios are independent draws from the same Phase-1 generator
distribution (same K, quantity range and Dirichlet(1); roles are treated
symmetrically), so the four cells face equal difficulty in distribution.
They are not identical scenarios: variance is higher than a design that
replays one scenario, which is the price of closing the leak. The report
shows each cell's mean best possible total as a balance check.

Lorè & Heydari (2024) is the reason repetition is the default unit: LLM
behaviour is stochastic, so one negotiation is one sample, not a
characterization. We adopt the principle (repeat, record the action
actually taken, aggregate, report distributions), not their game set,
trial count, temperature or regression model.

### 7.2 Secret-keyed scenarios

    seed(b, c)  = first 8 bytes of HMAC-SHA256(LAB_SCENARIO_SECRET, "agent-lab/v2|<set_id>|scenario|<b>|<c>")
    order(b)    = random.Random(first 8 bytes of HMAC-SHA256(secret, "agent-lab/v2|<set_id>|order|<b>")).shuffle(cells)

The 64-bit seed is passed unchanged to the Phase-1
`generate_resource_pool(seed, K, names)` and `generate_valuation(seed, role, pool)`;
no Phase-1 code changes. An agent that knows the generators and sees its
pool and own valuation cannot recover the seed (2^64 candidates, keyed by a
secret it does not have), and without the seed the opponent's valuation is
out of reach. The public prior (quantity range, Dirichlet) stays known,
legitimately.

Configuration: add `LAB_SCENARIO_SECRET` to `.env` (at least 64 hex
characters, e.g. `python -c "import secrets; print(secrets.token_hex(32))"`).
`.env` is already gitignored; `.env.example` is frozen with Phase 1, so the
setting is documented here. Without it, or with an invalid value,
evaluations refuse to start and the UI shows the configuration error.

Reproducibility: every evaluation gets a random `set_id`; `(secret, set_id,
K)` regenerates exactly the same scenarios and cell orders, and the stored
fingerprints verify the match. Reports store `set_id` and a one-way `key_id`
(the first 12 hex characters of SHA-256 over `"key-id|"` + key), never seeds. Completed trials keep
their pool and valuations, so a report stands on its own without the
secret. There is deliberately no way to re-run a scenario set: that would
show the same scenarios to an agent again (§11).

### 7.3 Failures and complete blocks

| Class | Cause | Attributed to an agent |
|---|---|---|
| `agent_failure` | an HTTP agent's endpoint did not reply: network error, timeout, non-2xx | yes, the agent whose turn it was |
| `provider_failure` | Claude or OpenAI transport failure | no (server-side infrastructure) |
| `budget_exhausted` | the $2 provider cost cap was reached | no; the evaluation stops |

A failure is never a negotiation outcome: it is not a walk-away, a timeout
or "no agreement", because a transport failure does not establish strategic
intent. A failed cell is never re-run (that would show its scenario to the
other agent again). After 3 aborts, or at the cost cap, the evaluation
stops; unstarted blocks are "not run".

A block is complete only if all 4 cells finished. **Every aggregate uses
complete blocks only**, so each agent keeps equal seat and first-move
exposure. With no complete block, no aggregate is reported. Failures are
counted over every attempted negotiation: per agent, its count, rate and
seat-A/seat-B split, shown next to the aggregates with a warning whenever a
block was excluded. An agent that fails only in one seat therefore leaves
every block incomplete and gains nothing.

A robustness analysis that treats failures as unsuccessful outcomes would
be a separate, explicitly labelled analysis; it is not part of the primary
metrics and is not implemented.

## 8. Evaluation framework

### 8.1 Layer 1: outcomes (Phase-1 definitions, evaluator ground truth)

Agreement, walk-away, timeout and invalid-action rates; rounds; utilities;
total welfare; relative welfare efficiency; egalitarian welfare;
equitability; envy-freeness. All are computed by the Phase-1 functions from
the evaluator's knowledge of both valuations and the optimum, never by a
model. Efficiency and fairness are reported separately (Bertsimas, Farias &
Trichakis, 2011: maximizing total utility and maximizing fairness can pick
different allocations), and nothing is folded into a single "cooperation
score". The Price of Fairness itself is not computed: the lab has no
fairness-constrained allocation policy for it to be defined against.

Per-agent attribution: an invalid action and a walk-away are attributed to
the agent that took that turn (derived from the transcript and the frozen
alternation rule).

### 8.2 Layer 2: behavior (transcript-derived, `lab/metrics.py`)

All values are normalized utilities in [0, 1]: points by the agent's own
private valuation divided by 100 (every agent's maximum). They are
computed by the evaluator after the negotiation, from the stored
transcript and the agent's own valuation.

| Metric | Measures | Calculation | Needs | Level | Limitations |
|---|---|---|---|---|---|
| Opening demand | how much the agent asks for first | u_own(first offer by the agent) / 100 | >= 1 offer by the agent | behavioral | undefined for an agent that never offers; for the second mover it is a counter-offer made after seeing an offer |
| Total concession | how far the agent's own offers moved toward the opponent | (u_own(first own offer) - u_own(last own offer)) / 100; negative = hardened | >= 2 offers | behavioral | includes a final-round offer; ignores the opponent's offers; one large step and many small ones look alike |
| Concession frequency | how often it conceded | #{i : u_own(o_{i+1}) < u_own(o_i)} / (k - 1) over its k offers | >= 2 offers | behavioral | direction only, not size |
| Accepted value | what the agent settled for when it accepted | u_own(accepted offer) / 100 | the agent accepted | behavioral | observed only when that agent accepts; not a reservation value |

The table is the definition; each metric has a hand-calculated test. The
Bayesian-learning negotiation paper (IMECS 2011) motivates looking at
concession over time under a deadline with incomplete information; we
measure concession from the transcript and do **not** build opponent
models: the agents under test are LLMs, not Bayesian learners.

Deferred (need definitions and tests first): response to opponent
concessions, deadline behavior, preference revelation (message text needs
a coder or judge; any LLM judge would be labelled evaluator-model-based,
never ground truth), consistency across repetitions.

### 8.3 Aggregation and statistics

Reported over complete blocks: counts, rates, median and interquartile
range, mean, min and max, with n shown next to every value. With n < 5 the
spread is reported as "insufficient observations". No p-values,
significance labels or confidence intervals: the stored per-trial data
(block, cell, seat, fingerprint) is what a later paired analysis would
need. Phase 1's statistical plan (spec §8.9) is untouched.

## 9. Exploratory vs research separation

| | Agent Lab (exploratory) | Research Mode (controlled) |
|---|---|---|
| Agents | any supported adapter | fixed Claude Sonnet 4.6 and GPT-4.1 |
| Scenarios | hidden, secret-keyed, a fresh set per evaluation | seeds 30000/40000 ranges, K = 3 |
| Storage | `sandbox_runs/lab/` (gitignored) | `results/*.db` |
| Read by research views | never | yes |
| Claims | descriptive measurements only | preregistered hypotheses and tests |

Every lab page and report says "Exploratory", and the research views never
read `sandbox_runs/`.

## 10. What exists now (vertical slice)

- `lab/adapters.py`: `AgentSpec` validation, `HTTPAgent` (whole-turn deadline, reply normalization), observation builder, `build_agent`, connection probe.
- `lab/metrics.py`: the four behavioral metrics above.
- `lab/evaluation.py`: keyed scenarios, the balanced-4-distinct design, failure classes, complete-block summaries, provenance, sealed API views, storage.
- `lab/example_http_agent.py`: a minimal reference endpoint (stdlib).
- `dashboard/server.py`: `/api/lab/*` endpoints, Host pinning for every `/api/*` request.
- `dashboard/static/app.js`: Agent Lab page and evaluation report.
- `tests/test_lab.py`: the regression list in §12.

## 11. What cannot be technically enforced

1. **Same-machine agents.** An agent running as the same OS user can read `.env` (the secret), `sandbox_runs/` and process memory. Only running untrusted agents on another host prevents this; the UI says so.
2. **Opponent-behaviour learning.** Each agent meets the same opponent in every negotiation, so an adaptive agent can model its policy across negotiations. That is legitimate adaptation, but negotiations are not independent for adaptive agents.
3. **Outcome-selective failure.** An agent that fails whenever it is losing (in any seat) biases the complete-block sample. The attributed failure rate and the exclusion warning make it visible; they cannot remove it.
4. **Collusion.** Two endpoints run by one operator, or endpoints communicating outside the lab, cannot be prevented.
5. **Prompt injection.** Public messages (up to 1000 characters) sent to an LLM opponent are strategic behaviour; the lab does not filter them.
6. **Operator trust.** Whoever holds the secret can reveal every scenario keyed with it.
7. **Future paired comparisons.** Reusing a scenario set across evaluations would re-expose it to any agent present in both; that must be designed when comparisons are built.

## 12. Regression and adversarial tests (`tests/test_lab.py`)

1. Seed recovery from an observation fails (the unkeyed design fell in 0.02 s).
2. Scenarios depend on key, set, block and cell; same inputs reproduce them.
3. The secret and derived seeds never appear in API responses, stored files, logs, errors or observations.
4. `LAB_SCENARIO_SECRET` is mandatory (missing, non-hex, too short); the server explains it; the key id is stable and one-way.
5. Scenarios come from the unchanged Phase-1 generators.
6. The block matrix is exact; every scenario in an evaluation is distinct.
7. A stateful agent never learns an opponent's valuation (the audit's seat-swap exploit); no agent sees a scenario twice.
8. Cell order is a keyed permutation.
9. The four cells draw from one scenario distribution.
10. A mid-run spy reading the lab API learns nothing about any evaluation.
11. The full report is available after completion.
12. `/api/*` GETs reject a foreign Host.
13. Listings never contain endpoint URLs.
14. Nested JSON is `malformed_output`, the evaluation continues, the probe explains it, and the server always answers a POST.
15. NaN, non-UTF-8, wrong JSON types and unparseable integers map to the frozen reasons.
16–18. Slow-drip bodies, slow-drip headers and silent endpoints are cut at the whole-turn deadline (the negotiation included).
19. The deadline holds when every worker is stuck.
20. Redirects are refused and never followed.
21. A seat-selective failing agent yields no aggregate.
22. One failure excludes its block; aggregates equal hand-computed values from the complete blocks.
23. Provider failures are not attributed to an agent.
24. A cost-cap stop leaves later blocks not run.
25. Failures never enter outcome rates.
26. Provenance matches the Phase-1 helpers; fingerprints recompute and reproduce from key and set id.
27. Lab runs leave a real research database, its snapshot and `results/` byte-identical.
28. Exact per-agent walk-away attribution across seats.

## 13. Extension points (next steps, in order)

1. Paired comparison report, designed around §11.7 (which agents may see a scenario set again, and in which seat).
2. Fixed user-defined scenario repeated N times (the sandbox setup as a scenario family).
3. Native Gemini and Ollama adapters behind the same `Agent` contract.
4. Report export (JSON already exists on disk; add CSV and a printable page).
5. More behavioral metrics, each with a definition, formula and hand-calculated test.
6. Outbound allow-list and per-user credential handling before any multi-user deployment.
