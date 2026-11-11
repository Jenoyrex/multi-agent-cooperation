# References

**VERIFIED** = the page was opened and resolved on the date shown, and it says what we use it for.
**TO CONFIRM** = not checked this time, or quoted from memory. Check it before you rely on it.

## Provider pages used in the app (checked 2026-11-11)

These links appear in the app and the README. They are also in `sandbox/providers.py` (`KEY_HELP`), and a test keeps
the Help page in step with that file.

| Provider | What | URL | Status |
|---|---|---|---|
| OpenAI | API keys page | https://platform.openai.com/api-keys | VERIFIED (linked from OpenAI's API quickstart) |
| OpenAI | Billing | https://platform.openai.com/account/billing/overview | VERIFIED (linked from the quickstart) |
| OpenAI | Pricing | https://developers.openai.com/api/docs/pricing | VERIFIED (resolves; platform.openai.com/docs/pricing redirects here) |
| OpenAI | ChatGPT and the API are billed separately | https://help.openai.com/en/articles/8156019 | VERIFIED by search result; the page blocks scripted requests, so TO CONFIRM in a browser |
| Anthropic | API keys (Claude Console) | https://platform.claude.com/settings/keys | VERIFIED (console.anthropic.com redirects here) |
| Anthropic | Billing | https://platform.claude.com/settings/billing | VERIFIED (resolves) |
| Anthropic | Pricing | https://platform.claude.com/docs/en/about-claude/pricing | VERIFIED (docs.claude.com redirects here) |
| Anthropic | Why API use is billed separately from a paid Claude plan | https://support.claude.com/en/articles/9876003 | VERIFIED (resolves; title says so) |
| Google | Gemini API key (Google AI Studio) | https://aistudio.google.com/apikey | VERIFIED (linked from Google's API key docs) |
| Google | Billing | https://ai.google.dev/gemini-api/docs/billing | VERIFIED (resolves; two tiers, free and paid) |
| Google | Pricing (free tier and paid tier) | https://ai.google.dev/gemini-api/docs/pricing | VERIFIED (resolves). Free-tier limits change: never quoted here |
| Ollama | Download | https://ollama.com/download | VERIFIED |
| LM Studio | Local server | https://lmstudio.ai/docs/developer/core/server | VERIFIED |

Some third-party sites said in 2026 that Anthropic paid plans now include API credit. Anthropic's own help page, above,
says API use is billed separately. We follow the official page. **TO CONFIRM** whether any plan now includes API credit.

## Provider base URLs and structured output

Listed with their sources in `docs/sandbox-v2-plan.md` (Ollama, LM Studio, OpenAI, Gemini OpenAI-compatible endpoint, Groq,
OpenRouter). Checked when sandbox v2 was built; **TO CONFIRM** again before a paid run.

## Statistics used by the study (docs/spec.md §8.9)

The methods are standard. The citations below are from memory, not checked this time: **TO CONFIRM**.

- Holm correction: S. Holm (1979), "A simple sequentially rejective multiple test procedure", *Scandinavian Journal of Statistics*.
- Wilcoxon signed-rank test: F. Wilcoxon (1945), "Individual comparisons by ranking methods", *Biometrics Bulletin*.
- Bootstrap percentile interval: B. Efron and R. Tibshirani (1993), *An Introduction to the Bootstrap*.
- Sign-flip (randomisation) test for paired differences: any standard text on permutation tests.

## Prices used by the study's cost estimate

`src/experiments/config.py` (`APPROVED_PRICES`, frozen): `claude-sonnet-4-6` $3 in / $15 out and `gpt-4.1-2025-04-14`
$2 in / $8 out, per million tokens. **TO CONFIRM** against each provider's pricing page on the day of a run: these are
the rates the study approved, not live prices.
