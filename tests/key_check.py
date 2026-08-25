"""
Checks the OpenRouter key itself, via OpenRouter's dedicated key-info
endpoint — this endpoint ONLY validates the key; it doesn't touch any
model, so it isolates "is this key valid at all" from "does this key
have access to this specific model."

Usage (PowerShell):
    $env:CORE_MODEL_KEY="sk-or-v1-your-real-key-here"
    python check_key_validity.py
"""

import os
import sys
import json
import urllib.request
import urllib.error

API_KEY = os.environ.get("CORE_MODEL_KEY", "").strip()
URL = "https://openrouter.ai/api/v1/auth/key"

if not API_KEY:
    print("FAIL: CORE_MODEL_KEY is not set.")
    sys.exit(1)

print("=== OpenRouter key validity check (auth/key endpoint) ===")
print(f"Key (masked): {'*' * max(len(API_KEY) - 6, 0)}{API_KEY[-6:]}  ({len(API_KEY)} chars)")
print()

request = urllib.request.Request(
    URL,
    headers={"Authorization": f"Bearer {API_KEY}"},
    method="GET",
)

try:
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
        print(f"SUCCESS: HTTP {response.status}")
        print()
        print("Key info returned by OpenRouter:")
        print(json.dumps(body, indent=2))
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    print(f"FAIL: HTTP {e.code} {e.reason}")
    print(error_body)
    if e.code == 401:
        print()
        print("The key itself is being rejected at the most basic possible")
        print("check OpenRouter offers — this points to the key/account,")
        print("not any request formatting, model name, or routing issue.")
except urllib.error.URLError as e:
    print(f"FAIL: network error — {e.reason}")