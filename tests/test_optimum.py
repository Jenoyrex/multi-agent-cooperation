from src.environment.optimum import compute_optimal_welfare


def test_optimum_hand_example_no_ties():
    pool = {"widgets": 10, "gadgets": 20}
    valuation_A = {"widgets": 2.0, "gadgets": 0.5}  # A values widgets more
    valuation_B = {"widgets": 0.5, "gadgets": 2.0}  # B values gadgets more
    alloc, welfare = compute_optimal_welfare(pool, valuation_A, valuation_B)

    assert alloc.a_A == {"widgets": 10, "gadgets": 0}
    assert alloc.a_B == {"widgets": 0, "gadgets": 20}
    # 10*2.0 (A's widgets) + 20*2.0 (B's gadgets) = 20 + 40 = 60
    assert welfare == 60.0


def test_optimum_tie_case_does_not_crash_and_welfare_is_correct():
    pool = {"widgets": 10}
    valuation_A = {"widgets": 1.0}
    valuation_B = {"widgets": 1.0}  # exact tie
    alloc, welfare = compute_optimal_welfare(pool, valuation_A, valuation_B)

    # Tie resolves to A by convention; welfare is unaffected by who wins the tie.
    assert alloc.a_A == {"widgets": 10}
    assert alloc.a_B == {"widgets": 0}
    assert welfare == 10.0


def test_optimum_is_upper_bound_vs_even_split():
    """Sanity property: the computed optimum should never be lower than
    a naive even split's welfare, given each agent's own valuation."""
    pool = {"widgets": 10, "gadgets": 10}
    valuation_A = {"widgets": 3.0, "gadgets": 1.0}
    valuation_B = {"widgets": 1.0, "gadgets": 3.0}

    _, optimal_welfare = compute_optimal_welfare(pool, valuation_A, valuation_B)

    even_split_welfare = (
        5 * valuation_A["widgets"] + 5 * valuation_A["gadgets"]
        + 5 * valuation_B["widgets"] + 5 * valuation_B["gadgets"]
    )
    assert optimal_welfare >= even_split_welfare
