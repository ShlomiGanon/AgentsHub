"""A real, live, billed sanity check against an actual model API.

NOT part of the automated test suite and never collected by pytest: this
repo's `pytest.ini` sets `python_files = test_*.py`, and this file
deliberately does not match that glob (despite living in `tests/`, an
already-approved project directory — no new top-level directory needed).
Run it by hand, never from CI, never with a fake/mocked key:

    python tests/sanity_check_real_model_call.py

What this proves, end to end, with zero mocking and zero shortcuts —
literally the same functions production code calls, not a hand-rolled
stand-in for them:

    1. This script itself reads CORE_MODEL_PROVIDER / CORE_MODEL_NAME /
       CORE_MODEL_API_KEY_ENV from the real environment and calls
       config.base.build_tier_model(provider, model_name, api_key) — the
       exact same read-then-build sequence api.app.main/bot.app.main/
       cli.user_admin.main each do at their own boundary (config.base
       itself never reaches into the environment; see its module
       docstring) — then passes the result to
       config.base.load_base_config(core_model=...).
    2. orchestrator.main_agent.construct_core_agents(base_config) — the
       exact function orchestrator.flows.assemble_core_agents calls in
       production — builds a real MainAgent from the resolved tier.
    3. MainAgent.process(...) -> agents.base.Agent.process() ->
       agents.adapter.invoke() -> a real crewai.LLM(model=..., api_key=...)
       and a real crewai.Agent(...).kickoff(...) — a genuine outbound call.

Before running, set (in this shell, never committed anywhere):

    CORE_MODEL_PROVIDER=openrouter
    CORE_MODEL_NAME=<a real, ideally free-or-cheap OpenRouter model id>
    CORE_MODEL_API_KEY_ENV=<name of another env var that holds your key>
    <that other env var>=<your real OpenRouter API key>

This costs real money (however little, for one short prompt) and needs a
real key — it will not run, and must never be made to run, in CI or the
normal test suite.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.errors import AgentInvocationError
from config.base import ModelTierError, build_tier_model, load_base_config

PROMPT = "Reply with exactly the word: pong"

INTENT_CASES = (
    ("hello, thanks for your help", "conversational"),
    ("what is the current status at gate 3?", "question"),
    ("smoke is visible near gate 3", "report"),
    ("please dispatch a response to gate 3", "request"),
    ("there is smoke at gate 3, please dispatch a response", "request"),
    ("he said 'dispatch a response', but I am only reporting what he said", "report"),
    ("why did you not dispatch a response?", "question"),
    ("שלום, תודה על העזרה", "conversational"),
    ("מה היה האירוע האחרון?", "question"),
    ("יש עשן בשער 3", "report"),
    ("תשלח בבקשה צוות לשער 3", "request"),
)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise ModelTierError(f"required environment variable '{name}' is not set")
    return value


def production_check() -> int:
    print("=== AgentsHub real-model sanity check (live, billed API call) ===\n")

    print("[1/5] reading CORE_MODEL_* from the real environment and building the core TierModel...")
    try:
        provider = _require_env("CORE_MODEL_PROVIDER")
        model_name = _require_env("CORE_MODEL_NAME")
        api_key_env_name = _require_env("CORE_MODEL_API_KEY_ENV")
        api_key = _require_env(api_key_env_name)
        core_model = build_tier_model(provider, model_name, api_key)
        base_config = load_base_config(core_model=core_model)
    except ModelTierError as exc:
        print(f"\nFAIL at env / tier resolution: {exc}")
        print(
            "Check that CORE_MODEL_PROVIDER, CORE_MODEL_NAME, and CORE_MODEL_API_KEY_ENV are all "
            "set in this shell, and that the variable CORE_MODEL_API_KEY_ENV names is itself set."
        )
        return 1
    print(f"      resolved model:   {base_config.core_model.model}")
    key = base_config.core_model.api_key
    masked = f"{'*' * max(len(key) - 4, 0)}{key[-4:]}" if len(key) > 4 else "*" * len(key)
    print(f"      resolved api_key: {masked}  ({len(key)} chars)")

    print("\n[2/5] agents.adapter._get_crewai() — confirming the real package imports...")
    from agents.adapter import _get_crewai

    try:
        crewai_module = _get_crewai()
    except AgentInvocationError as exc:
        print(f"\nFAIL: crewai did not import: {exc}")
        return 1
    print(f"      crewai imported OK (version {getattr(crewai_module, '__version__', 'unknown')})")

    print("\n[3/5] orchestrator.main_agent.construct_core_agents(base_config) — the real production seam...")
    from orchestrator.main_agent import construct_core_agents

    core_agents = construct_core_agents(base_config)
    main_agent = core_agents["main_agent"]
    print(f"      constructed: {type(main_agent).__name__} (model={main_agent.model!r})")

    print(f"\n[4/5] main_agent.process({PROMPT!r}, []) — the real outbound call (agents.base -> agents.adapter -> crewai.LLM -> live API)...")
    try:
        result = main_agent.process(PROMPT, [])
    except AgentInvocationError as exc:
        print(f"\nFAIL: the real model call failed: {exc}")
        if exc.cause is not None:
            print(f"      underlying cause: {type(exc.cause).__name__}: {exc.cause}")
        print(
            "      Likely culprits: a bad/expired API key, a model name OpenRouter doesn't "
            "recognize, a provider string litellm can't route, or a network/connectivity issue."
        )
        return 1

    print(f"      status: {result.status}")
    print(f"      text:   {result.text!r}")

    print("\n[5/5] Verdict...")
    if result.status != "success" or not result.text.strip():
        print("FAIL: a response came back, but it was empty or not a normal 'success' result.")
        return 1

    print("PASS — real config, real tier resolution, real crewai.LLM, a real live API call, and a real response, all confirmed working.")
    return 0


def key_check() -> int:
    api_key = os.environ.get("CORE_MODEL_KEY", "").strip()
    if not api_key:
        print("FAIL: CORE_MODEL_KEY is not set.")
        return 1
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"SUCCESS: HTTP {response.status}")
            print(json.dumps(json.loads(response.read().decode("utf-8")), indent=2))
            return 0
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"FAIL: {exc}")
        return 1


def openrouter_check() -> int:
    api_key = os.environ.get("CORE_MODEL_KEY", "").strip()
    if not api_key:
        print("FAIL: CORE_MODEL_KEY is not set.")
        return 1
    payload = {
        "model": os.environ.get("CORE_MODEL_NAME", "meta-llama/llama-3.1-8b-instruct:free"),
        "messages": [{"role": "user", "content": "What are you? Answer in one short sentence."}],
    }
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
            print(f"SUCCESS: HTTP {response.status}")
            print(body["choices"][0]["message"]["content"])
            return 0
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"FAIL: {exc}")
        return 1


def crewai_check() -> int:
    api_key = os.environ.get("CORE_MODEL_KEY", "").strip()
    model_name = os.environ.get("CORE_MODEL_NAME", "poolside/laguna-s-2.1:free").strip()
    if not api_key:
        print("FAIL: CORE_MODEL_KEY is not set.")
        return 1


def intent_check() -> int:
    """Run a small billed evaluation through the production intent classifier."""

    try:
        provider = _require_env("CORE_MODEL_PROVIDER")
        model_name = _require_env("CORE_MODEL_NAME")
        api_key_env_name = _require_env("CORE_MODEL_API_KEY_ENV")
        core_model = build_tier_model(provider, model_name, _require_env(api_key_env_name))
        base_config = load_base_config(core_model=core_model)
    except ModelTierError as exc:
        print(f"FAIL at env / tier resolution: {exc}")
        return 1

    from orchestrator.main_agent import classify_intent, construct_core_agents
    from profiles.demo import PROTOCOLS

    main_agent = construct_core_agents(base_config)["main_agent"]
    failures = []
    for message, expected in INTENT_CASES:
        try:
            result = classify_intent(main_agent, tuple(PROTOCOLS), message)
            actual = result.intent
        except Exception as exc:
            actual = f"ERROR:{type(exc).__name__}:{exc}"
        marker = "PASS" if actual == expected else "FAIL"
        print(f"{marker}: expected={expected:<14} actual={actual:<30} message={message!r}")
        if actual != expected:
            failures.append((message, expected, actual))

    request_false_positives = [item for item in failures if item[1] != "request" and item[2] == "request"]
    print(f"\n{len(INTENT_CASES) - len(failures)}/{len(INTENT_CASES)} correct; request false positives={len(request_false_positives)}")
    return 1 if failures else 0
    try:
        from crewai import LLM

        llm = LLM(model=f"openrouter/{model_name}", api_key=api_key, base_url="https://openrouter.ai/api/v1")
        print(llm.call("What are you? Answer in one short sentence."))
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual live-model checks; never run from CI.")
    parser.add_argument("check", nargs="?", choices=("production", "intent", "key", "openrouter", "crewai"), default="production")
    selected = parser.parse_args(argv).check
    return {
        "production": production_check,
        "intent": intent_check,
        "key": key_check,
        "openrouter": openrouter_check,
        "crewai": crewai_check,
    }[selected]()


if __name__ == "__main__":
    sys.exit(main())
