"""Pilot run (docs/spec.md §8.4): 24 negotiations, real API cost.

    PYTHONPATH=. python scripts/run_pilot.py --max-input-tokens-per-call N \
        --max-cost-usd X [--max-total-tokens T] [--db results/pilot.db] [--resume] [--confirm]

Approved pilot budget (no token cap):

    PYTHONPATH=. python scripts/run_pilot.py --max-input-tokens-per-call 20000 \
        --max-cost-usd 20 [--confirm]

Without --confirm it only validates the configuration and prints the plan
and its worst case; no API call is made. Generation settings come from .env
(see .env.example) and must equal the approved runtime configuration
(§8.15). Budget values have no defaults. Pilot data goes to its own
database, which must not exist yet; after a stop, --resume continues that
database with the same caps and the spend already charged (never a fresh
budget), running only the negotiations that have no record yet.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.agents.generation import GenerationConfig
from src.experiments.pilot import pilot_matrix, pilot_worst_case, prepare_pilot, run_pilot

DEFAULT_DB = Path(__file__).resolve().parent.parent / "results" / "pilot.db"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-input-tokens-per-call", type=int, required=True)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--resume", action="store_true", help="continue an existing pilot database")
    parser.add_argument("--confirm", action="store_true", help="actually run (real API cost)")
    args = parser.parse_args()

    load_dotenv()
    claude_cfg, openai_cfg = GenerationConfig.from_env("CLAUDE"), GenerationConfig.from_env("OPENAI")
    budget_args = dict(max_total_tokens=args.max_total_tokens, max_cost_usd=args.max_cost_usd,
                       max_input_tokens_per_call=args.max_input_tokens_per_call)
    plan, budget = prepare_pilot(claude_cfg, openai_cfg, args.db, resume=args.resume, **budget_args)
    remaining = sum(config.num_negotiations for _, config, *_ in plan)

    print(f"=== PILOT PLAN ({remaining} of {len(pilot_matrix())} negotiations to run, "
          "excluded from confirmatory analysis) ===")
    for cell, config, *_ in plan:
        for seed in range(config.base_seed, config.base_seed + config.num_negotiations):
            print(f"  cell {cell.index}: {cell.condition:13s} Claude={cell.claude_seat} "
                  f"first_mover={cell.first_mover} seed={seed}")
    print(f"  budget: max_total_tokens={args.max_total_tokens} max_cost_usd={args.max_cost_usd} "
          f"max_input_tokens_per_call={args.max_input_tokens_per_call}")
    print(f"  already charged to this pilot's budget: {budget.spent_tokens} tokens, ${budget.spent_cost_usd:.4f}")
    print(f"  worst case of the remaining plan without transport reruns: {pilot_worst_case(budget, remaining)}")
    print(f"  database: {args.db}")
    if not plan:
        print("Nothing left to run.")
        return 0
    if not args.confirm:
        print("Validation only. Re-run with --confirm to make real API calls.")
        return 0

    args.db.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run_pilot(claude_cfg, openai_cfg, args.db, resume=args.resume, **budget_args))
    print(f"Pilot {result['status']}: {result['completed_negotiations']}/{len(pilot_matrix())} negotiations "
          "recorded; this invocation: " + ", ".join(
              f"cell {b['cell'].index} from seed {b['base_seed']}={b['negotiations']}" for b in result["batches"]))
    if result["blocked"]:
        print(f"Blocked (a transport failure is recorded and no outcome; never re-run automatically, "
              f"needs an explicit decision): {result['blocked']}")
    print(f"Pilot budget charged so far, all invocations (worst case for unknown usage): "
          f"{result['spent_tokens']} tokens, ${result['spent_cost_usd']:.4f}")
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
