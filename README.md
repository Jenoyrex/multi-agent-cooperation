# Multi-Agent Cooperation: Resource-Split Negotiation Game

**Status: Phase 0/1 complete. No experiments have been run. This repo
currently contains the formal spec, the harness, and a zero-cost
plumbing test — not results.**

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
no fairness framing. This is what later structured-protocol methods
(Phase 2, not yet built) will be compared against.

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

## Limitations (Phase 0/1)

- Valuations are linear/separable, which makes the welfare-maximizing
  allocation always "winner take all per category" — efficiency and
  fairness are expected to trade off by construction. See `docs/spec.md`
  §1.3 for why this was chosen anyway for the MVP, and the documented
  extension (concave utilities) if this makes results uninteresting.
- Two agents only; no more than one structured protocol variant exists
  yet (baseline only — Phase 2 not started).
- No experiments have been run. Every number in this repo so far comes
  from either hand-computed test fixtures or the mock-agent plumbing
  test, both explicitly labeled as such.

## Project structure

```
multi-agent-cooperation/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
│   └── spec.md              # Phase 0 formal specification
├── src/
│   ├── agents/               # Agent interface + mock/Claude/OpenAI implementations
│   ├── environment/           # resource pool, valuations, allocation validation, optimum
│   ├── negotiation/            # protocol engine (privacy boundary lives here)
│   ├── evaluation/              # metrics
│   ├── experiments/              # config, cost gating, batch runner
│   └── storage/                   # SQLite persistence
├── tests/                          # 43 tests, see below
├── scripts/
│   ├── run_smoke_test.py            # mock agents, zero cost
│   └── run_real_smoke_test.py        # real models, capped at 3 negotiations
└── results/                           # SQLite DBs land here (gitignored)
```
