"""Real-model smoke test. Same harness as run_smoke_test.py, but with one
Claude agent and one OpenAI agent, capped at 3 negotiations. This is the
ONLY script that spends real API credits in Phase 1 — and it's still
capped by ExperimentConfig's mode="smoke" guard (max 5 negotiations,
enforced in code, not just by convention).

Run this locally, not in a shared/sandboxed environment, since it reads
real API keys from .env:

    1. cp .env.example .env
    2. Fill in ANTHROPIC_API_KEY / OPENAI_API_KEY and every CLAUDE_* / OPENAI_*
       generation setting in .env (model, temperature, max output tokens,
       timeout, max retries have NO defaults and must be set explicitly)
    3. pip install -e ".[dev]"
    4. PYTHONPATH=. python scripts/run_real_smoke_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.agents.claude_agent import ClaudeAgent
from src.agents.generation import GenerationConfig
from src.agents.openai_agent import OpenAIAgent
from src.evaluation.metrics import equitability, envy_freeness, social_welfare_efficiency
from src.experiments.config import ExperimentConfig
from src.experiments.runner import run_batch

RESULTS_DB = Path(__file__).resolve().parent.parent / "results" / "real_smoke_test.db"


async def main():
    print("=== REAL-MODEL SMOKE TEST (Claude vs GPT, 3 negotiations, real API cost) ===\n")

    config = ExperimentConfig(
        mode="smoke",
        method="baseline_plain_prompting",
        model_A_label="claude",
        model_B_label="openai",
        num_negotiations=3,
        base_seed=2000,
        max_rounds=8,
        num_categories=3,
    )

    agent_A = ClaudeAgent(GenerationConfig.from_env("CLAUDE"))
    agent_B = OpenAIAgent(GenerationConfig.from_env("OPENAI"))

    RESULTS_DB.parent.mkdir(exist_ok=True)
    records = await run_batch(config, agent_A, agent_B, db_path=RESULTS_DB)

    for i, r in enumerate(records, 1):
        print(f"--- Negotiation {i} (seed={r.instance_seed}) ---")
        print(f"  Outcome: {r.outcome} | Rounds: {r.num_rounds}")
        print(f"  Utility A (Claude)={r.utility_A:.2f}  Utility B (GPT)={r.utility_B:.2f}")
        if r.outcome == "agreed":
            print(f"  SWE={social_welfare_efficiency(r):.3f}  Equitability={equitability(r):.3f}  "
                  f"Envy-free={envy_freeness(r).envy_free}")
        print()

    print("This confirms real API calls work end-to-end through the exact same harness")
    print("verified by the mock smoke test. Still not a real experiment — n=3, one pairing,")
    print("one method. Do not draw conclusions from this run.")


if __name__ == "__main__":
    asyncio.run(main())
