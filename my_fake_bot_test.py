"""Quick test: try to impersonate bot-service against the local API.

Run this WHILE `python -m api.app profiles.demo` is running.
Usage: python test_impersonation.py
"""

import requests

API_BASE = "http://localhost:8902"

print("Test 1: X-Identity: bot-service, NO X-Service-Key header")
r1 = requests.get(
    f"{API_BASE}/Notifications",
    params={"since": 0, "wait_seconds": 1},
    headers={"X-Identity": "bot-service"},
)
print(f"  Status: {r1.status_code}")
print(f"  Body: {r1.text[:300]}")
print()

print("Test 2: X-Identity: bot-service, WRONG X-Service-Key")
r2 = requests.get(
    f"{API_BASE}/Notifications",
    params={"since": 0, "wait_seconds": 1},
    headers={
        "X-Identity": "bot-service",
        "X-Service-Key": "this-is-definitely-not-the-real-key-12345",
    },
)
print(f"  Status: {r2.status_code}")
print(f"  Body: {r2.text[:300]}")
print()

print("Expected: both requests should return 401 Unauthorized.")
print("If either returns 200, the bot-service key check is NOT working correctly.")