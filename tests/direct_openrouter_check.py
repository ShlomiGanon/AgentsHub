"""
Standalone OpenRouter sanity check — bypasses crewai/LiteLLM/this codebase
entirely. Talks directly to OpenRouter's HTTP API to isolate whether the
key itself works, independent of anything in agents/adapter.py or how
crewai routes requests.

Usage (PowerShell):
    $env:CORE_MODEL_KEY="sk-or-v1-your-real-key-here"
    python direct_openrouter_check.py

Usage (bash):
    export CORE_MODEL_KEY="sk-or-v1-your-real-key-here"
    python direct_openrouter_check.py
"""

import os
import sys
import json
import urllib.request
import urllib.error

API_KEY = os.environ.get("CORE_MODEL_KEY", "").strip()
MODEL = "meta-llama/llama-3.1-8b-instruct:free"
URL = "https://openrouter.ai/api/v1/chat/completions"

if not API_KEY:
    print("FAIL: CORE_MODEL_KEY is not set in the environment.")
    print("Set it first, e.g.:")
    print('  $env:CORE_MODEL_KEY="sk-or-v1-..."   (PowerShell)')
    print('  export CORE_MODEL_KEY="sk-or-v1-..." (bash)')
    sys.exit(1)

print("=== Direct OpenRouter API check (no crewai, no LiteLLM) ===")
print(f"Key (masked): {'*' * max(len(API_KEY) - 6, 0)}{API_KEY[-6:]}  ({len(API_KEY)} chars)")
print(f"Model: {MODEL}")
print()

payload = {
    "model": MODEL,
    "messages": [
        {"role": "user", "content": "What are you? Answer in one short sentence."}
    ],
}

request = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    },
    method="POST",
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        status = response.status
        body = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    print(f"FAIL: HTTP {e.code} {e.reason}")
    print("Response body:")
    print(error_body)
    print()
    if e.code == 401:
        print("A 401 here means OpenRouter itself is rejecting the key —")
        print("this confirms the problem is the key/account, not this")
        print("codebase's routing or crewai/LiteLLM configuration.")
    sys.exit(1)
except urllib.error.URLError as e:
    print(f"FAIL: network error — {e.reason}")
    sys.exit(1)

print(f"SUCCESS: HTTP {status}")
print()
print("Model's response:")
print(body["choices"][0]["message"]["content"])
print()
print("Full raw response (for reference):")
print(json.dumps(body, indent=2, ensure_ascii=False))