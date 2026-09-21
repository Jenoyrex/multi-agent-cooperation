import json
import os
import subprocess
import sys
from pathlib import Path

from src.agents.mock_agent import MockAgent
from src.experiments.config import ExperimentConfig
from src.experiments.runner import _build_instance_state

ROOT = Path(__file__).resolve().parents[1]

_SNIPPET = """
import json
from src.agents.mock_agent import MockAgent
from src.experiments.config import ExperimentConfig
from src.experiments.runner import _build_instance_state
cfg = ExperimentConfig(mode="smoke", method="t", model_A_label="a", model_B_label="b",
                       num_negotiations=1, base_seed=1234)
s = _build_instance_state(1234, cfg, MockAgent(), MockAgent())
print(json.dumps([s.resource_pool, s.valuation_A, s.valuation_B], sort_keys=True))
"""


def _cfg(**kw) -> ExperimentConfig:
    return ExperimentConfig(mode="smoke", method="t", model_A_label="a", model_B_label="b",
                            num_negotiations=4, base_seed=100, **kw)


def _run_in_new_process(hashseed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    out = subprocess.run([sys.executable, "-c", _SNIPPET], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_instance_identical_across_processes():
    # Two fresh interpreters with different str-hash salts must agree with
    # each other and with this process. (The salt is varied only to prove
    # independence from it; nothing relies on it being set.)
    first, second = _run_in_new_process("1"), _run_in_new_process("2")
    assert first == second

    cfg = _cfg()
    s = _build_instance_state(1234, cfg, MockAgent(), MockAgent())
    local = json.dumps([s.resource_pool, s.valuation_A, s.valuation_B], sort_keys=True)
    assert local == first


def test_first_mover_policy_does_not_change_instance():
    for i in range(4):
        states = [
            _build_instance_state(100 + i, _cfg(first_mover_policy=p), MockAgent(), MockAgent(), index=i)
            for p in ("A", "B", "alternate")
        ]
        assert len({tuple(sorted(s.resource_pool.items())) for s in states}) == 1
        assert len({tuple(sorted(s.valuation_A.items())) for s in states}) == 1
        assert len({tuple(sorted(s.valuation_B.items())) for s in states}) == 1
        assert states[0].first_mover == "A" and states[1].first_mover == "B"
        assert [s.first_mover_policy for s in states] == ["A", "B", "alternate"]


def test_first_mover_independent_of_seed_parity():
    # Same index, seeds of opposite parity -> same first mover.
    a = _build_instance_state(100, _cfg(), MockAgent(), MockAgent(), index=0)
    b = _build_instance_state(101, _cfg(), MockAgent(), MockAgent(), index=0)
    assert a.first_mover == b.first_mover == "A"


def test_alternate_mapping_by_index():
    cfg = _cfg(first_mover_policy="alternate")
    assert [cfg.first_mover_for(i) for i in range(4)] == ["A", "B", "A", "B"]
    states = [_build_instance_state(100 + i, cfg, MockAgent(), MockAgent(), index=i) for i in range(4)]
    assert [s.first_mover for s in states] == ["A", "B", "A", "B"]
