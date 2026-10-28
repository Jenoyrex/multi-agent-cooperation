"""Workbench: an exploratory multi-agent experimentation layer on top of the frozen
negotiation engine. Agents (workbench/agents.py) and environments
(workbench/environments.py) are saved in their own SQLite file under sandbox_runs/,
simulations run the unchanged NegotiationSession, and nothing here reads or writes
the research database in results/. See docs/workbench.md.
"""
