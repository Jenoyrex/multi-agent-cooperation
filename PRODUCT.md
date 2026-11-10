# Product

Inferred from the 2026-10-30 redesign brief, without an interview: the brief was exhaustive and asked for no routine approvals. Assumptions are marked *(assumed)*.

## What it is
Cooperation Lab: a zero-cost sandbox for testing how LLMs negotiate: how they respond to resources, share them and bargain. People connect local LLMs through Ollama or configure rule-based agents, define the resources those agents divide, run simulations on the frozen negotiation engine, and read automatically computed behavioural analytics. A separate, read-only Research area holds the preregistered confirmatory study.

## Who uses it
University students, researchers and non-technical users. None should need Python, APIs, databases or statistics to finish the ordinary path: add agents → define resources → run an experiment → read the analysis → export.

## Mode
Operate (app UI). Research pages (Methodology, Results) lean Read.

## Visual direction (pinned by the brief)
Apple-inspired research software: restrained, typographic, quiet depth, whitespace over borders, one accent for action and selection. No glassmorphism-everywhere, no gradients, no decorative icons, no generic AI imagery. Light and dark are both first-class; the default follows the system. *(assumed: system font on Apple devices, Inter self-hosted elsewhere, JetBrains Mono for data only.)*

## Non-negotiable truths
- Budget ₹0. No paid or external model calls, ever, including during tests.
- Nothing fabricated: every number comes from stored negotiations; empty means empty.
- Rule-based and simulated results are labelled as such and never presented as Claude, GPT or Gemini evidence.
- Uploaded Python is inspected (parsed), never executed.
- Workbench experiments never touch the frozen research environment, seeds, prompts, hypotheses, metrics or `results/`.
- Exploratory comparisons say so; confirmatory analysis stays locked to the complete preregistered design.
