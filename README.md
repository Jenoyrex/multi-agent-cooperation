# Multi-Agent Cooperation: Resource-Split Negotiation Game

## Install and run on your own computer

Needs Python 3.10 or newer. Works the same on Windows, macOS and Linux.

```bash
python -m venv .venv
# Windows:        .venv\\Scripts\\activate
# macOS / Linux:  source .venv/bin/activate
pip install git+https://github.com/Jenoyrex/multi-agent-cooperation
cooperation-lab
```

The app starts on `http://127.0.0.1` and opens your browser. Options: `--port 9000`, `--no-browser`, and `--host` (only if
you really want other machines to reach it). Your data stays in `~/.cooperation-lab`, or wherever `COOPERATION_LAB_HOME` points.

**Free local models (optional).** Install one, and the app finds it on this computer:

- [Ollama](https://ollama.com): after installing, run `ollama pull llama3.2`. Default address `http://localhost:11434/v1`.
- [LM Studio](https://lmstudio.ai): load a model and start its local server. Default address `http://localhost:1234/v1`.

## Live demo

**Live demo: _not deployed yet_.** The link is added here once the Render service is live and has been checked.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Jenoyrex/multi-agent-cooperation)

**Cooperation Lab** is an experimental research platform for multi-agent negotiation. You configure agents,
define the resources they divide and how much each agent privately values them, run experiments on a
fixed negotiation engine, watch them live, and explore automatically computed behavioural analytics.
Alongside it sits a preregistered research study comparing Claude and GPT, shown read-only.

> **Simulated vs. real-model results.** Every experiment in the public demo uses rule-based agents and is
> labelled **simulated**: it shows how the lab measures negotiation behaviour, not how any AI model behaves.
> No AI provider is called anywhere in the demo, and the project spends ₹0. Real-model results would come only
> from the preregistered study (Claude vs GPT), which has not been run. No such results exist in this repository.

**Features**
- Guided start page: add agents → define resources → run an experiment → explore results → export.
- Agents: built-in rule-based strategies with adjustable bargaining; Claude, GPT and Gemini configurations (saved but
  locked, never called); upload your own Python agent file. It is **checked by reading its code, never executed**.
- Resource builder with quantities, units, categories and each agent's private values.
- Five-step experiment wizard, live monitoring, and interactive analytics: outcome mix, value captured, who got
  what, turn-by-turn timelines, condition → outcome flows, behaviour profiles, concession curves, value vs. ask
  heatmaps, plus plain-language findings that state their basis.
- Cross-experiment analytics, a two-experiment comparison (labelled exploratory), printable reports (save as PDF),
  and CSV / JSON / Markdown exports.
- Light, dark and system themes; works on phones.

**Stack:** Python standard-library HTTP server (`dashboard/server.py`) plus `pydantic`; vanilla JavaScript with
no build step; SVG charts; SQLite for local storage; Motion (vendored) for animation; Inter and JetBrains Mono
fonts (SIL OFL, self-hosted).

**Run it locally** (Python 3.10+):

```bash
pip install "pydantic>=2.6" "python-dotenv>=1.0"   # or: pip install -e ".[dev]" for tests and the research tools
python -m dashboard.server                          # then open http://127.0.0.1:8765
```

On Windows, use `py` where `python` is the Microsoft Store stub. Local runs keep everything on your machine:
Workbench data lives in `sandbox_runs/workbench.db`, separate from research data in `results/`.

**Deployment.** `render.yaml` describes a free Render web service: no build step beyond two pip packages,
`python -m dashboard.server --host 0.0.0.0` on Render's `$PORT`, health check `/healthz`, and `PUBLIC_DEMO=1`.
In that mode the site is a **shared presentation demo**:
- everything visitors create is visible to all visitors and cannot be edited or deleted;
- local HTTP agents are off;
- experiments are capped at 48 negotiations, and run starts are rate-limited;
- error responses carry no technical detail.

Render's free tier wipes the disk on every restart, deploy or idle spin-down (after about 15 minutes without
traffic), so **demo data is temporary**. The first visit after idling takes up to a minute while the service wakes.

**Status: Phase 0/1 complete. No experiments have been run. This repo
currently contains the formal spec, the harness, and a zero-cost
plumbing test — not results.**

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

## Workbench (exploratory, zero cost)

The dashboard opens on **Start**, a guided path through the **Workspace**: Agents, Resources, Experiments,
Analytics and Reports. You add agents (built-in strategies, locked AI providers, or an uploaded `.py` file that is
inspected but never executed), define resources, run designed experiments on the unchanged engine through a
step-by-step wizard, follow them live, explore interactive behavioural analytics, compare experiments, and save
reports as PDF or export CSV/JSON. Light, dark and system themes are in **Settings**. Rule-based and Python
agents run locally at no cost; Claude, GPT and Gemini entries are configurable but locked. Rule-based runs are labelled simulated, runs with your local HTTP agents are labelled local agents, and both are stored in `sandbox_runs/`, never `results/`.
See [docs/workbench.md](docs/workbench.md).

Zero-cost end-to-end demo: `PYTHONPATH=. py scripts/run_workbench_demo.py`. The preregistered §8.9 analysis is
`src/evaluation/inference.py` (`scripts/run_analysis.py --db <full-run db>`; it refuses anything but the complete full run).

## Public demo mode

```bash
PUBLIC_DEMO=1 LAB_SCENARIO_SECRET=<64 hex characters> PYTHONPATH=. python -m dashboard.server --host 0.0.0.0
```

What a visitor can do: set up a negotiation between two **scripted,
rule-based agents**, watch it live, and read the evaluator's analysis;
or run the Agent Lab's balanced evaluation (up to 3 blocks, 12
negotiations) on the same scripted agents. What it is not:

- **Not an AI evaluation.** No AI provider is called and no API key is
  used. Scripted agents follow fixed rules and ignore the instructions,
  so their numbers show how the lab measures a negotiation, not how any
  model negotiates. Claude, GPT and your own HTTP agents run only in the
  full lab, on a local server with its own configuration.
- **Not persistent.** Only the newest 100 runs of each kind are kept,
  on the server's local disk; on a host whose disk is wiped on restart
  (such as a free Render instance) they disappear with every restart.
- **Not private between visitors.** Recent runs are listed for everyone.
- **Not a benchmark.** Results are descriptive; no significance test is
  run and no agent is ranked.

The server enforces all of this itself, whatever the page sends: scripted
agents only, no connection probe, at most 20 run starts per minute for
the whole server, and no technical error detail in responses
(`dashboard/demo.py`). The research views are unchanged and still show
only real experiment data (none has been collected yet).

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
