"""Analysis you can trust: hand-checked metrics, numbers recomputed from the export, small-n warnings, honest wording."""
import csv
import io
import json
from statistics import mean

import pytest

from sandbox import analysis as SA
from tests.sandbox_fake import FakeModels
from tests.test_sandbox_models import DUMMY_KEY, agent, connection, consented, design, isolated, run  # noqa: F401
from workbench import export, simulations as S
from workbench.analytics import analyse

RULES = ("builtin-priority", "builtin-concession")


def _rows(doc, name):
    return list(csv.DictReader(io.StringIO(export.build(doc, name)["content"])))


def hand_trial(**over):
    """Food worth 8 a unit to A and 2 to B; water the reverse. Max is 100 each."""
    offer = lambda turn, actor, a_food, a_water, msg=None: {  # noqa: E731
        "turn": turn, "actor": actor, "action": "OFFER", "message": msg,
        "allocation": {"A": {"food": a_food, "water": a_water}, "B": {"food": 10 - a_food, "water": 10 - a_water}}}
    t = {"index": 0, "repetition": 0, "seed": 0, "condition": "baseline_v1", "status": "completed", "outcome": "agreed", "rounds": 4,
         "utility_A": 80.0, "utility_B": 80.0, "rwe": 1.0, "ew": 0.8, "pool": {"food": 10, "water": 10}, "points": {"A": {"food": 80, "water": 20}, "B": {"food": 20, "water": 80}},
         "events": [offer(1, "A", 10, 10, "Food matters most to me."), offer(2, "B", 0, 0), offer(3, "A", 10, 0, "Hello there."),
                    {"turn": 4, "actor": "B", "action": "ACCEPT", "message": None, "allocation": None}],
         "agents": {"X": {"seat": "A", "ended": False, "utility": 0.8}, "Y": {"seat": "B", "ended": True, "utility": 0.8}}}
    return {**t, **over}


def test_metrics_by_hand():
    m = SA.trial_metrics(hand_trial(), "X")
    assert m["opening_demand"] == 1.0  # asked for everything
    assert m["concession_speed"] == pytest.approx((1.0 - 0.8) / 1)  # 100% then 80%, over one step
    assert m["share_of_value"] == 0.5
    # Offer 1: A 100%, B 0%: fairness 0. Offer 3: A 80%, B 80%: fairness 1.
    assert m["offer_fairness"] == pytest.approx(0.5)
    assert m["walk_away_rate"] == 0.0 and m["rule_break_rate"] == 0.0 and m["turns_to_agreement"] == 4.0
    assert m["reveals_preferences"] == 0.5  # one of two messages names food with a priority word
    assert m["demand_shift"] is None
    y = SA.trial_metrics(hand_trial(), "Y")
    assert y["concession_speed"] is None and y["reveals_preferences"] is None  # not observed, never 0


def test_walk_away_and_rule_breaking_are_charged_to_the_agent_that_did_it():
    walked = hand_trial(outcome="walked_away")
    assert SA.trial_metrics(walked, "Y")["walk_away_rate"] == 1.0 and SA.trial_metrics(walked, "X")["walk_away_rate"] == 0.0
    assert SA.trial_metrics(walked, "X")["share_of_value"] is None and SA.trial_metrics(walked, "X")["turns_to_agreement"] is None
    bad = hand_trial(outcome="invalid_action")
    assert SA.trial_metrics(bad, "Y")["rule_break_rate"] == 1.0


def test_reaction_by_hand():
    ev = {"turn": 3, "category": "fuel", "units": 10, "points": {"A": 100, "B": 0}, "pool_after": {"food": 10, "water": 10, "fuel": 10}}
    t = hand_trial(events_fired=[ev], final_pool=ev["pool_after"], final_valuation={"A": {"food": 8.0, "water": 2.0, "fuel": 10.0}, "B": {"food": 2.0, "water": 8.0, "fuel": 0.0}})
    t["events"] = [t["events"][0], t["events"][1],
                   {"turn": 3, "actor": "A", "action": "OFFER", "message": None, "allocation": {"A": {"food": 10, "water": 0, "fuel": 10}, "B": {"food": 0, "water": 10, "fuel": 0}}},
                   t["events"][3]]
    r = SA.event_reaction(t, "X")
    assert r["before"] == 1.0  # all of the old pool
    assert r["after"] == pytest.approx(180 / 200)  # 80 + 100 of a new maximum of 200
    assert r["shift"] == pytest.approx(-0.1) and r["new_resource_claim"] == 1.0 and r["first_response"] == "OFFER"
    assert SA.event_reaction(hand_trial(), "X") is None


def test_uncertainty_is_counted_per_repetition_not_per_negotiation():
    s = SA.summary([(0, 1.0), (0, 1.0), (0, 1.0), (1, 0.0)], "t")
    assert s["n"] == 2 and s["n_negotiations"] == 4 and s["mean"] == 0.5 and s["small_n"] is True
    assert SA.summary([(0, 1.0)], "one")["ci"] is None  # one repetition: no interval, never a fake one
    assert SA.summary([(0, None)], "none")["mean"] is None
    wide = SA.summary([(i, float(i % 2)) for i in range(40)], "wide")
    assert wide["ci"][0] < 0.5 < wide["ci"][1] and wide["small_n"] is False


def test_paired_difference_uses_shared_repetitions():
    d = SA.paired_difference({0: 0.5, 1: 0.5, 2: 0.9}, {0: 0.7, 1: 0.7}, "p")
    assert d["n"] == 2 and d["mean"] == pytest.approx(0.2)


def test_chart_numbers_equal_numbers_recomputed_from_the_exported_csv():
    doc = run(design(*RULES, environment="builtin-research", environments=["builtin-research", "builtin-field-hospital"], repetitions=6, swap_seats=True, swap_first_mover=True,
                     events=[{"turn": 3, "category": "fuel", "units": 6}]))
    sb = analyse(doc)["sandbox"]
    rows = _rows(doc, "metrics.csv")
    assert rows
    def recompute(agent_label, metric, group=None):
        by_rep = {}
        for r in rows:
            if r["agent"] == agent_label and r["metric"] == metric and r["value"] != "" and (group is None or r["resource_set"] == group):
                by_rep.setdefault(r["repetition"], []).append(float(r["value"]))
        return mean(mean(v) for v in by_rep.values()) if by_rep else None, len(by_rep)
    checked = 0
    for label, profile in sb["profiles"].items():
        for metric, s in profile.items():
            value, n = recompute(label, metric)
            assert n == s["n"], metric
            if s["mean"] is not None:
                assert value == pytest.approx(s["mean"]), metric
                checked += 1
    assert checked > 10
    for g in sb["sweep"]["groups"]:
        for label, profile in g["agents"].items():
            for metric, s in profile.items():
                value, n = recompute(label, metric, g["name"])
                assert n == s["n"] and (s["mean"] is None or value == pytest.approx(s["mean"]))
    behaviour = _rows(doc, "behaviour.csv")
    row = next(r for r in behaviour if r["group"] == "all" and r["agent"] == "X" and r["metric"] == "opening_demand")
    assert float(row["mean"]) == pytest.approx(sb["profiles"]["X"]["opening_demand"]["mean"]) and int(row["n_repetitions"]) == 6


def test_small_samples_and_replays_are_called_out():
    doc = run(design(*RULES, repetitions=3))
    sb = analyse(doc)["sandbox"]
    assert any("Small sample: 3" in w for w in sb["warnings"])
    assert any("replays the same negotiations" in w for w in sb["warnings"])
    assert all(s["small_n"] for s in sb["profiles"]["X"].values())
    assert "replays" in export.report(doc) and "Small sample" in export.report(doc)
    assert not any("replays" in w for w in analyse(run(design(*RULES, environment="builtin-research", repetitions=3)))["sandbox"]["warnings"])


def test_findings_state_uncertainty_and_never_motive():
    doc = run(design(*RULES, environment="builtin-research", environments=["builtin-research", "builtin-field-hospital"], repetitions=4, swap_seats=True))
    sb = analyse(doc)["sandbox"]
    assert sb["findings"]
    text = " ".join(f["text"] for f in sb["findings"])
    assert "n = " in text and "95% CI" in text
    for claim in ("because", "proves", "significant", "wants", "intends", "better at", "strategy is"):
        assert claim not in text.lower(), claim
    assert "exploratory" in sb["exploratory"].lower() and "not the research study" in sb["exploratory"]


def test_every_export_carries_a_source_label_and_no_key():
    fake = FakeModels()
    try:
        from sandbox import keys
        conn = connection(fake, tier="api")
        keys.set_key(conn, DUMMY_KEY)
        aid = agent(conn)
        doc = run(consented(design(aid, repetitions=2, caps={"max_tokens": 100000}, prices={"good": [1.0, 2.0]})))
        for name in ("negotiations.csv", "summary.csv", "behaviour.csv", "usage.csv"):
            content = export.build(doc, name)["content"]
            assert "API MODEL" in content, name
        assert "API model: fake api/good" in export.build(doc, "metrics.csv")["content"]
        for name in ("config.json", "transcripts.json", "analytics.json"):
            assert "API MODEL" in export.build(doc, name)["content"], name
        report = export.report(doc)
        assert "API model: fake api/good" in report and "No empirical experiment with a real LLM" not in report
        for name in export.FILES:
            assert DUMMY_KEY not in json.dumps(export.build(doc, name))
    finally:
        fake.close()


def test_cost_and_tokens_add_up():
    fake = FakeModels()
    try:
        doc = run(design(agent(connection(fake)), repetitions=2))
        u = analyse(doc)["sandbox"]["usage"]
        assert u["calls"] == len(fake.chats()) and u["input_tokens"] == 100 * u["calls"] and u["output_tokens"] == 20 * u["calls"]
        assert u["tokens"] == 120 * u["calls"] and u["cost"] is None and u["priced"] is False  # no price entered: never a made-up cost
        rows = _rows(doc, "usage.csv")
        assert sum(int(r["input_tokens"]) for r in rows) == u["input_tokens"]
    finally:
        fake.close()


def test_intervals_wait_until_the_experiment_finishes():
    doc = run(design(*RULES, environment="builtin-research", repetitions=3))
    running = {**doc, "status": "running"}
    sb = analyse(running)["sandbox"]
    assert sb["intervals_ready"] is False and sb["profiles"]["X"]["opening_demand"]["ci"] is None and sb["profiles"]["X"]["opening_demand"]["mean"] is not None
    assert analyse(doc)["sandbox"]["profiles"]["X"]["opening_demand"]["ci"] is not None
