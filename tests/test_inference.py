"""Mathematical validation of the preregistered analysis (src/evaluation/inference.py).

These tests establish that the procedures compute what spec §8.9/§8.11 define, on
synthetic data with known answers. They say nothing about how any agent behaves."""
import itertools
import math
import random

import pytest

from src.evaluation import inference as I


def rows_for(diffs, metric="rwe", per_condition=4, base=0.5):
    """Synthetic negotiations: instance i gets per-condition mean `base` (baseline) and
    `base + d_i` (structured), spread over `per_condition` agreed negotiations each."""
    rows = []
    for i, d in enumerate(diffs):
        for cond, m in (("baseline_v1", base), ("structured_v1", base + d)):
            for _ in range(per_condition):
                rows.append({"seed": 1000 + i, "condition": cond, "outcome": "agreed", "rwe": m, "ew": m})
    return rows


def exact_sign_flip_p(d):
    """Enumerate every sign pattern: the exact two-sided permutation p-value."""
    t = abs(sum(d) / len(d))
    hits = sum(abs(sum(s * x for s, x in zip(signs, d)) / len(d)) >= t * (1 - 1e-12)
               for signs in itertools.product((1, -1), repeat=len(d)))
    return hits / 2 ** len(d)


def mc_tolerance(p, n=I.PERMUTATIONS):
    return 4 * math.sqrt(max(p * (1 - p), 1e-4) / n) + 1 / n


# ------------------------------------------------------------ 1. identical conditions
def test_identical_conditions_give_zero_effect_p_one_and_undefined_wilcoxon():
    h = I.analyse(rows_for([0.0] * 12))["primary"]["H1"]
    assert h["n_pairs"] == 12 and h["mean_difference"] == 0
    assert h["permutation"]["p"] == 1.0 and h["permutation"]["p_holm"] == 1.0 and not h["permutation"]["reject"]
    assert (h["bootstrap"]["low"], h["bootstrap"]["high"]) == (0, 0)
    assert h["wilcoxon"] == {"defined": False, "reason": "every paired difference is zero", "n_used": 0, "zeros_dropped": 12}
    assert "not statistically significant" in I.interpret(h)


# ------------------------------------------------------------ 2-3. consistent effects
@pytest.mark.parametrize("effect", [0.1, -0.1])
def test_a_consistent_effect_is_detected_in_its_direction(effect):
    res = I.analyse(rows_for([effect] * 20))["primary"]
    h = res["H1"]
    # Only the all-+ and all-− patterns reach |T|: P = 2/2^20, so essentially no hits.
    assert h["permutation"]["p"] <= 3 / (1 + I.PERMUTATIONS)
    assert h["bootstrap"]["low"] == pytest.approx(effect) and h["bootstrap"]["high"] == pytest.approx(effect)
    assert h["permutation"]["reject"] and "statistically significant after Holm" in I.interpret(h)
    assert ("higher" if effect > 0 else "lower") in I.interpret(h)


# ------------------------------------------------------------ 4. mixed effects and ties
def test_mixed_effects_match_exact_enumeration_within_monte_carlo_error():
    d = [0.3, -0.1, 0.2, 0.2, -0.05, 0.0, 0.15, -0.2, 0.1, 0.25]
    h = I.analyse(rows_for(d))["primary"]["H1"]
    exact = exact_sign_flip_p(d)
    assert abs(h["permutation"]["p"] - exact) <= mc_tolerance(exact)
    assert h["wilcoxon"]["zeros_dropped"] == 1 and h["wilcoxon"]["n_used"] == 9
    assert h["wilcoxon"]["method"].startswith("normal")  # |0.2| and |-0.2| tie


def test_toy_example_by_hand():
    d = [1.0, -2.0, 3.0]
    # Sign flips of (1, -2, 3): sums 6, 0, 2, -4, 4, -2, 0, -6; |sum| >= 2 in 6 of 8 -> exact p = 0.75.
    assert exact_sign_flip_p(d) == 0.75
    p = I.sign_flip_test(d, random.Random(1))["p"]
    assert abs(p - 0.75) <= mc_tolerance(0.75)
    # Ranks of |d|: 1, 2, 3; W+ = 1 + 3 = 4. Null counts for W+ = 0..6: 1,1,1,2,1,1,1 (of 8).
    # P(W+ <= 4) = 6/8, P(W+ >= 4) = 3/8 -> two-sided p = 2 * 3/8 = 0.75.
    w = I.wilcoxon_signed_rank(d)
    assert (w["statistic_w_plus"], w["method"], w["p"]) == (4, "exact", 0.75)
    # Bootstrap of (0, 1): resample means 0, 0.5, 1 with probability 1/4, 1/2, 1/4.
    ci = I.bootstrap_ci([0.0, 1.0], random.Random(2))
    assert (ci["low"], ci["high"]) == (0.0, 1.0)
    assert I._percentile([0.0, 10.0], 0.5) == 5.0 and I._percentile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.75


def test_wilcoxon_matches_scipy_as_an_independent_oracle():
    stats = pytest.importorskip("scipy.stats")  # test-only oracle; not a runtime dependency
    rng = random.Random(7)
    cases = [[rng.uniform(-1, 1) for _ in range(n)] for n in (5, 9, 17, 30, 55)]  # no ties
    cases += [[round(rng.uniform(-1, 1), 1) for _ in range(n)] for n in (8, 20, 40)]  # ties
    for d in cases:
        mine = I.wilcoxon_signed_rank(d)
        nz = [x for x in d if x != 0]
        method = "exact" if mine["method"] == "exact" else next(
            m for m in ("asymptotic", "approx") if m in stats.wilcoxon.__doc__)
        ref = stats.wilcoxon(nz, zero_method="wilcox", correction=False, method=method)
        assert mine["p"] == pytest.approx(ref.pvalue, rel=1e-9, abs=1e-12), (len(d), mine["method"])


# ------------------------------------------------------------ 5. small and insufficient samples
def test_small_and_empty_samples_are_reported_not_fabricated():
    one = I.analyse(rows_for([0.4]))["primary"]["H1"]
    assert one["permutation"]["p"] == 1.0  # both sign patterns give |T*| = |T|
    assert "2^1" in one["permutation"]["note"] and "Degenerate" in one["bootstrap"]["note"]
    assert "no two-sided p below 0.05" in one["wilcoxon"]["note"]
    empty = I.analyse([])["primary"]["H1"]
    assert empty["n_pairs"] == 0 and empty["mean_difference"] is None
    assert empty["permutation"] == {"defined": False, "reason": "no paired instances", "p_holm": None, "reject": False}
    assert I.interpret(empty) == "Not testable: no paired instances."


# ------------------------------------------------------------ 6. failures, invalid actions, missing pairs
def test_failures_score_zero_and_missing_pairs_are_reported():
    rows = [
        {"seed": 1, "condition": "baseline_v1", "outcome": "agreed", "rwe": 0.8, "ew": 0.4},
        {"seed": 1, "condition": "baseline_v1", "outcome": "walked_away", "rwe": 0.0, "ew": 0.0},
        {"seed": 1, "condition": "structured_v1", "outcome": "agreed", "rwe": 0.9, "ew": 0.45},
        {"seed": 1, "condition": "structured_v1", "outcome": "invalid_action", "rwe": 0.0, "ew": 0.0},
        {"seed": 2, "condition": "baseline_v1", "outcome": "timeout", "rwe": 0.0, "ew": 0.0},  # no structured
    ]
    res = I.analyse(rows)
    h1, h3 = res["primary"]["H1"], res["primary"]["H3"]
    assert h1["pairs"] == [{"seed": 1, "baseline": 0.4, "structured": 0.45, "d": pytest.approx(0.05), "n_baseline": 2, "n_structured": 2}]
    assert h1["unpaired"] == [{"seed": 2, "missing": "structured_v1", "n_baseline": 1, "n_structured": 0}]
    assert h3["pairs"][0]["d"] == 0  # 1/2 agreed in both conditions
    # §8.11: the invalid action is removed; structured's mean is then over its one agreed negotiation.
    s = res["invalid_action_sensitivity"]["H1"]
    assert s["pairs"][0]["structured"] == 0.9 and s["pairs"][0]["n_structured"] == 1
    assert res["outcomes_by_condition"]["structured_v1"]["invalid_action"] == 1


def test_an_instance_left_with_no_negotiations_drops_out_of_the_sensitivity_analysis_only():
    rows = [{"seed": 1, "condition": "baseline_v1", "outcome": "agreed", "rwe": 0.5, "ew": 0.5},
            {"seed": 1, "condition": "structured_v1", "outcome": "invalid_action", "rwe": 0.0, "ew": 0.0}]
    res = I.analyse(rows)
    assert res["primary"]["H1"]["n_pairs"] == 1
    assert res["invalid_action_sensitivity"]["H1"]["n_pairs"] == 0
    assert res["invalid_action_sensitivity"]["H1"]["unpaired"][0]["missing"] == "structured_v1"


@pytest.mark.parametrize("row,match", [
    ({"outcome": "walked_away", "rwe": 0.3}, "must be 0"),        # a failure scored non-zero
    ({"outcome": "agreed", "rwe": None}, "missing"),               # an agreement without a value
    ({"outcome": "transport_failure", "rwe": 0.0}, "not a strategic outcome"),  # aborts are never outcomes
])
def test_rows_that_break_the_frozen_scoring_are_refused(row, match):
    with pytest.raises(ValueError, match=match):
        I.analyse([{"seed": 1, "condition": "baseline_v1", "ew": 0.0, **row}])


# ------------------------------------------------------------ 7. Holm changes the interpretation
def test_holm_correction_by_hand():
    adj = I.holm({"H1": 0.01, "H2": 0.04, "H3": 0.03})
    # Sorted 0.01, 0.03, 0.04: 3*0.01 = 0.03; max(0.03, 2*0.03) = 0.06; max(0.06, 1*0.04) = 0.06.
    assert {k: round(v["p_holm"], 10) for k, v in adj.items()} == {"H1": 0.03, "H2": 0.06, "H3": 0.06}
    assert [adj[k]["reject"] for k in ("H1", "H2", "H3")] == [True, False, False]  # raw p would reject all three
    assert I.holm({"H1": None, "H2": 0.02, "H3": 0.5}) == {"H1": {"p_holm": None, "reject": False},
                                                          "H2": {"p_holm": 0.04, "reject": True}, "H3": {"p_holm": 0.5, "reject": False}}


def test_holm_turns_a_raw_significance_into_a_non_significance_end_to_end():
    # Exact sign-flip p for H1 is 0.0386 (by enumeration below); H2 and H3 have no effect (p = 1),
    # so Holm multiplies H1's p by 3: 0.116, no longer significant.
    d = [0.11, 0.09, -0.05, 0.07, 0.10, -0.04, 0.07, 0.05, 0.09, -0.06, 0.05, 0.04]
    assert exact_sign_flip_p(d) == pytest.approx(0.0386, abs=5e-4)
    rows = rows_for(d)
    for r in rows:
        r["ew"] = 0.5  # no EW effect
    res = I.analyse(rows)["primary"]
    raw, h1 = res["H1"]["permutation"]["p"], res["H1"]["permutation"]
    assert abs(raw - exact_sign_flip_p(d)) <= mc_tolerance(0.0386) and raw < 0.05
    assert res["H2"]["permutation"]["p"] == res["H3"]["permutation"]["p"] == 1.0
    assert h1["p_holm"] == pytest.approx(3 * raw) and h1["p_holm"] > 0.05 and not h1["reject"]
    assert "not statistically significant after Holm correction" in I.interpret(res["H1"])


# ------------------------------------------------------------ 8. nested repeats are not independent
def test_repeated_negotiations_are_averaged_within_their_instance():
    many = I.analyse(rows_for([0.1, -0.05, 0.2], per_condition=4))["primary"]["H1"]
    one = I.analyse(rows_for([0.1, -0.05, 0.2], per_condition=1))["primary"]["H1"]
    assert many["n_pairs"] == one["n_pairs"] == 3          # 24 negotiations are 3 units, not 24
    assert [p["d"] for p in many["pairs"]] == pytest.approx([p["d"] for p in one["pairs"]])
    assert many["permutation"]["p"] == one["permutation"]["p"]  # repeats add no false precision


# ------------------------------------------------------------ reproducibility
def test_the_analysis_is_reproducible_and_records_its_seed():
    rng = random.Random(3)
    rows = rows_for([rng.uniform(-0.3, 0.3) for _ in range(15)])  # continuous: resample means are distinct
    a, b = I.analyse(rows), I.analyse(rows)
    assert a == b and a["seed"] == I.ANALYSIS_SEED
    other = I.analyse(rows, seed=1)
    assert other["primary"]["H1"]["mean_difference"] == a["primary"]["H1"]["mean_difference"]
    assert other["primary"]["H1"]["bootstrap"] != a["primary"]["H1"]["bootstrap"]  # resampling depends on the seed


# ------------------------------------------------------------ the §8.9 lock in the dashboard
def _walked(seed, cond, claude_seat="A", first_mover="A"):
    from src.negotiation.protocol import NegotiationRecord
    from dashboard.data import APPROVED_PAIRING  # the §8.15 snapshots, as agents record them
    a, b = APPROVED_PAIRING if claude_seat == "A" else APPROVED_PAIRING[::-1]
    return NegotiationRecord(instance_seed=seed, method=cond, model_A=a, model_B=b, first_mover=first_mover,
                             outcome="walked_away", num_rounds=1, final_allocation=None, utility_A=0.0, utility_B=0.0,
                             total_welfare=0.0, optimal_welfare=120.0, max_rounds=10)


def _design():
    """The preregistered full run: every seed x condition x Claude seat x first mover once (480)."""
    return [_walked(s, c, seat, fm) for s in range(40000, 40060) for c in ("baseline_v1", "structured_v1")
            for seat in "AB" for fm in "AB"]


@pytest.mark.parametrize("mode,n,expect", [("full", 480, True), ("full", 479, False), ("pilot", 24, False)])
def test_inference_runs_only_on_the_complete_full_run(mode, n, expect):
    from dashboard import data as D
    records = _design()[:n]
    rows = [D.summarize(r, str(i), mode) for i, r in enumerate(records)]
    view = D.dataset_view(rows, records, mode)
    assert ("inference" in view) is expect
    if expect:
        assert view["inference"]["primary"]["H3"]["n_pairs"] == 60 and view["inference"]["primary"]["H3"]["permutation"]["p"] == 1.0



@pytest.mark.parametrize("mutate", ["duplicate", "foreign_seed"])
def test_more_rows_or_foreign_seeds_do_not_unlock_the_full_run(mutate):
    """480+ rows are not enough: the exact design (8 per seed 40000-40059) must be present."""
    from dashboard import data as D
    records = _design()
    if mutate == "duplicate":
        records.append(_walked(40000, "baseline_v1"))
    else:
        records[0] = _walked(99999, "baseline_v1")
    rows = [D.summarize(r, str(i), "full") for i, r in enumerate(records)]
    view = D.dataset_view(rows, records, "full")
    assert view["locked"] and "inference" not in view


@pytest.mark.parametrize("mutate", ["one_condition", "cell_swapped_for_duplicate", "unbalanced_conditions", "no_claude_seat",
                                    "gemini_opponent", "mixed_claude_ids", "unapproved_snapshots", "other_round_limit"])
def test_counts_alone_do_not_unlock_the_full_run(mutate):
    """Review finding: 480 rows with 8 per seed but a missing or duplicated design cell must stay locked."""
    from dashboard import data as D
    records = _design()
    if mutate == "one_condition":  # 8 per seed, all baseline
        records = [_walked(r.instance_seed, "baseline_v1", "A" if r.model_A.startswith("claude") else "B", r.first_mover) for r in records]
    elif mutate == "cell_swapped_for_duplicate":  # one cell missing, its neighbour twice
        records[1] = _walked(40000, "baseline_v1", "A", "A")
    elif mutate == "unbalanced_conditions":  # 5 baseline / 3 structured on one seed
        records[4] = _walked(40000, "baseline_v1", "A", "B")
    else:
        from dataclasses import replace
        if mutate == "gemini_opponent":  # one consistent pairing, but the opponent is not the GPT model
            records = [replace(r, **({"model_B": "gemini:z"} if r.model_A.startswith("claude") else {"model_A": "gemini:z"}))
                       for r in records]
        elif mutate == "unapproved_snapshots":  # one consistent Claude-vs-GPT pairing, but not the §8.15 snapshots
            other = {"claude:claude-sonnet-4-6": "claude:claude-3-haiku", "openai:gpt-4.1-2025-04-14": "openai:gpt-4o"}
            records = [replace(r, model_A=other[r.model_A], model_B=other[r.model_B]) for r in records]
        elif mutate == "other_round_limit":  # §8.15 fixes MAX_ROUNDS = 10 for every full-run negotiation
            records = [replace(r, max_rounds=5) for r in records]
        else:
            swap = {"no_claude_seat": {"model_A": "mock:a", "model_B": "mock:b"},  # mock rows labelled full
                    "mixed_claude_ids": {"model_A": "claude:other-snapshot"}}[mutate]  # seat A is Claude in records[0]
            records[0] = replace(records[0], **swap)
    rows = [D.summarize(r, str(i), "full") for i, r in enumerate(records)]
    assert len(rows) == 480 and set(__import__("collections").Counter(r["seed"] for r in rows).values()) == {8}
    assert not D.full_design_complete(rows)
    view = D.dataset_view(rows, records, "full")
    assert view["locked"] and "inference" not in view


def test_cached_analysis_equals_the_analysis_and_cannot_be_corrupted():
    """Review M2: dashboards poll; unchanged data must not redo the resampling, and a caller's edits must not leak."""
    rows = [{"seed": s, "condition": c, "outcome": "agreed", "rwe": 0.5 + 0.01 * s * (c == I.STRUCTURED), "ew": 0.3}
            for s in range(6) for c in (I.BASELINE, I.STRUCTURED)]
    I._analyse_key.cache_clear()
    first = I.analyse_cached(rows)
    assert first == I.analyse(rows)
    first["primary"]["H1"]["permutation"]["p"] = -1  # a caller mutating its copy
    again = I.analyse_cached([dict(r, extra="ignored") for r in rows])
    assert I._analyse_key.cache_info().hits == 1 and again["primary"]["H1"]["permutation"]["p"] > 0

