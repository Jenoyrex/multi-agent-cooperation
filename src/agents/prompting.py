"""Shared prompt construction for real-model agents. Kept in one place so
Claude and GPT agents are given information-equivalent prompts — a
difference in prompt wording between the two model wrappers would be a
confound in any cross-model comparison (see spec.md, Research Discipline
note in the original project brief)."""
from __future__ import annotations

from src.agents.base import AgentView

SYSTEM_INSTRUCTIONS = """You are negotiating on behalf of Agent {role} in a two-party \
resource-split negotiation game. There is a fixed pool of resources, split into \
categories. You and the other agent (Agent {other_role}) must agree on how to split \
every unit of every category between you. Nothing is allocated unless you reach \
agreement; if you fail to agree within the round limit, you both get nothing.

You have a PRIVATE valuation: you know exactly how many points each unit of each \
category is worth to you. You do NOT know the other agent's valuation — they may \
value categories very differently from you, which means there may be trades that \
make you both better off compared to an even split.

Rules:
- Turns alternate between you and the other agent.
- On your turn you must either: propose a full allocation (OFFER), accept the most \
recent offer on the table exactly as it stands (ACCEPT), or end the negotiation with \
no agreement (WALK_AWAY).
- An OFFER must allocate every unit of every category between the two agents (no \
leftover units).
- You cannot ACCEPT on the very first turn of the negotiation (nothing has been \
offered yet).
- There are {max_rounds} total rounds (one round = one agent's turn). The final \
round is the deadline: on the final round you may only ACCEPT the offer on the table \
or WALK_AWAY. If you make an OFFER on the final round, it cannot be answered, so the \
negotiation ends at the deadline with no agreement and both agents receive zero value.
- If no offer is accepted by the end of round {max_rounds}, the negotiation ends with \
no agreement and both agents receive zero value.

Respond with exactly one structured action per turn, matching the required schema. \
The `message` field is a short, externally-visible statement to the other agent \
(not private reasoning) — keep it brief."""


# Instruction-strategy conditions (NOT protocol variants: the engine, schema,
# user-turn prompt and information given are identical). A variant only
# appends a fixed block to the system instructions; baseline_v1 appends
# nothing, so its text is exactly SYSTEM_INSTRUCTIONS.
#
# structured_v1 is one package of three components: (1) preference ranking/
# revelation, (2) integrative trade guidance, (3) disagreement-point/deadline
# reasoning. Effects are attributable to the package, not to one component.
# Deliberately no fairness instruction.
STRUCTURED_V1_BLOCK = """Negotiation procedure (follow these steps):
1. Preference ranking: in your `message`, state which categories matter most \
and least to you (a ranking is enough), and ask the other agent for their ranking.
2. Integrative trades: build offers that give the other agent more of the \
categories you value less, in exchange for more of the categories you value more.
3. Disagreement point and deadline: before you WALK_AWAY, or before the final \
round passes without an accepted offer, compare the offer on the table with the \
zero value you both receive if there is no agreement."""

INSTRUCTION_VARIANTS = {
    "baseline_v1": None,
    "structured_v1": STRUCTURED_V1_BLOCK,
}
BASELINE_VARIANT = "baseline_v1"


def check_variant(variant: str) -> str:
    if variant not in INSTRUCTION_VARIANTS:
        raise ValueError(f"Unknown instruction variant {variant!r}; "
                         f"expected one of {sorted(INSTRUCTION_VARIANTS)}")
    return variant


def build_prompt(view: AgentView) -> str:
    other_role = "B" if view.role == "A" else "A"
    pool_str = ", ".join(f"{cat}: {qty} units" for cat, qty in view.resource_pool.items())
    valuation_str = ", ".join(
        f"{cat}: {v:.2f} points/unit" for cat, v in view.own_valuation.items()
    )

    if view.transcript_so_far:
        lines = []
        for turn in view.transcript_so_far:
            a = turn.action
            if a.action_type == "OFFER":
                offer_str = ", ".join(
                    f"{cat}: A gets {a.allocation['A'].get(cat, 0)}, B gets {a.allocation['B'].get(cat, 0)}"
                    for cat in view.resource_pool
                ) if a.allocation else "(malformed offer)"
                lines.append(f"Round {turn.turn_number}, Agent {turn.actor} OFFERED: {offer_str}")
            elif a.action_type == "ACCEPT":
                lines.append(f"Round {turn.turn_number}, Agent {turn.actor} ACCEPTED the offer on the table.")
            else:
                lines.append(f"Round {turn.turn_number}, Agent {turn.actor} WALKED AWAY.")
            if a.message:
                lines.append(f'  message: "{a.message}"')
        transcript_str = "\n".join(lines)
    else:
        transcript_str = "(no offers made yet — you are moving first)"

    final_turn_note = (
        "\nThis is the FINAL round (the deadline): you may only ACCEPT the offer on "
        "the table or WALK_AWAY. An OFFER now ends the negotiation with no agreement.\n"
        if view.rounds_remaining == 0 else ""
    )

    return (
        f"Resource pool: {pool_str}\n"
        f"Your private valuation: {valuation_str}\n"
        f"Round {view.round_number} of {view.max_rounds} "
        f"({view.rounds_remaining} remaining after this one).\n"
        f"{final_turn_note}\n"
        f"Negotiation so far:\n{transcript_str}\n\n"
        f"It is your turn, Agent {view.role}. Respond with your action."
    )


def system_instructions(view: AgentView, variant: str = BASELINE_VARIANT) -> str:
    other_role = "B" if view.role == "A" else "A"
    text = SYSTEM_INSTRUCTIONS.format(role=view.role, other_role=other_role, max_rounds=view.max_rounds)
    block = INSTRUCTION_VARIANTS[check_variant(variant)]
    return text if block is None else f"{text}\n\n{block}"
