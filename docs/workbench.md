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

| Page | What it does |
|---|---|
| Overview `#/workbench` | Counts, recent simulations, what runs on this server |
| Agents `#/agents` | List, create, edit, duplicate and delete agents; built-ins are read-only |
| Environments `#/environments` | Visual editor, JSON or text import, live server-side validation and preview, save |
| Simulations `#/simulations` | List; `#/simulations/new` builds a design and shows its exact size before it runs |
| Simulation `#/simulation/<id>` | Live turns from the engine, negotiations with transcripts, analytics, log, exports |

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

## Limitations

- Two agents per negotiation (the frozen protocol is bilateral).
- No inferential tests; no analysis of responses to scarcity yet.
- Provider agents cannot run under the zero-cost policy.
- In the public demo the workbench is read-only.
