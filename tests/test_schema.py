import pytest
from pydantic import ValidationError

from src.agents.schema import NegotiationAction


def test_valid_offer_parses():
    action = NegotiationAction(
        action_type="OFFER",
        allocation={"A": {"widgets": 5}, "B": {"widgets": 5}},  # two-sided form
        message="Here's my offer.",
    )
    assert action.action_type == "OFFER"
    assert action.allocation["A"]["widgets"] == 5


def test_valid_accept_parses_without_allocation():
    action = NegotiationAction(action_type="ACCEPT")
    assert action.allocation is None


def test_invalid_action_type_rejected():
    with pytest.raises(ValidationError):
        NegotiationAction(action_type="MAYBE")


def test_message_optional():
    action = NegotiationAction(action_type="WALK_AWAY")
    assert action.message is None
