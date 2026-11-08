# Multi-Agent Cooperation: Resource-Split Negotiation Game

**Live demo: [Open Cooperation Lab](https://cooperation-lab.onrender.com/)**, hosted on Render's free tier.
The demo runs **rule-based demo agents only** and keeps its data in a **temporary, shared** workspace: everyone
sees what others create, and everything is wiped when the service restarts or idles. The first visit after
idling can take up to a minute while the service wakes.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Jenoyrex/multi-agent-cooperation)

> **Demo output is not research data.** Every experiment in the public demo uses rule-based agents and is
> labelled **simulated**: it shows how the lab measures negotiation behaviour, not how any AI model behaves.
> No AI provider is called anywhere in the demo, and the project spends ₹0. Real-model results would come only
> from the preregistered study (Claude vs GPT), which has not been run. No such results exist in this repository.

## In plain words

Two AI agents have to share a pile of resources. Each one privately knows how much each kind of resource is
worth to *it*, but not to the other. They take turns making offers until one accepts, one walks away, or time
runs out. We want to know whether giving both agents a short, fixed set of negotiation steps (say what matters
to you, trade what you value less for what you value more, remember that no deal means zero for both) changes
how much total value they create, how well the worse-off agent does, and how often they agree at all, compared
with giving them only the rules.

This repository has two separate parts:

1. **The research study**: a frozen, preregistered experiment with real Claude and GPT models. Its data lives
   in `results/` and is shown read-only in the dashboard. It has **not been run yet**.
2. **Cooperation Lab (the Workbench)**: an exploratory, zero-cost tool for configuring agents and resources,
   running rule-based or local agents, and exploring the analytics. Its data lives in `sandbox_runs/` and is
   never mixed with research data. The public demo is this part.

## Status: no results yet

- The harness is implemented: the frozen negotiation engine, the preregistered design, metrics and analysis
  plan (`docs/spec.md` §8), the analysis code (`src/evaluation/inference.py`), a cost-gated pilot driver, the
  dashboard and the Workbench.
- **No real-model pilot or full run has been carried out.** H1–H3 are untested; there are no effect sizes,
  intervals or p-values.
- Every number in the repository comes from hand-computed test fixtures, the mock-agent plumbing test, or
  rule-based simulations, each labelled as such. None of it is evidence about Claude, GPT or any other model.

## At a glance

- **What it is:** a controlled environment in which two LLM agents with hidden valuations negotiate how to
  split a pool of resources, plus the metrics and tooling to compare negotiation strategies.
- **Why it exists:** to test whether a structured negotiation instruction strategy (`structured_v1`) changes
  welfare, fairness and agreement rates compared with plain prompting (`baseline_v1`); see `docs/spec.md` §8.
- **Key technical decisions:**
  - Every negotiation instance is derived from a single integer seed, and every run is logged to SQLite with
    its full transcript and both hidden valuations.
  - Metrics are computed by the evaluator against a computed optimum, never self-reported by agents; their
    definitions and the analysis plan are preregistered in `docs/spec.md` §8.
  - Agents only ever receive an `AgentView` (the privacy boundary); the Claude and OpenAI agents share one
    prompt builder.
  - Real-model runs are cost-gated: `smoke` and `pilot` modes are capped, and a full run requires explicit
    confirmation after a cost estimate.
- **Try it:** the test suite and the mock-agent smoke test need no API keys; see [Running it](#running-it).

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
preferences must split a shared resource, does giving them structured
negotiation instructions actually produce better outcomes than plain
prompting — and along which dimensions (total value created vs. how
fairly it's split vs. how often they even reach agreement)?**

## Research question

When two LLM agents (one Claude model, one GPT model) with private linear
valuations negotiate a split of a shared resource pool under the frozen
alternating-offers protocol, does the `structured_v1` negotiation
instruction strategy change relative welfare efficiency, egalitarian
welfare, or agreement rate compared with the `baseline_v1` instructions?
(`docs/spec.md` §8.1; hypotheses H1–H3 are non-directional, §8.8.)

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

## Experimental conditions

**`baseline_v1` (plain prompting)**: agents get the rules and their own
private valuation, nothing else — no explicit instruction to cooperate, no
shared scratchpad, no fairness framing.

**`structured_v1`**: the same system instructions followed by one fixed
block with three steps, delivered together as a package: (1) state which
categories matter most and least and ask for the other agent's ranking,
(2) build offers that trade what you value less for what you value more,
(3) before walking away or letting the deadline pass, compare the offer on
the table with the zero both agents get without a deal. It contains **no
fairness instruction**.

Both conditions use the same engine and protocol; `structured_v1` only
appends a fixed block to the system instructions
(`src/agents/prompting.py`, `docs/spec.md` §8.2). Any difference can only
be attributed to the package as a whole, not to one step.

## Experimental design: why 480 negotiations

The full run uses **60 instances** (seeds 40000–40059). An instance fixes
the resource pool and both private valuations. Every instance is run in
every combination of three two-way factors:

| Factor | Levels |
|---|---|
| Model seat | Claude as Agent A / GPT as Agent A |
| First mover | Agent A opens / Agent B opens |
| Condition | `baseline_v1` / `structured_v1` |

So each instance gets 2 × 2 × 2 = 8 negotiations, and
**60 × 2 × 2 × 2 = 480** in total. Seat and first-mover swaps cancel out
seat and opening advantages; the instance (n = 60) is the unit of
analysis, comparing each instance's 4 structured negotiations with its 4
baseline ones (`docs/spec.md` §8.3, §8.9). A separate 24-negotiation pilot
(seeds 30000–30002) is an operational check only and is never part of the
tested data (§8.4).

## Metrics

Confirmatory (tested, H1–H3): relative welfare efficiency, egalitarian
welfare and agreement rate, all unconditional (failures score 0).
Secondary and diagnostic (descriptive only): equitability and conditional
welfare efficiency on agreed negotiations, envy, model imbalance,
first-mover effects and outcome rates. Precise, reproducible definitions in
`docs/spec.md` §5 and §8.6 — none of these are computed or self-reported by
the agents themselves.

## Reproducibility

Every negotiation instance is derived from a single integer seed: the
resource pool and both valuations are deterministic functions of that seed
(`src/environment/`, with per-agent seeds from a stable SHA-256 derivation).
Who moves first comes only from the configured first-mover policy and the
negotiation's position in the batch, never from the seed. Every run is
logged to SQLite with the full transcript, both hidden valuations, every
computed metric and its provenance (`src/storage/db.py`). Model *behavior*
is inherently non-deterministic and it would be dishonest to claim
otherwise — what's reproducible is the environment and experimental
configuration, not the model's specific responses on a re-run.

## Cost controls

Three explicit modes (`src/experiments/config.py`): `smoke` (≤5
negotiations, mock or real), `pilot` (≤20 per batch), `full` (uncapped, but
requires `run_batch(..., confirmed_full_run=True)` after the cost estimate
has been printed — calling it without that flag prints the estimate and
stops, it does not run anything). Paid provider calls are also refused
unless `ALLOW_PAID_PROVIDER_CALLS` is set. See `docs/spec.md` for the full
policy.

## Running it

Python 3.10+. On Windows, use `py` where `python` is the Microsoft Store stub.

```bash
pip install -e ".[dev]"

# Zero-cost harness verification (no keys needed):
PYTHONPATH=. python -m pytest -q
PYTHONPATH=. python scripts/run_smoke_test.py

# Dashboard and Workbench, then open http://127.0.0.1:8765
PYTHONPATH=. python -m dashboard.server
node --test tests/dashboard_lib.test.mjs        # UI helper tests

# Zero-cost end-to-end Workbench demo:
PYTHONPATH=. python scripts/run_workbench_demo.py

# Real-model smoke test (3 negotiations, real API cost — needs keys):
cp .env.example .env   # then fill in your keys
PYTHONPATH=. python scripts/run_real_smoke_test.py
```

To run only the dashboard, `pip install "pydantic>=2.6" "python-dotenv>=1.0"` is enough.

## Dashboard: research views (read-only)

The research pages read `results/*.db` in SQLite read-only mode, reuse
`src/evaluation/metrics.py` for every number, and never start or change a
run. Full-run results stay locked until all 480 negotiations exist
(spec §8.9). The preregistered tests (sign-flip permutation, bootstrap
interval, Wilcoxon sensitivity, Holm correction) are implemented in
`src/evaluation/inference.py` and are shown only once the complete full
run exists; the pilot is never tested. `scripts/run_analysis.py --db
<full-run db>` runs the same analysis and refuses anything but the
complete full run.

## Workbench (exploratory, zero cost)

The dashboard opens on **Start**, a guided path through the **Workspace**:
Agents, Resources, Experiments, Analytics and Reports.

- Agents: built-in rule-based strategies with adjustable bargaining;
  Claude, GPT and Gemini configurations (saved but locked, never called);
  upload your own Python agent file, which is **checked by reading its
  code, never executed**; or, on a local server, your own HTTP agent.
- Resource builder with quantities, units, categories and each agent's
  private values.
- Five-step experiment wizard on the unchanged engine, live monitoring,
  and interactive analytics with plain-language findings that state their
  basis.
- Cross-experiment analytics, a two-experiment comparison (labelled
  exploratory), printable reports (save as PDF), and CSV / JSON / Markdown
  exports.
- Light, dark and system themes; works on phones.

Rule-based runs are labelled **simulated**, runs with your local HTTP
agents are labelled **local agents**, and both are stored in
`sandbox_runs/`, never `results/`. See [docs/workbench.md](docs/workbench.md).

**Agent Lab** (`lab/`, `/api/lab/*`): connect two agents, run them through
the same controlled negotiation environment under a balanced design, and
read evaluator-computed outcome and behaviour measurements. It reports
measurements only and never ranks agents, and it does not use or modify
the research preregistration, runner or databases. Design, security model
and known limits: [`docs/phase2-agent-lab-plan.md`](docs/phase2-agent-lab-plan.md).

**Stack:** Python standard-library HTTP server (`dashboard/server.py`) plus
`pydantic`; vanilla JavaScript with no build step; SVG charts; SQLite;
Motion (vendored) for animation; Inter and JetBrains Mono fonts (SIL OFL,
self-hosted).

## Public demo mode

```bash
PUBLIC_DEMO=1 LAB_SCENARIO_SECRET=<64 hex characters> PYTHONPATH=. python -m dashboard.server --host 0.0.0.0
```

`LAB_SCENARIO_SECRET` is needed only for Agent Lab evaluations. `render.yaml`
does not set it, so the hosted demo reports Agent Lab as unavailable unless
it is added in Render's dashboard.

`render.yaml` describes the free Render web service that hosts the live
demo: two pip packages, `python -m dashboard.server --host 0.0.0.0` on
Render's `$PORT`, health check `/healthz`, `PUBLIC_DEMO=1` and
`ALLOW_PAID_PROVIDER_CALLS=0`. In this mode the site is a **shared
presentation demo**:

- **Not an AI evaluation.** Only scripted, rule-based agents run; no AI
  provider is called and no API key is used. Scripted agents follow fixed
  rules and ignore the instructions, so their numbers show how the lab
  measures a negotiation, not how any model negotiates.
- **Not persistent.** Render's free tier wipes the disk on every restart,
  deploy or idle spin-down (after about 15 minutes without traffic), and
  the oldest entries are pruned.
- **Not private between visitors.** Everything visitors create is visible
  to all visitors and cannot be edited or deleted.
- **Not a benchmark.** Results are descriptive; no significance test is
  run and no agent is ranked.

The server enforces this itself, whatever the page sends
(`dashboard/demo.py`, `workbench/api.py`): scripted agents only, local HTTP agents off,
experiments capped at 48 negotiations, rate-limited run starts, and no
technical error detail in responses. The research views are unchanged and
still show only real experiment data (none has been collected yet).

## Limitations

- **Linear valuations.** The welfare-maximizing allocation is always
  "winner take all per category", so efficiency and fairness can diverge
  by construction. See `docs/spec.md` §1.3 for why this was chosen anyway;
  concave utilities are a possible later experiment, not part of the
  preregistration (§8.13).
- **Bundled intervention.** `structured_v1` is one three-step package; the
  design cannot isolate the effect of any single step.
- **One model pairing, two agents.** One fixed Claude model and one fixed
  GPT model; results may not generalize to other models or to more agents.
- **Different output mechanisms.** Claude answers through a forced tool
  call and GPT through strict JSON-schema output, which may affect
  malformed-output rates.
- **Stochastic models and drift.** Outputs are not deterministic, and a
  provider can change a model behind the same identifier.
- **Selection in agreement-only metrics.** Equitability and conditional
  welfare efficiency are computed on agreed negotiations only, so they are
  descriptive, not treatment effects.
- **Correlated outcomes.** The three confirmatory metrics all depend on
  agreement and may be strongly correlated.
- **Demo is not evidence.** Workbench and public-demo results come from
  rule-based or local agents and say nothing about Claude, GPT or Gemini.

Full list: `docs/spec.md` §8.12.

## Project structure

```
multi-agent-cooperation/
├── README.md
├── PRODUCT.md                     # UI/product direction
├── pyproject.toml
├── render.yaml                    # free Render service for the public demo
├── .env.example
├── docs/
│   ├── spec.md                    # formal specification + §8 preregistration
│   ├── workbench.md               # Workbench guide
│   └── phase2-agent-lab-plan.md   # Agent Lab design and security model
├── src/
│   ├── agents/                    # Agent interface, mock/Claude/OpenAI agents, shared prompts
│   ├── environment/               # resource pool, valuations, allocation validation, optimum
│   ├── negotiation/               # protocol engine (privacy boundary lives here)
│   ├── evaluation/                # metrics and the §8.9 inference
│   ├── experiments/               # config, cost gating, batch runner, pilot, provenance
│   └── storage/                   # SQLite persistence
├── workbench/                     # Workbench agents, environments, simulations, analytics, exports
├── lab/                           # Agent Lab
├── dashboard/                     # stdlib server + static UI (research views, Workbench, Agent Lab)
├── tests/                         # pytest suite + Node tests for the UI helpers
├── scripts/                       # smoke tests, pilot, analysis, Workbench demo, offline simulation
├── artifacts/offline_simulation/  # labelled SIMULATED offline outputs
├── results/                       # research SQLite DBs (gitignored)
└── sandbox_runs/                  # Workbench data (gitignored)
```
