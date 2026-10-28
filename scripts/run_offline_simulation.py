"""Offline simulation pilot: SIMULATED DATA, zero cost, no network.

    PYTHONPATH=. py scripts/run_offline_simulation.py [--out artifacts/offline_simulation]

Runs the frozen 24-negotiation pilot matrix with deterministic mock agents
inside the network tripwire, re-checks every stored record, and writes
negotiations.csv, summary.json and REPORT.md (identical bytes on every run)
plus simulation.db (recreated on each run; not committed). See
src/experiments/simulation.py. It is not evidence about any LLM.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import zero_cost
from src.experiments import simulation as S

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "artifacts" / "offline_simulation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if zero_cost.paid_calls_allowed():
        raise SystemExit(f"Refusing: {zero_cost.ENV}=1 is set, and the offline simulation must run in zero-cost mode.")

    db = args.out / "simulation.db"
    args.out.mkdir(parents=True, exist_ok=True)
    if db.exists():
        S.load_rows(db)  # refuses unless it holds only simulated data, so nothing else is ever deleted
        db.unlink()
    with zero_cost.network_tripwire() as blocked:
        asyncio.run(S.run_simulation(db))
        rows = S.load_rows(db)
        problems = S.verify(rows)
        summary = S.analyse(rows)
    hashes = S.write_artifacts(args.out, summary, problems, blocked)

    print(S.LABEL)
    print(f"{summary['negotiations']} simulated negotiations; integrity problems: {len(problems)}; "
          f"network attempts blocked: {len(blocked)}; API keys present: "
          f"{any(os.environ.get(k) for k in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY'))}")
    for name, digest in hashes.items():
        print(f"  {args.out / name}  sha256={digest}")
    ok = not problems and not blocked and summary["condition_has_no_effect_by_construction"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
