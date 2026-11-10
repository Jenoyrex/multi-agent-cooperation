# Workbench

An exploratory layer for running your own two-agent negotiations on the frozen engine.
Open it from the dashboard (`PYTHONPATH=. py -m dashboard.server`, then **Workbench** in the
sidebar). It never reads or writes the research database in `results/`. Its data lives in
`sandbox_runs/workbench.db` (gitignored).

**Zero cost.** Rule-based and Python agents run locally. Claude, GPT and Gemini entries can be saved
and reviewed but cannot run: real provider calls cost money and the project never makes them
(`src/zero_cost.py` refuses them underneath as well). Every simulation is labelled by what actually
ran. Rule-based results are **simulated** and say nothing about any LLM.

## Pages

The UI calls environments **Resources** and simulations **Experiments**; the API and stored data keep their old names.

| Page | What it does |
|---|---|
| Start `#/` | Guided five-step journey with real completion state, a one-click sample experiment, recent experiments |
| Agents `#/agents` | Grouped list; `#/agents/new` offers three paths: built-in strategy, AI provider (saved, locked), or a Python file upload (inspected, never executed) |
| Resources `#/environments` | Builder with quantities, units, categories, private points (sliders, balance to 100), a who-values-what view, import, live server preview |
| Experiments `#/simulations` | List; `#/simulations/new/<step>` is a five-step wizard: agents, resources, preferences, settings, review |
| Experiment `#/simulation/<id>` | Live counters and transcript, outcome tape, interactive analytics (Outcomes, Agents, Behaviour, Statistics, Findings), negotiations, log, exports |
| Analytics `#/analytics` | Pooled descriptive results across experiments; `#/analytics/compare` compares two experiments (exploratory) |
| Reports `#/reports`, `#/report/<id>` | Printable report: setup, results, charts, interpretation, limitations, provenance; Save as PDF through the browser's print dialog |
| Settings `#/settings` | Theme (system, light, dark), motion, zero-cost and provider status, where data lives |

The research study's pages moved under **Research study** (`#/study`, `#/experiments` "Data collection", `#/results`, `#/methodology`, `#/configuration`) and are unchanged in substance.

## Uploaded Python agents

`POST /api/workbench/uploads/inspect` takes `{filename, source}` (at most 48 KB of source; this route and agent saves accept bodies up to 128 KB) and returns a report from `workbench/inspect_agent.py`. The file is parsed with `ast` and never imported, compiled or executed. It must contain a class with `async def generate_response(self, view)`; imports that reach the network, files, processes or AI providers, and calls such as `eval`, `exec` or `open`, are flagged. Saving creates a `python_upload` agent whose source and report are stored with it; the server re-inspects on save. It is never runnable here, because the server has no isolated sandbox for untrusted code. To run your own agent, serve it on this machine and add it as a Local HTTP agent. A starter template is served at `templates/my_custom_agent.py`; a test runs it against the engine.

## Resource details

A custom environment may carry `"meta": {"food": {"unit": "crates", "group": "provisions"}}`. These are display labels only: the engine and every metric see the same pool and points without them.

## Agent interface

The engine knows one interface (`src/agents/base.py`):

```python
class Agent:
    name: str
    async def generate_response(self, view: AgentView) -> NegotiationAction: ...
```

`AgentView` holds only what the agent may know: the pool, its own valuation, the public transcript,
the round and the deadline. `NegotiationAction` is `OFFER` (with an allocation for both seats),
`ACCEPT` or `WALK_AWAY`, plus an optional message. The protocol validates every action (spec §3, §8.5).

Agent **kinds** in `workbench/agents.py` are adapters: each declares its parameters (`Param`), a check,
and a `build(name, params, variant) -> Agent`. To add a Python agent, write a class like
`PriorityAgent` and register a `Kind` with `category="python"`. To add a provider, register a kind with
`executable=False` until a separately authorised integration exists. The engine never changes.
Uploaded Python is never executed by the server.

| Kind | Runs | Notes |
|---|---|---|
| `concession` | yes, free | `MockAgent`: uniform claim, fixed concession, threshold accept |
| `priority` | yes, free | `PriorityAgent`: claims what it values most, concedes, accepts at the deadline |
| `http_local` | yes, loopback only | Your program behind `http://127.0.0.1…`, Agent Lab protocol; no token stored |
| `python_upload` | **never** | Your uploaded file, inspected only (see below) |
| `local_llm` | yes, loopback only | An open model on this computer through Ollama (loopback only); off in the public demo |
| `claude`, `openai` | **locked** | Adapters exist; zero-cost mode refuses them |
| `gemini` | **not implemented** | No adapter; the entry records a configuration only |

## Scenario format

JSON (same shape the builder saves):

```json
{"name": "Field hospital", "max_rounds": 10, "first_mover": "A", "source": "custom",
 "resources": {"food": 20, "water": 12, "medicine": 6},
 "points": {"A": {"food": 50, "water": 30, "medicine": 20},
            "B": {"food": 10, "water": 30, "medicine": 60}}}
```

Text:

```
name: Field hospital
rounds: 10
first: A
resources: food=20, water=12, medicine=6
A: food=50, water=30, medicine=20
B: food=10, water=30, medicine=60
```

Rules: 2–6 resources (lowercase names), quantities 1–1000, each agent's points are whole numbers
summing to exactly 100, rounds 2–20. A resource's per-unit value is points / quantity, as in the
research environment. `"source": "generated"` with `"categories": 2–5` uses the research generator,
which draws a new instance per seed. Errors name the field or line to fix. YAML and Python modules are
not supported.

## Simulations and analytics

A design is repetitions (one seed each) × conditions × optional seat swap × optional opening-seat swap
(at most 400 negotiations). Infrastructure failures are recorded as aborted, not as outcomes. Cancelling
stops before the next turn.

Analytics use the research definitions: RWE and egalitarian welfare score failures as 0; SWE,
equitability and envy-freeness use agreed negotiations and report their n. Per agent: utility,
opening demand, concession, accepts and walk-aways, and resource priorities (private points as ground
truth vs. observed opening claims and final shares). Observations state their basis and link to the
negotiations behind them; motives are never inferred.

## Behavioural measures

All are computed from stored negotiations. Each states its definition and denominator in the UI and in
`analytics.json`. **Ground truth** is the private points an agent was given. **Observed** is what it did.
**Derived** is a calculation over both. Motives are never inferred.

| Measure | Definition | Denominator |
|---|---|---|
| Opening claim per resource | Share of each resource's units claimed in the agent's first offer | negotiations with an opening offer |
| Priority alignment | Mean over opening offers of the share of resource pairs (with different points) where it claimed strictly more of the higher-valued one; ties reported separately | opening offers with unequal points |
| Asks for what it values | Opening claim on the top half of resources by its own points minus the claim on the rest | same |
| Scarcity response | Opening claim share vs. the resource's quantity: tercile bins and a Spearman correlation. **Caveat:** 100 points per resource make scarce resources worth more per unit, so quantity and per-unit value are confounded | (negotiation, resource) pairs |
| Concession curve | Mean value (own points / 100) of the agent's k-th offer | negotiations with ≥ k offers |
| Position | Agreement and own utility (failures 0) when it opened vs. responded, and in seat A vs. B | negotiations in that role |
| By condition | Opening demand, total concession, utility and agreement per condition | negotiations in that condition |

Rule-based agents are deterministic. Differences across seats, openers or instances come from the rules meeting different
situations, not from learning; the UI says so.

## Statistical analysis

`src/evaluation/inference.py` implements the preregistered analysis (spec §8.9, §8.11) in pure Python:

- Unit = instance; per-instance mean per condition; d_i = structured − baseline, only for instances seen in both conditions.
  Unpaired instances are listed, never imputed.
- Two-sided sign-flip permutation test, 10,000 flips, p = (1 + #{|T*| ≥ |T|}) / 10,001.
- 95% percentile bootstrap CI for mean(d), 10,000 resamples of instances (linear interpolation between order statistics).
- Wilcoxon signed-rank sensitivity check, zeros dropped: exact when there are no ties and n ≤ 50, otherwise tie-corrected
  normal approximation without continuity correction. It is not part of the corrected family.
- Holm across the H1–H3 permutation p-values at α = 0.05.
- Failures score 0 for RWE and EW; agreement uses every negotiation. §8.11 sensitivity: invalid actions removed.
- Seed `ANALYSIS_SEED = 20260921`, recorded in the output, with one stream per hypothesis and procedure. The spec fixes the
  procedures but not the seed value or the percentile convention; both were fixed in code before any full-run data existed.

Where it runs:

- **Research dashboard and `scripts/run_analysis.py`:** only on the complete full run: 480 negotiations with every
  seed 40000–40059 × condition × Claude seat × first mover cell exactly once, all between the approved §8.15 snapshots
  with `MAX_ROUNDS` 10 (`full_design_complete` in `dashboard/data.py`). Never on the pilot or partial data (§8.9, no interim analysis). No full run exists, so nothing has been
  computed on research data.
- **Workbench (exploratory):** the same procedures on a simulation with both conditions. A fixed environment is one instance
  however many repetitions ran. Labelled exploratory: it describes those agents on those instances, not any LLM.

`tests/test_inference.py` validates the mathematics on synthetic data with known answers. It covers zero effect,
consistent ± effects, mixed effects and ties, small and empty samples, failures, invalid actions and missing pairs, a Holm
reversal, and nested repeats. It also checks hand-computed toy values, and uses scipy as an independent Wilcoxon oracle in
the tests only. These tests establish correctness of the calculations, not anything about agent behaviour.

## Demo (zero cost, from a clean checkout)

Prerequisites: Python 3.10+ with the project installed (`pip install -e ".[dev]"`); no API keys, no network.

```bash
PYTHONPATH=. py scripts/run_workbench_demo.py            # use `python` on systems where it is not the Store stub
```

It creates two rule-based agents and a generated environment through the validated API, then runs 80 negotiations (both
conditions × 10 seeds × swapped seats × swapped openers). It prints one transcript, the observations and the exploratory
inference, and writes every export to `sandbox_runs/workbench_demo/` (gitignored). It exits 0 only if nothing touched the
network, all 80 negotiations completed, and every paired difference is 0 with no significant test. That is the expected
result by construction: these agents never read instructions. `DEMO_SUMMARY.md`, both CSVs, `analytics.json`,
`transcripts.json` and `environment.json` are byte-identical across runs. `config.json` and `report.md` also record the
simulation id, timestamps and code version.

To see the same workflow in the UI, start `PYTHONPATH=. py -m dashboard.server` and open **Workbench**.

## Limitations

- Two agents per negotiation (the frozen protocol is bilateral).
- Inferential tests in the Workbench are exploratory; scarcity is measured only through quantity, confounded with per-unit value.
- Provider agents cannot run under the zero-cost policy.
- In the public demo (`PUBLIC_DEMO=1`) the workbench is a shared, create-only workspace: no edits or deletes, no local HTTP agents, at most 48 negotiations per experiment, run starts rate-limited, oldest entries pruned (40 agents, 40 resource sets, 30 experiments). Everything is visible to every visitor and lost on restart.
