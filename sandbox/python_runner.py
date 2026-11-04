"""The child process of a Python file agent. Started by sandbox/python_agent.py, never imported by the server.

Protocol: one JSON object per line. The parent sends a view; this answers {"action": ...} or {"error": ...}. The agent's
own prints go to stderr, so they cannot be mistaken for an answer. A returned NegotiationAction is two-sided; a returned
dict uses the single-sided "my units" form that HTTP agents use.
"""
import asyncio
import importlib.util
import json
import sys


def main() -> None:
    path, class_name = sys.argv[1], sys.argv[2]
    channel, sys.stdout = sys.stdout, sys.stderr
    from sandbox.engine import SandboxView
    from src.agents.base import TranscriptTurn
    from src.agents.parsing import parse_action
    from src.agents.schema import InvalidAgentOutputError, NegotiationAction

    spec = importlib.util.spec_from_file_location("user_agent", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, class_name)
    try:
        agent = cls()
    except TypeError:
        agent = cls("python-file")  # an Agent subclass that takes a name

    def answer(payload: dict) -> None:
        channel.write(json.dumps(payload) + "\n")
        channel.flush()

    answer({"ready": True})
    for line in sys.stdin:
        msg = json.loads(line)
        view = SandboxView(role=msg["role"], resource_pool=msg["resource_pool"], own_valuation=msg["own_valuation"],
                           transcript_so_far=[TranscriptTurn(t["turn"], t["actor"], NegotiationAction(**t["action"])) for t in msg["transcript"]],
                           round_number=msg["round_number"], rounds_remaining=msg["rounds_remaining"], max_rounds=msg["max_rounds"],
                           notices=tuple(msg.get("notices", ())))
        try:
            result = asyncio.run(agent.generate_response(view))
            if isinstance(result, dict):
                result = parse_action(view, result)
            if not isinstance(result, NegotiationAction):
                raise InvalidAgentOutputError(f"generate_response returned {type(result).__name__}, not a NegotiationAction or a dict")
            answer({"action": result.model_dump()})
        except InvalidAgentOutputError as exc:
            answer({"invalid": str(exc), "reason": exc.reason})
        except Exception as exc:  # noqa: BLE001 — the agent's bug is reported as its invalid output
            answer({"invalid": f"{type(exc).__name__}: {exc}"[:300], "reason": "malformed_output"})


if __name__ == "__main__":
    main()
