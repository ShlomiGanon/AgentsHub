"""
Minimal crewai-only sanity check -- uses the real crewai.LLM class
directly, with no wrapping from this codebase (no MainAgent, no
build_tier_model, no agents/adapter.py). This isolates whether the
problem is in crewai/LiteLLM's own OpenRouter routing, versus something
in this codebase's own construction path.

Usage (PowerShell):
    $env:CORE_MODEL_KEY="sk-or-v1-your-real-key-here"
    python crewai_direct_check.py

    # optional -- override the model (defaults to a currently-live free one):
    $env:CORE_MODEL_NAME="poolside/laguna-s-2.1:free"

Usage (bash):
    export CORE_MODEL_KEY="sk-or-v1-your-real-key-here"
    python crewai_direct_check.py
"""

import os
import sys

API_KEY = os.environ.get("CORE_MODEL_KEY", "").strip()
MODEL_NAME = os.environ.get("CORE_MODEL_NAME", "poolside/laguna-s-2.1:free").strip()
MODEL_STRING = f"openrouter/{MODEL_NAME}"

if not API_KEY:
    print("FAIL: CORE_MODEL_KEY is not set in the environment.")
    print('  $env:CORE_MODEL_KEY="sk-or-v1-..."   (PowerShell)')
    print('  export CORE_MODEL_KEY="sk-or-v1-..." (bash)')
    sys.exit(1)

print("=== Minimal crewai.LLM check (real crewai, no codebase wrapping) ===")
print(f"Model string: {MODEL_STRING}")
print(f"Key (masked): {'*' * max(len(API_KEY) - 6, 0)}{API_KEY[-6:]}  ({len(API_KEY)} chars)")
print()

try:
    from crewai import LLM
except ImportError as e:
    print(f"FAIL: could not import crewai -- {e}")
    print("Is crewai installed in this environment? (pip show crewai)")
    sys.exit(1)

print("[1/2] Constructing crewai.LLM(...) directly, explicit api_key and base_url...")

llm = LLM(
    model=MODEL_STRING,
    api_key=API_KEY,
    base_url="https://openrouter.ai/api/v1",
)

print("      constructed OK")
print()
print("[2/2] Calling llm.call(...) with a simple prompt...")
print()

try:
    response = llm.call("What are you? Answer in one short sentence.")
except Exception as e:
    print(f"FAIL: the call raised an exception.")
    print(f"      type: {type(e).__name__}")
    print(f"      message: {e}")
    sys.exit(1)

print("SUCCESS")
print()
print("Model's response:")
print(response)