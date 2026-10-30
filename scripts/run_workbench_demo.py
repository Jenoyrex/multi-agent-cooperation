"""Workbench end-to-end demo: zero cost, no network, deterministic.

    PYTHONPATH=. py scripts/run_workbench_demo.py [--out sandbox_runs/workbench_demo]

Walks the complete workflow through the same validated API the UI uses:
  1. configures two rule-based agents (a Priority agent and a custom Concession agent),
  2. imports an environment (the research generator, so every seed is a new instance),
  3. designs a simulation: both conditions x 10 repetitions x swapped seats x swapped openers = 80 negotiations,
  4. runs it and prints one transcript,
  5. prints behavioural observations,
  6. runs the exploratory statistical analysis (the design has both conditions and 10 paired instances),
  7. exports every file.

Everything is SIMULATED: deterministic rule-based agents, which never read instructions,
so the two conditions are identical by construction and the analysis must find no
difference. Nothing here is evidence about Claude, GPT, Gemini or any LLM.
Outputs go to a fresh workbench database inside --out (gitignored by default).
DEMO_SUMMARY.md and the CSV/JSON exports are byte-identical on every run; config.json
and report.md also record the simulation id, timestamps and code version, which change.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import zero_cost
from workbench import api, export, simulations as S, store
from workbench.analytics import analyse

ROOT = Path(__file__).resolve().parent.parent
DETERMINISTIC = ("negotiations.csv", "summary.csv", "analytics.json", "transcripts.json", "environment.json", "DEMO_SUMMARY.md")
ENVIRONMENT = {"format": "json", "content": json.dumps({
    "name": "Demo: research generator", "source": "generated", "categories": 3, "max_rounds": 10, "first_mover": "A",
    "description": "Three resources; quantities and both agents' private points drawn from each seed (spec §1-§2)."})}


def post(path, body):
    payload, status = api.post_route(path)(body)
    assert status in (200, 201, 202), (path, status, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "sandbox_runs" / "workbench_demo")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # the summary uses typographic signs; Windows consoles default to cp1252
    if zero_cost.paid_calls_allowed():
        raise SystemExit(f"Refusing: {zero_cost.ENV}=1 is set; the demo runs in zero-cost mode only.")
    if (args.out / "workbench.db").resolve() == store.DB_PATH.resolve():  # e.g. --out sandbox_runs
        raise SystemExit(f"Refusing: {store.DB_PATH} is the workbench's own database, which this demo would delete. "
                         "Choose another --out.")
    args.out.mkdir(parents=True, exist_ok=True)
    store.DB_PATH = args.out / "workbench.db"
    if store.DB_PATH.exists():
        store.DB_PATH.unlink()  # this demo's own database, recreated on every run

    with zero_cost.network_tripwire() as blocked:
        # 1. agents
        x = post("/api/workbench/agents", {"name": "Demo priority trader", "kind": "priority",
                                           "params": {"high_claim": 0.85, "low_claim": 0.25, "step": 0.05, "floor": 0.6, "accept_threshold": 0.5}})
        y = post("/api/workbench/agents", {"name": "Demo conceder", "kind": "concession",
                                           "params": {"opening_ask": 0.7, "min_ask": 0.4, "concession_step": 0.06, "accept_threshold": 0.5}})
        # 2. environment: validate and preview, then save
        preview = post("/api/workbench/environments/preview", ENVIRONMENT)
        env = post("/api/workbench/environments", preview["environment"])
        # 3-4. design and run
        sim = S.start({"name": "Workbench demo", "environment": env["id"], "agents": {"X": x["id"], "Y": y["id"]},
                       "conditions": ["baseline_v1", "structured_v1"], "repetitions": 10, "base_seed": 7000,
                       "swap_seats": True, "swap_first_mover": True, "pace": "instant"}, background=False)
        doc = store.get("simulations", sim.id)
        a = analyse(doc)
        files = {name: export.build(doc, name)["content"] for name in export.FILES}

    first = doc["trials"][0]
    turns = "\n".join(f"  turn {e['turn']} seat {e['actor']}: {e['action']}"
                      + (f" (A gets {e['allocation']['A']})" if e["action"] == "OFFER" else "") for e in first["events"])
    inf = a["inference"]
    summary = "\n".join([
        "# Workbench demo (SIMULATED)", "", f"> {doc['config']['label']}", "",
        f"- Agents: X {x['name']} (priority), Y {y['name']} (concession). Environment: {env['name']}.",
        f"- Design: {doc['plan_size']} negotiations; completed {a['n_completed']}; outcomes {a['overall']['outcomes']}.",
        f"- Agreement {a['overall']['agreement_rate']:.3f}, mean RWE {a['overall']['rwe']:.4f}, mean EW {a['overall']['ew']:.4f}.",
        "", "## First negotiation", f"seed {first['seed']}, {first['condition']}, X in seat {first['agents']['X']['seat']}, "
        f"{first['first_mover']} opens: {first['outcome']} after {first['rounds']} turns", turns,
        "", "## Observations", *[f"- [{o['basis']}] {o['text']}" for o in a["observations"]],
        "", "## Exploratory inference", f"> {inf['scope']}", *[f"- {h}: {t}" for h, t in inf["interpretations"].items()],
        "", "Expected by construction: neither agent reads instructions, so every paired difference is 0 and no test is significant.",
        "", f"Network attempts blocked: {len(blocked)}.", ""])
    files["DEMO_SUMMARY.md"] = summary
    for name, text in files.items():
        (args.out / name).write_text(text, encoding="utf-8", newline="\n")

    print(summary)
    print("Files (sha256; * = identical on every run):")
    for name in sorted(files):
        print(f"  {'*' if name in DETERMINISTIC else ' '} {name}  {hashlib.sha256(files[name].encode()).hexdigest()}")
    ok = (not blocked and a["n_completed"] == doc["plan_size"] and inf["available"]
          and all(r["mean_difference"] == 0 and not r["permutation"]["reject"] for r in inf["result"]["primary"].values()))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
