"""Bring-your-own-key: error messages, key save modes, forget, usage per agent, live spend, demo lockout.

Every provider path runs against the fake server on 127.0.0.1 or a stub exception: no paid call is made.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dashboard import demo
from sandbox import experiment as X, keys, providers
from tests.sandbox_fake import TOKENS
from workbench import simulations as S
from tests.test_sandbox_models import (DUMMY_KEY, _search, agent, connection, consented, design, fake, http, isolated,  # noqa: F401 — fixtures
                                       run, server)

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- errors: specific, actionable, never the key
@pytest.mark.parametrize("model,kind", [
    ("unauthorized", "invalid_key"), ("bad-key-400", "invalid_key"), ("quota", "no_credit"), ("credit", "no_credit"),
    ("forbidden", "forbidden"), ("limited", "rate_limited"), ("exhausted", "rate_limited"), ("missing", "model_not_found"),
    ("server-error", "provider_error")])
def test_each_provider_failure_gets_its_own_message_and_no_key(fake, model, kind):
    conn = connection(fake, tier="api")
    keys.set_key(conn, DUMMY_KEY)
    r = providers.test_connection(conn, model)
    assert not r["ok"] and r["kind"] == kind and r["title"] and r["hint"]
    assert DUMMY_KEY not in json.dumps(r)


def test_billing_hint_points_to_the_providers_billing_page():
    class Quota(Exception):
        status_code, body = 429, {"error": {"code": "insufficient_quota"}}
    why = providers.classify(Quota("You exceeded your current quota"), providers.resolve("openai"))
    assert why["kind"] == "no_credit" and providers.KEY_HELP["openai"]["billing"] in why["hint"] and "subscription" in why["hint"]


def test_a_stopped_local_server_says_how_to_start_it():
    class APIConnectionError(Exception):
        pass
    for cid, words in (("ollama", "ollama serve"), ("lmstudio", "LM Studio")):
        why = providers.classify(APIConnectionError("Connection error."), providers.resolve(cid))
        assert why["kind"] == "local_not_running" and words in why["hint"]


def test_timeout_and_network_are_told_apart(fake):
    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass
    assert providers.classify(APITimeoutError("timed out"), providers.resolve("openai"))["kind"] == "timeout"
    assert providers.classify(APIConnectionError("Connection error."), providers.resolve("openai"))["kind"] == "network"


def test_a_run_that_fails_explains_why_in_plain_words(fake):
    conn = connection(fake, tier="api")
    keys.set_key(conn, DUMMY_KEY)
    doc = run(consented(design(agent(conn, "quota"), caps={"max_tokens": 100_000})))
    aborted = [t for t in doc["trials"] if t["status"] == "aborted"]
    assert aborted and "No credit" in aborted[0]["detail"] and DUMMY_KEY not in json.dumps(doc)


# ---------------------------------------------------------------- the "how to get your key" panel
def test_every_api_preset_links_its_official_console_and_pricing_and_states_the_subscription_fact():
    for cid in ("openai", "anthropic", "gemini"):
        h = providers.public(providers.resolve(cid))["key_help"]
        assert h["console"].startswith("https://") and h["pricing"].startswith("https://") and h["billing"].startswith("https://")
    assert "does not include an API key" in providers.SUBSCRIPTION_NOTE
    assert providers.public(providers.resolve("ollama"))["key_help"] is None


# ---------------------------------------------------------------- save modes
def test_session_only_is_the_default_and_writes_nothing(server, tmp_path):
    code, text = http(server, "/api/models/keys", {"connection": "openai", "key": DUMMY_KEY})
    assert code == 200 and json.loads(text)["key_saved"] is False
    assert not (tmp_path / ".env").exists() and keys.get("openai") == DUMMY_KEY


def test_save_to_env_writes_one_line_and_can_be_removed(server, tmp_path):
    code, text = http(server, "/api/models/keys", {"connection": "anthropic", "key": DUMMY_KEY, "save": "env_file"})
    assert code == 200 and DUMMY_KEY not in text and json.loads(text)["key_saved"] is True
    assert (tmp_path / ".env").read_text().strip() == f"ANTHROPIC_API_KEY={DUMMY_KEY}"
    keys.forget_all()  # a restart: the key comes back from the file
    assert keys.get("anthropic") == DUMMY_KEY and "ANTHROPIC_API_KEY" not in os.environ
    code, text = http(server, "/api/models/keys/delete", {"connection": "anthropic", "from_env_file": True})
    assert code == 200 and "ANTHROPIC_API_KEY" not in (tmp_path / ".env").read_text()
    keys.forget_all()
    assert keys.get("anthropic") == ""


def test_a_custom_connection_key_saves_under_its_own_name(fake, tmp_path):
    conn = connection(fake, tier="api")
    keys.save_to_env(conn, DUMMY_KEY)
    keys.forget_all()
    assert keys.get(conn) == DUMMY_KEY and keys.env_name(conn).startswith("COOPERATION_LAB_KEY_")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_saving_into_a_repository_needs_env_to_be_ignored(server, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    code, text = http(server, "/api/models/keys", {"connection": "openai", "key": DUMMY_KEY, "save": "env_file"})
    assert code == 400 and "not ignored by git" in text and not (tmp_path / ".env").exists() and keys.get("openai") == ""
    (tmp_path / ".gitignore").write_text(".env\n")
    assert http(server, "/api/models/keys", {"connection": "openai", "key": DUMMY_KEY, "save": "env_file"})[0] == 200


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_this_repositorys_env_is_gitignored():
    assert subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", ".env"]).returncode == 0


def test_forget_all_hides_entered_and_env_keys_for_this_session(server, tmp_path):
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={DUMMY_KEY}\n")
    keys.set_key("openai", DUMMY_KEY)
    code, text = http(server, "/api/models/keys/forget", {})
    assert code == 200 and DUMMY_KEY not in text
    assert keys.get("openai") == "" and keys.get("gemini") == ""
    assert keys.redact(DUMMY_KEY) == "[redacted key]"  # still redacted while hidden


def test_a_bad_save_mode_is_refused(server):
    assert http(server, "/api/models/keys", {"connection": "openai", "key": DUMMY_KEY, "save": "cloud"})[0] == 400


# ---------------------------------------------------------------- usage per agent, live spend, cap
def test_tokens_are_reported_per_agent_and_live_spend_matches(fake):
    conn = connection(fake, tier="api")
    keys.set_key(conn, DUMMY_KEY)
    doc = run(consented(design(agent(conn), caps={"max_tokens": 100_000})))
    t = doc["trials"][0]
    by = t["usage_by_agent"]
    assert by["Y"]["api_calls"] == 0 and by["X"]["api_calls"] == t["usage"]["api_calls"] > 0
    assert by["X"]["input_tokens"] + by["X"]["output_tokens"] == doc["spend"]["tokens"]
    assert doc["spend"]["token_cap"] == 100_000


def test_the_cap_stops_cleanly_and_spend_stays_under_it(fake):
    conn = connection(fake, tier="api")
    keys.set_key(conn, DUMMY_KEY)
    aid = agent(conn)
    cap = X.estimate(S.validate(design(aid, repetitions=20)))["reserve_per_call"] + 3 * sum(TOKENS)
    doc = run(consented(design(aid, repetitions=20, caps={"max_tokens": cap})))
    assert doc["status"] == "cancelled" and "cap" in (doc["stop_reason"] or "")
    assert 0 < doc["spend"]["tokens"] <= cap and doc["spend"]["token_cap"] == cap


# ---------------------------------------------------------------- public demo
def test_public_demo_has_no_key_or_model_route(server, monkeypatch):
    monkeypatch.setenv(demo.ENV, "1")
    for path, body in (("/api/models/keys", {"connection": "openai", "key": DUMMY_KEY}), ("/api/models/keys/forget", {}),
                       ("/api/models/keys/delete", {"connection": "openai"}), ("/api/models/test", {"connection": "openai", "model": "x"}),
                       ("/api/models/list", {"connection": "openai"})):
        assert http(server, path, body)[0] == 405
    assert http(server, "/api/models/connections")[0] in (403, 404, 405)
    assert keys.get("openai") == ""


def test_help_page_links_are_the_same_official_pages():
    app = (REPO / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    for cid in ("openai", "anthropic", "gemini"):
        for kind in ("console", "billing", "pricing"):
            assert providers.KEY_HELP[cid][kind] in app, (cid, kind)
    assert "A chat subscription is not an API key." in app
