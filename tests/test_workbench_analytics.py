"""Behavioural measures in workbench/analytics.py, on hand-built trials with known answers."""
import pytest

from workbench import analytics as A


def trial(index, *, pool, x_claims, x_points, seed=1, condition="baseline_v1", outcome="agreed", first_mover="A",
          x_seat="A", x_offer_values=(80,), utility=0.6, rwe=0.8, ew=0.5):
    """X sits in x_seat; its opening offer claims x_claims (shares); its offers are worth x_offer_values (own points)."""
    y_seat = "B" if x_seat == "A" else "A"
    events = [{"turn": i + 1, "actor": x_seat, "action": "OFFER", f"value_{x_seat}": v, f"value_{y_seat}": 100 - v}
              for i, v in enumerate(x_offer_values)]
    return {"index": index, "seed": seed, "condition": condition, "status": "completed", "outcome": outcome, "first_mover": first_mover,
            "pool": pool, "points": {x_seat: x_points, y_seat: {c: 100 / len(pool) for c in pool}}, "events": events,
            "rwe": rwe if outcome == "agreed" else 0.0, "ew": ew if outcome == "agreed" else 0.0,
            # Fields every stored trial carries (agreement-only metrics are None on failures).
            "sw": 80.0 if outcome == "agreed" else None, "swe": rwe if outcome == "agreed" else None,
            "equitability": 0.9 if outcome == "agreed" else None, "envy_free": True if outcome == "agreed" else None,
            "invalid_reason": None, "rounds": len(x_offer_values),
            "agents": {"X": {"seat": x_seat, "utility": utility if outcome == "agreed" else 0.0, "opening": {"turn": 1, "shares": x_claims},
                             "final_shares": None, "ended": False,
                             "behavior": {"opening_demand": x_offer_values[0] / 100, "offers": len(x_offer_values),
                                          "total_concession": (x_offer_values[0] - x_offer_values[-1]) / 100 if len(x_offer_values) > 1 else None,
                                          "concession_frequency": None, "accepted_value": None}},
                       "Y": {"seat": y_seat, "utility": 0.4, "opening": None, "final_shares": None, "ended": False,
                             "behavior": {"opening_demand": None, "offers": 0, "total_concession": None, "concession_frequency": None, "accepted_value": None}}}}


def test_scarcity_needs_varying_quantities():
    ts = [trial(i, pool={"a": 10, "b": 10}, x_claims={"a": 0.5, "b": 0.5}, x_points={"a": 50, "b": 50}) for i in range(4)]
    s = A.scarcity_response("X", ts)
    assert s["measurable"] is False and s["n"] == 8 and "distinct quantities" in s["reason"]


def test_scarcity_bins_and_rank_correlation():
    # The scarcer the resource, the larger the share claimed: a perfect negative rank correlation.
    pools = [{"a": 10, "b": 40}, {"a": 20, "b": 50}, {"a": 30, "b": 60}]
    claim = lambda q: 1 - q / 100  # noqa: E731
    ts = [trial(i, pool=p, x_claims={c: claim(q) for c, q in p.items()}, x_points={"a": 50, "b": 50}) for i, p in enumerate(pools)]
    s = A.scarcity_response("X", ts)
    assert s["measurable"] and s["n"] == 6 and s["spearman_quantity_vs_claim"] == pytest.approx(-1.0)
    assert sum(b["n"] for b in s["bins"].values()) == 6
    assert s["bins"]["scarce"]["opening_claim_mean"] > s["bins"]["plentiful"]["opening_claim_mean"]


def test_concession_curve_counts_negotiations_reaching_each_offer():
    ts = [trial(0, pool={"a": 10, "b": 10}, x_claims={"a": .5, "b": .5}, x_points={"a": 60, "b": 40}, x_offer_values=(90, 80, 70)),
          trial(1, pool={"a": 10, "b": 10}, x_claims={"a": .5, "b": .5}, x_points={"a": 60, "b": 40}, x_offer_values=(70,))]
    pts = A.concession_curve("X", ts)["points"]
    assert [(p["k"], p["n"]) for p in pts] == [(1, 2), (2, 1), (3, 1)]
    assert [p["mean_value"] for p in pts] == pytest.approx([0.8, 0.8, 0.7])


def test_value_split_compares_high_and_low_value_resources_and_skips_equal_points():
    ts = [trial(0, pool={"a": 10, "b": 10, "c": 10}, x_claims={"a": 0.9, "b": 0.3, "c": 0.1}, x_points={"a": 60, "b": 30, "c": 10}),
          trial(1, pool={"a": 10, "b": 10}, x_claims={"a": 0.5, "b": 0.5}, x_points={"a": 50, "b": 50})]  # equal: skipped
    v = A.value_split("X", ts)
    assert v["n_negotiations"] == 1 and v["evidence"] == [{"trial": 0}]
    assert v["high_value_claim"] == pytest.approx(0.9)                 # top half of 3 = 1 resource
    assert v["low_value_claim"] == pytest.approx(0.2) and v["difference"] == pytest.approx(0.7)


def test_roles_split_by_opener_and_seat_with_failures_scored_zero():
    pool = {"a": 10, "b": 10}
    ts = [trial(0, pool=pool, x_claims={"a": .5, "b": .5}, x_points={"a": 60, "b": 40}, first_mover="A", x_seat="A", utility=0.7),
          trial(1, pool=pool, x_claims={"a": .5, "b": .5}, x_points={"a": 60, "b": 40}, first_mover="A", x_seat="B", outcome="timeout"),
          trial(2, pool=pool, x_claims={"a": .5, "b": .5}, x_points={"a": 60, "b": 40}, first_mover="B", x_seat="B", utility=0.5)]
    r = A.by_role("X", ts)
    assert (r["opens"]["n"], r["opens"]["agreement_rate"], r["opens"]["utility_mean"]) == (2, 1.0, pytest.approx(0.6))
    assert (r["responds"]["n"], r["responds"]["agreement_rate"], r["responds"]["utility_mean"]) == (1, 0.0, 0.0)
    assert (r["seat_A"]["n"], r["seat_B"]["n"]) == (1, 2) and r["seat_B"]["outcomes"]["timeout"] == 1


def _doc(trials, conditions=("baseline_v1", "structured_v1"), source="generated"):
    agent = {"name": "n", "kind": "concession", "params": {}}
    return {"trials": trials, "config": {"conditions": list(conditions), "label": "SIMULATED: test",
                                         "agents": {"X": agent, "Y": agent}, "environment": {"source": source}}}


def test_exploratory_inference_uses_real_instances_not_repetitions():
    pool, pts = {"a": 10, "b": 10}, {"a": 60, "b": 40}
    ts = [trial(i, pool=pool, x_claims={"a": .5, "b": .5}, x_points=pts, seed=s, condition=c)
          for i, (s, c) in enumerate((s, c) for s in (1, 2, 3) for c in ("baseline_v1", "structured_v1"))]
    generated = A.analyse(_doc(ts))["inference"]
    assert generated["available"] and generated["result"]["primary"]["H1"]["n_pairs"] == 3
    fixed = A.analyse(_doc(ts, source="custom"))["inference"]
    assert fixed["fixed_environment"] and fixed["result"]["primary"]["H1"]["n_pairs"] == 1  # 3 seeds, one instance
    assert "Exploratory" in fixed["scope"] and "LLM" in fixed["scope"]


def test_one_condition_and_empty_simulations_are_reported_not_fabricated():
    pool, pts = {"a": 10, "b": 10}, {"a": 60, "b": 40}
    one = A.analyse(_doc([trial(0, pool=pool, x_claims={"a": .5, "b": .5}, x_points=pts)], conditions=("baseline_v1",)))
    assert one["inference"] == {"available": False, "reason": "Needs both conditions, baseline_v1 and structured_v1, in the design."}
    empty = A.analyse(_doc([]))
    assert empty["behaviour"] == {} and empty["observations"] == [] and empty["inference"]["available"] is False
    assert any("deterministic rules" in x for x in empty["not_available"])


def test_alignment_separates_strict_alignment_from_ties_and_uniform_claims_are_named():
    pool = {"a": 10, "b": 10, "c": 10}
    uniform = trial(0, pool=pool, x_claims={"a": 0.7, "b": 0.7, "c": 0.7}, x_points={"a": 60, "b": 30, "c": 10})
    aligned = trial(1, pool=pool, x_claims={"a": 0.9, "b": 0.5, "c": 0.1}, x_points={"a": 60, "b": 30, "c": 10})
    assert A._rank_agreement(uniform["points"]["A"], uniform["agents"]["X"]["opening"]["shares"]) == (0.0, 1.0)
    assert A._rank_agreement(aligned["points"]["A"], aligned["agents"]["X"]["opening"]["shares"]) == (1.0, 0.0)
    p = A.agent_profile("X", [uniform])
    obs = A.observations({"X": p}, {}, {"X": "U"}, {"X": False})
    assert any("about the same share of every resource" in o["text"] for o in obs)
    assert any("strictly larger" in o["text"] and "100% it claimed equal shares" in o["text"] for o in obs)


def test_per_condition_concession_reports_its_own_n():
    """Concession needs at least two own offers, so its n can be smaller than the condition's n."""
    ts = [trial(0, pool={"a": 10, "b": 10}, x_claims={"a": 0.5, "b": 0.5}, x_points={"a": 50, "b": 50}, x_offer_values=(80, 70)),
          trial(1, pool={"a": 10, "b": 10}, x_claims={"a": 0.5, "b": 0.5}, x_points={"a": 50, "b": 50}, x_offer_values=(80,))]
    c = A.by_condition_agent("X", ts, ["baseline_v1"])["baseline_v1"]
    assert c["n"] == 2 and c["n_concession"] == 1 and c["total_concession_mean"] == pytest.approx(0.1)


def test_exploratory_inference_waits_for_a_running_simulation_to_finish():
    """Review M2: a live view polls every 400 ms; the resampling runs once the simulation has finished."""
    ts = [trial(i, pool={"a": 10, "b": 10}, x_claims={"a": 0.5, "b": 0.5}, x_points={"a": 50, "b": 50}, seed=i // 2,
                condition=("baseline_v1", "structured_v1")[i % 2]) for i in range(4)]
    doc = {"status": "running", "trials": ts, "config": {"label": "SIMULATED: x", "conditions": ["baseline_v1", "structured_v1"],
           "environment": {"source": "generated"}, "agents": {k: {"name": k, "kind": "priority"} for k in "XY"}}}
    assert A.analyse(doc)["inference"] == {"available": False, "reason": "Computed when the simulation finishes (it is still running)."}
    assert A.analyse({**doc, "status": "completed"})["inference"]["available"] is True


def test_a_hardening_agent_is_described_as_raising_its_ask():
    """Final review: a negative total concession used to read 'lowered what it asked for by -0.12'."""
    base = {"resources": {}, "priority_alignment": None, "n_concession": 3, "accepted": [], "walked_away": [], "invalid": [],
            "negotiations": 3}
    text = lambda c: [o["text"] for o in A.observations({"X": {**base, "total_concession_mean": c}}, {}, {"X": "Agent"}, {"X": True})][0]  # noqa: E731
    assert "raised what it asked for by 0.12" in text(-0.12) and "-0" not in text(-0.12)
    assert "lowered what it asked for by 0.12" in text(0.12)
