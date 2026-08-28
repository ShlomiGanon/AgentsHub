"""Opt-in response-pipeline evaluation; live mode makes billed model calls."""

import argparse
import json
from collections import Counter
from pathlib import Path

from api.app import build_context
from config import resolve_tier_model_from_env
from orchestrator.flows import classify_intent


def _load_cases(path: Path, split: str | None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [case for case in cases if split is None or case["split"] == split]


def _score(cases: list[dict], predictions: dict[str, str]) -> dict:
    by_class: dict[str, Counter] = {}
    correct = 0
    for case in cases:
        expected = case["expected_intent"]
        predicted = predictions.get(case["id"], "missing")
        by_class.setdefault(expected, Counter())["total"] += 1
        if predicted == expected:
            correct += 1
            by_class[expected]["correct"] += 1
    return {
        "cases": len(cases),
        "accuracy": correct / len(cases) if cases else 0.0,
        "per_class": {
            label: {"correct": counts["correct"], "total": counts["total"]}
            for label, counts in sorted(by_class.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate AgentsHub response routing. --live incurs provider charges.")
    parser.add_argument("--corpus", type=Path, default=Path("fixtures/response_eval_v1.jsonl"))
    parser.add_argument("--split", choices=("development", "held_out"))
    parser.add_argument("--predictions", type=Path, help="Offline JSONL containing id and predicted_intent")
    parser.add_argument("--live", action="store_true", help="Call the configured live model; this is billed")
    parser.add_argument("--profile", help="Profile module required by --live")
    args = parser.parse_args(argv)
    cases = _load_cases(args.corpus, args.split)

    predictions: dict[str, str] = {}
    if args.predictions:
        for line in args.predictions.read_text(encoding="utf-8").splitlines():
            prediction = json.loads(line)
            predictions[prediction["id"]] = prediction["predicted_intent"]
    elif args.live:
        if not args.profile:
            parser.error("--profile is required with --live")
        context = build_context(
            args.profile,
            resolve_tier_model_from_env("CORE"),
            resolve_tier_model_from_env("SUB"),
        )
        try:
            protocols = context.deps.protocol_set.all()
            for case in cases:
                predictions[case["id"]] = classify_intent(context.main_agent, protocols, case["message"]).intent
        finally:
            context.queue.stop()
            context.scheduler.stop()
            context.deps.persistence.close()
    else:
        print(json.dumps({"cases": len(cases), "splits": dict(Counter(case["split"] for case in cases))}, indent=2))
        return 0

    print(json.dumps(_score(cases, predictions), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
