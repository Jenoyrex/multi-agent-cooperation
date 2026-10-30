"""The preregistered analysis (docs/spec.md §8.9, §8.11) on a full-run database.

    PYTHONPATH=. py scripts/run_analysis.py --db results/full.db [--out analysis.json]

Refuses unless the database holds the complete confirmatory dataset: exactly the
480 full-mode negotiations on seeds 40000-40059, each design cell once, between the approved
Claude and GPT snapshots with the approved round limit (no interim analysis, §8.9). Pilot and simulated runs are never analysed here. The output
records the analysis seed. Costs nothing: it only reads the database.
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import data as D
from src.evaluation import inference as I


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    conn = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [D.summarize(D.row_to_record(r), str(r["id"]), "full") for r in conn.execute(
            "SELECT n.* FROM negotiations n JOIN runs r USING (run_id) WHERE r.mode = 'full'")]
    finally:
        conn.close()
    if not D.full_design_complete(rows):
        print(f"Refusing: {len(rows)} full-run negotiations over {len(Counter(r['seed'] for r in rows))} instances; the analysis needs "
              f"all {D.FULL_RUN_TOTAL}: every seed {D.FULL_RUN_SEEDS[0]}-{D.FULL_RUN_SEEDS[1]} once in each condition x Claude seat x "
              f"first mover cell, all {D.APPROVED_PAIRING[0]} vs {D.APPROVED_PAIRING[1]} with max_rounds {D.APPROVED_MAX_ROUNDS}. "
              f"No interim analysis (§8.9).")
        return 1
    result = I.analyse(rows)
    for h, r in result["primary"].items():
        print(f"{h} {r['label']}: {I.interpret(r)}")
    if args.out:
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
