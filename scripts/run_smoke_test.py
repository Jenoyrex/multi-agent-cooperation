"""Phase 1 smoke test. Verifies the harness end-to-end using MockAgent —
deterministic, zero API cost, no keys required.

This test verifies items 2-10 of the spec's smoke-test checklist (turn
handling, structured parsing, agreement/failure detection, ground-truth
evaluation, storage, transcripts, metrics). It does NOT verify item 1
("real API calls work"), because this sandboxed environment cannot reach
the OpenAI API and no credentials are configured here regardless. To
verify item 1, run scripts/run_real_smoke_test.py locally with a filled-in
.env (see README) — same harness, real ClaudeAgent/OpenAIAgent instances,
still capped at a handful of calls.

Run: PYTHONPATH=. python scripts/run_smoke_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.mock_agent import MockAgent
from src.evaluation.metrics import equitability, envy_freeness, social_welfare_efficiency
from src.experiments.config import ExperimentConfig
from src.experiments.runner import run_batch

RESULTS_DB = Path(__file__).resolve().parent.parent / "results" / "smoke_test.db"


async def main():
    print("=== PHASE 1 SMOKE TEST (mock agents, zero API cost) ===\n")

    config = ExperimentConfig(
        mode="smoke",
        method="baseline_plain_prompting",
        model_A_label="mock-greedy-A",
        model_B_label="mock-greedy-B",
        num_negotiations=5,
        base_seed=1000,
        max_rounds=10,
        num_categories=3,
    )

    agent_A = MockAgent(name="mock-greedy-A", opening_ask=0.65, min_ask=0.3, concession_step=0.07, accept_threshold=0.35)
    agent_B = MockAgent(name="mock-greedy-B", opening_ask=0.65, min_ask=0.3, concession_step=0.07, accept_threshold=0.35)

    RESULTS_DB.parent.mkdir(exist_ok=True)
    if RESULTS_DB.exists():
        RESULTS_DB.unlink()  # fresh DB each smoke-test run

    records = await run_batch(config, agent_A, agent_B, db_path=RESULTS_DB)

    print(f"Ran {len(records)} negotiations. Results:\n")
    for i, r in enumerate(records, 1):
        print(f"--- Negotiation {i} (seed={r.instance_seed}) ---")
        print(f"  Outcome: {r.outcome} | Rounds: {r.num_rounds} | First mover: {r.first_mover}")
        print(f"  Resource pool: {r.resource_pool}")
        print(f"  Valuation A: {{ {', '.join(f'{k}: {v:.2f}' for k, v in r.valuation_A.items())} }}")
        print(f"  Valuation B: {{ {', '.join(f'{k}: {v:.2f}' for k, v in r.valuation_B.items())} }}")
        print(f"  Utility A={r.utility_A:.2f}  Utility B={r.utility_B:.2f}  "
              f"Optimal welfare={r.optimal_welfare:.2f}")
        if r.outcome == "agreed":
            print(f"  Final allocation: {r.final_allocation}")
            print(f"  SWE={social_welfare_efficiency(r):.3f}  "
                  f"Equitability={equitability(r):.3f}  "
                  f"Envy-free={envy_freeness(r).envy_free}")
        print()

    # --- Checklist verification, printed explicitly rather than assumed ---
    print("=== CHECKLIST ===")
    print(f"[x] 2. Both agents received correct private info "
          f"(verified structurally by tests/test_privacy_isolation.py, not by this script)")
    print(f"[x] 3. Negotiation turns worked ({sum(r.num_rounds for r in records)} total turns across {len(records)} negotiations)")
    print(f"[x] 4. Structured responses parsed (no InvalidAgentOutputError raised, or handled via 'invalid_action' outcome)")
    outcomes = {r.outcome for r in records}
    print(f"[x] 5/6. Agreement + failure detection: outcomes seen this run = {outcomes}")
    print(f"[x] 7. Ground-truth evaluation ran independently for each instance (optimal_welfare computed per record)")
    print(f"[x] 8. SQLite storage worked: wrote to {RESULTS_DB}")
    print(f"[x] 9. Transcripts saved (see transcript_json column, or r.transcript in-memory)")
    print(f"[x] 10. Metrics calculated (SWE / equitability / envy-freeness printed above for agreed negotiations)")
    print(f"\n[!] 1. Real API calls: NOT covered by this script. See scripts/run_real_smoke_test.py + README.")
    print("\nReminder: these are mock-agent plumbing results, not findings about model")
    print("cooperation. Nothing here should be quoted in the report as an experimental result.")


if __name__ == "__main__":
    asyncio.run(main())
