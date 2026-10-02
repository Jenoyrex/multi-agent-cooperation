# Multi-Agent Cooperation: Resource-Split Negotiation Game

**Status:** the Phase 1 experiment harness is implemented: a frozen
negotiation engine, the preregistered experimental design and metrics
(`docs/spec.md` §8), a pilot driver with a budget guard, and a read-only
results dashboard. A separate Phase 2 tool, the Agent Lab, exists as a
vertical slice (`lab/`, see [below](#phase-2-agent-lab)). **No
experimental results are reported in this repository.**

## At a glance

- **What it is:** a controlled environment in which two LLM agents with
  hidden valuations negotiate how to split a pool of resources, plus the
  metrics and tooling to compare negotiation strategies.
- **Why it exists:** to test whether a structured negotiation instruction
  strategy (`structured_v1`) changes welfare, fairness and agreement rates
  compared with plain prompting (`baseline_v1`); see `docs/spec.md` §8.
- **Key technical decisions:**
  - Every negotiation instance is derived from a single integer seed, and
    every run is logged to SQLite with its full transcript and both hidden
    valuations.
  - Metrics are computed by the evaluator against a computed optimum,
    never self-reported by agents; their definitions and the analysis plan
    are preregistered in `docs/spec.md` §8.
  - Agents only ever receive an `AgentView` (the privacy boundary); the
    Claude and OpenAI agents share one prompt builder.
  - Real-model runs are cost-gated: `smoke` and `pilot` modes are capped,
    and a full run requires explicit confirmation after a cost estimate.
- **Try it:** the test suite and the mock-agent smoke test need no API
  keys; see [Running it](#running-it).

## Team

College group project by:

- [@Jenoyrex](https://github.com/Jenoyrex) 
- [@sujathaa-m](https://github.com/sujathaa-m)
- [@4mh24cd002-stack](https://github.com/4mh24cd002-stack)

## Problem statement

As LLM agents increasingly negotiate and coordinate on our behalf (see
recent work like MAGRPO on cooperative multi-agent RL for LLMs, and
DeepMind's Melting Pot benchmark suite for multi-agent cooperation), a
basic open question is: **when two LLM agents with different, hidden
preferences must split a shared resource, does giving them a structured
cooperation protocol actually produce better outcomes than plain
prompting — and along which dimensions (total value created vs. how
fairly it's split vs. how often they even reach agreement)?**

## Research question

Does a structured negotiation protocol improve social welfare and/or
fairness in LLM-agent resource-split negotiations, compared to a
plain-prompting baseline, without one agent systematically exploiting
the other?

## Environment

Two agents negotiate over a small pool of resource categories (e.g.
widgets, gadgets, components), each with a private, randomly-generated
valuation over those categories (see `docs/spec.md` §1-2 for exact
generation). Agents alternate structured offers; agreement requires
explicit acceptance; a round limit forces resolution one way or another.
Full formal spec: [`docs/spec.md`](docs/spec.md).

## Negotiation protocol

Alternating offers, `ACCEPT` / `OFFER` / `WALK_AWAY`, fixed round limit,
disagreement point = zero utility for both agents. Full detail in
`docs/spec.md` §3.

## Agent architecture

A single `Agent` interface (`src/agents/base.py`) that the negotiation
protocol talks to — it has no model-specific logic anywhere. Three
implementations exist:

- `MockAgent` — deterministic, zero-cost, used only to verify the harness.
- `ClaudeAgent` — wraps the Anthropic API with structured tool-use output.
- `OpenAIAgent` — wraps the OpenAI API with structured JSON-schema output.

Both real-model agents are built from the same shared prompt-construction
code (`src/agents/prompting.py`) so a result difference between them can't
be explained by one getting a differently-worded prompt.

## Metrics

Social welfare, social welfare efficiency (vs. a computed ground-truth
optimum), equitability (fairness), envy-freeness, agreement/timeout rates,
and cross-model imbalance. Precise, reproducible definitions in
`docs/spec.md` §5 — none of these are computed or self-reported by the
agents themselves.

## Baseline method (implemented)

**Plain prompting**: agents get the rules and their own private valuation,
nothing else — no explicit instruction to cooperate, no shared scratchpad,
no fairness framing. This is the `baseline_v1` condition that the
`structured_v1` instruction strategy is compared against
(`src/agents/prompting.py`, `docs/spec.md` §8.2). Both conditions use the
same engine and protocol; `structured_v1` only appends a fixed block to
the system instructions.

## Reproducibility

Every negotiation instance is derived from a single integer seed:
resource pool, both valuations, and who moves first are all deterministic
functions of that seed (`src/environment/`). Every run is logged to
SQLite with the full transcript, both hidden valuations, and every
computed metric (`src/storage/db.py`). Model *behavior* is inherently
non-deterministic and it would be dishonest to claim otherwise — what's
reproducible is the environment and experimental configuration, not the
model's specific responses on a re-run.

## Cost controls

Three explicit modes (`src/experiments/config.py`): `smoke` (≤5
negotiations, mock or real), `pilot` (≤20), `full` (uncapped, but requires
`run_batch(..., confirmed_full_run=True)` after the cost estimate has
been printed — calling it without that flag prints the estimate and stops,
it does not run anything). See `docs/spec.md` for the full policy.

## Running it

```bash
pip install -e ".[dev]"

# Zero-cost harness verification (no keys needed):
PYTHONPATH=. python -m pytest -q
PYTHONPATH=. python scripts/run_smoke_test.py

# Real-model smoke test (3 negotiations, real API cost — needs keys):
cp .env.example .env   # then fill in your keys
PYTHONPATH=. python scripts/run_real_smoke_test.py
```

## Dashboard (read-only)

```bash
PYTHONPATH=. python -m dashboard.server        # http://127.0.0.1:8765
node --test tests/dashboard_lib.test.mjs        # UI helper tests
```

Stdlib server + static page in `dashboard/`. It reads `results/*.db` in
SQLite read-only mode, reuses `src/evaluation/metrics.py` for every number,
and never starts or changes a run. Full-run results stay locked until all
480 negotiations exist (spec §8.9). No inferential statistics are shown,
because the §8.9 analysis has not been implemented yet.

## Phase 2: Agent Lab

A separate tool built on the frozen Phase 1 engine: a third party can
connect two agents (including their own, over HTTP), run them through the
same controlled negotiation environment under a balanced design, and read
evaluator-computed outcome and behavior measurements. It reports
measurements only and never ranks agents. It does not use or modify the
Phase 1 preregistration, runner or research databases.

Implemented as a vertical slice: `lab/` (agent adapters, evaluation
design, behavioral metrics, a reference HTTP agent), `/api/lab/*`
endpoints in `dashboard/server.py`, and an Agent Lab page in the
dashboard. Design, security model and known limits:
[`docs/phase2-agent-lab-plan.md`](docs/phase2-agent-lab-plan.md).

## Limitations

- Valuations are linear/separable, which makes the welfare-maximizing
  allocation always "winner take all per category" — efficiency and
  fairness are expected to trade off by construction. See `docs/spec.md`
  §1.3 for why this was chosen anyway for the MVP, and the documented
  extension (concave utilities) if this makes results uninteresting.
- Two agents only; one negotiation protocol. The two experimental
  conditions (`baseline_v1`, `structured_v1`) differ only in system
  instructions.
- No experimental results are reported here. Every number in this repo
  comes from either hand-computed test fixtures or the mock-agent
  plumbing test, both explicitly labeled as such.

## Project structure

```
multi-agent-cooperation/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
│   ├── spec.md              # formal specification + §8 preregistration
│   └── phase2-agent-lab-plan.md  # Agent Lab design and security model
├── src/
│   ├── agents/               # Agent interface + mock/Claude/OpenAI implementations
│   ├── environment/           # resource pool, valuations, allocation validation, optimum
│   ├── negotiation/            # protocol engine (privacy boundary lives here)
│   ├── evaluation/              # metrics
│   ├── experiments/              # config, cost gating, batch runner, pilot
│   └── storage/                   # SQLite persistence
├── lab/                            # Phase 2 Agent Lab
├── dashboard/                      # read-only results dashboard + Agent Lab UI
├── tests/                          # 344 pytest tests + 10 Node dashboard tests
├── scripts/
│   ├── run_smoke_test.py            # mock agents, zero cost
│   ├── run_real_smoke_test.py        # real models, capped at 3 negotiations
│   └── run_pilot.py                  # preregistered pilot (§8.4), real API cost
└── results/                           # SQLite DBs land here (gitignored)
```
