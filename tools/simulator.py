"""Sensor event simulator (work_plan.md §9.1).

Standalone program — run directly (`python -m tools.simulator ...`), never
imported by anything else in the system. It is a client of `POST /Event`,
authenticating as any other caller would (a pre-registered sensor
identity, provisioned via `cli.user_admin` before this is ever run); it
has no special access and no import path into the rest of the codebase
beyond the standard library. This is why `docs/allowed_calls.md` has no
entry for it — nothing inside the system ever calls it, and it never
calls anything inside the system except over the same HTTP boundary any
other API client uses.

Generates several distinct sentence templates per event type and area,
with randomized location/severity language, rather than one fixed
string — repeated runs don't produce byte-identical text, so extraction
is genuinely exercised rather than pattern-matched against one shape.
This is not natural-language generation; it's enough real variation to
satisfy this subtask's own "reads like a real sensor report rather than
a template" bullet short of running a real model to write the reports.

Built on `urllib.request` only, the same choice `bot/http_api_client.py`
made — no new runtime dependency for a client this small.
"""

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

_FIRE_TEMPLATES = [
    "Smoke visible near {location} in {area}, {severity}.",
    "Report of an open flame at {location}, {area} — {severity}.",
    "Heat signature detected close to {location} in the {area} zone, {severity}.",
    "Caller reports fire at {location} in {area}; {severity}.",
    "Haze and a burning smell reported around {location}, {area}, {severity}.",
]

_MEDICAL_TEMPLATES = [
    "Person reported unresponsive near {location}, {area}.",
    "Medical assistance requested at {location} in {area} — {severity}.",
    "Injury reported at {location}, {area}; {severity}.",
    "A bystander flagged a possible medical emergency near {location}, {area}.",
    "Someone collapsed near {location} in {area}, {severity}.",
]

_UNCLASSIFIABLE_TEMPLATES = [
    "Something unusual near {location}, hard to describe.",
    "Sensor picked up an anomaly at {location}; unclear what it is.",
    "Noise reported near {location} in {area}, cause unknown.",
    "Unidentified activity observed close to {location}.",
    "A report came in about {location} but the details don't fit anything known.",
]

_LOCATIONS = [
    "gate 3", "the loading dock", "building 7", "the north fence line",
    "the parking structure", "the main entrance", "the warehouse", "the perimeter road",
]
_SEVERITIES = [
    "appears minor", "looks serious", "unclear how serious",
    "escalating quickly", "seems contained for now",
]


def _generate_text(event_type: str | None, area: str) -> str:
    if event_type == "fire":
        template = random.choice(_FIRE_TEMPLATES)
    elif event_type == "medical":
        template = random.choice(_MEDICAL_TEMPLATES)
    else:
        template = random.choice(_UNCLASSIFIABLE_TEMPLATES)

    return template.format(location=random.choice(_LOCATIONS), area=area, severity=random.choice(_SEVERITIES))


def _next_classification_area(event_types: list[str], areas: list[str], repeat_rate: float, unclassifiable_rate: float, recent: list[tuple[str, str]]) -> tuple[str | None, str]:
    """Pick the (event_type, area) pair for the next event. `recent` is a
    small pool of already-emitted pairs — reusing one is what gives
    precedent lookup (§6.5) real matches to find during a live run,
    per this subtask's own bullet, rather than only in fixtures.
    """

    if random.random() < unclassifiable_rate:
        return None, random.choice(areas)

    if recent and random.random() < repeat_rate:
        return random.choice(recent)

    return random.choice(event_types), random.choice(areas)


def _post_event(base_url: str, identity: str, text: str, timeout: float = 10.0) -> dict:
    body = json.dumps({"text": text, "sender_identity": identity}).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/Event", data=body, method="POST",
        headers={"X-Identity": identity, "Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status_code": response.status, **json.loads(response.read())}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"message": raw.decode("utf-8", errors="replace")}
        return {"status_code": exc.code, **payload}
    except urllib.error.URLError as exc:
        return {"status_code": None, "message": str(exc.reason)}


def _emit_one(base_url: str, identity: str, event_types: list[str], areas: list[str], repeat_rate: float, unclassifiable_rate: float, recent: list[tuple[str, str]]) -> dict:
    event_type, area = _next_classification_area(event_types, areas, repeat_rate, unclassifiable_rate, recent)
    text = _generate_text(event_type, area)

    if event_type is not None:
        recent.append((event_type, area))
        del recent[:-20]  # keep the pool small and recent, not unbounded

    result = _post_event(base_url, identity, text)
    result["text"] = text
    result["intended_classification"] = event_type
    result["intended_area"] = area
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.simulator",
        description="Emit synthetic sensor events as free-form English text against a running deployment's POST /Event (work_plan.md §9.1).",
    )
    parser.add_argument("--host", default="localhost", help="target deployment host (default: localhost)")
    parser.add_argument("--port", type=int, required=True, help="the target deployment's API port (the profile's own API_PORT)")
    parser.add_argument("--identity", required=True, help="the sensor's own pre-registered identity — must already exist via cli.user_admin, same as any other caller")
    parser.add_argument("--count", type=int, default=None, help="total events to send; give this or --duration, not both")
    parser.add_argument("--duration", type=float, default=None, help="seconds to run for; give this or --count, not both")
    parser.add_argument("--rate", type=float, default=1.0, help="events per second outside of a burst (default: 1.0)")
    parser.add_argument(
        "--burst-size", type=int, default=0,
        help="send this many events back-to-back with no delay, once, before switching to --rate — "
        "exercises serial processing and SQLite write contention (§9.19)",
    )
    parser.add_argument(
        "--repeat-rate", type=float, default=0.2,
        help="probability [0,1] of reusing a recently-emitted classification/area pair, "
        "so precedent lookup (§6.5) has real matches to find (default: 0.2)",
    )
    parser.add_argument(
        "--unclassifiable-rate", type=float, default=0.1,
        help="probability [0,1] of emitting text no classification fits, to drive the clarification path live (default: 0.1)",
    )
    parser.add_argument("--event-types", default="fire,medical", help="comma-separated types to emit — must match the target profile's own EVENT_TYPES")
    parser.add_argument("--areas", default="north_sector,south_sector", help="comma-separated areas to emit — must match the target profile's own AREAS")
    parser.add_argument("--seed", type=int, default=None, help="random seed, for a reproducible run")
    parser.add_argument("--quiet", action="store_true", help="only print the final summary, not each submission")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if (args.count is None) == (args.duration is None):
        print("error: give exactly one of --count or --duration", file=sys.stderr)
        return 1

    if args.seed is not None:
        random.seed(args.seed)

    base_url = f"http://{args.host}:{args.port}"
    event_types = [t.strip() for t in args.event_types.split(",") if t.strip()]
    areas = [a.strip() for a in args.areas.split(",") if a.strip()]
    recent: list[tuple[str, str]] = []

    sent = 0
    succeeded = 0
    failed = 0
    start = time.monotonic()
    deadline = start + args.duration if args.duration is not None else None

    def _should_continue() -> bool:
        if args.count is not None:
            return sent < args.count
        return time.monotonic() < deadline

    def _emit_and_report() -> None:
        nonlocal sent, succeeded, failed
        result = _emit_one(base_url, args.identity, event_types, areas, args.repeat_rate, args.unclassifiable_rate, recent)
        sent += 1
        if result.get("status_code") == 202:
            succeeded += 1
        else:
            failed += 1
        if not args.quiet:
            print(json.dumps(result))

    if args.burst_size > 0:
        for _ in range(args.burst_size):
            if not _should_continue():
                break
            _emit_and_report()

    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    while _should_continue():
        _emit_and_report()
        if interval > 0:
            time.sleep(interval)

    elapsed = time.monotonic() - start
    print(f"sent={sent} succeeded={succeeded} failed={failed} elapsed_seconds={elapsed:.2f}", file=sys.stderr)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
