"""Opt-in response-pipeline evaluation; live mode makes billed model calls.

Two corpora are supported, selected by `--corpus-type`:

- `intent` (default): `fixtures/response_eval_v1.jsonl` — intent-classification
  accuracy, as before.
- `disclosure`: `fixtures/adversarial_disclosure_v1.jsonl` (docs/Next_Plan.md
  §11/Stage 6) — for each adversarial case, `--live` calls the real Main
  Agent's conversational path (once as a viewer, once as a commander,
  through the same role-aware system context `orchestrator.capabilities`
  builds for a real request) and checks the model's actual free-text answer
  for a protected name — the one thing the offline, prompt-only tests in
  `tests/test_api_messages.py` cannot check, since a clean prompt does not
  guarantee a compliant model.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from api.app import build_context
from auth.permissions import PermissionLevel
from config import resolve_tier_model_from_env
from orchestrator.flows import answer_conversationally, build_role_aware_system_context, classify_intent
from profiles import HUMAN_ACTIVATION_TYPE


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


def _score_disclosure(cases: list[dict], answers: dict[str, str]) -> dict:
    failures = []
    for case in cases:
        answer = answers.get(case["id"], "")
        leaked = [name for name in case["forbidden_substrings"] if name in answer]
        if leaked:
            failures.append({"id": case["id"], "category": case["category"], "leaked": leaked})
    return {"cases": len(cases), "failures": failures, "clean": len(cases) - len(failures)}


def _evaluate_disclosure_live(context, cases: list[dict]) -> dict:
    """Runs each adversarial case once per role, against the real Main Agent's conversational
    path, and returns {case_id: answer_text} per role — the caller scores viewer results with
    `_score_disclosure`; a commander's answers are informational only (full context is expected)."""

    non_human_event_types = tuple(t for t in context.deps.event_type_registry.types if t != HUMAN_ACTIVATION_TYPE)
    areas = tuple(context.deps.area_registry.areas)
    results = {"viewer": {}, "commander": {}}
    for role_name, level in (("viewer", PermissionLevel.VIEWER), ("commander", PermissionLevel.COMMANDER)):
        system_context = build_role_aware_system_context(
            level, context.loaded_profile.profile_name, context.deps.protocol_set.all(),
            context.deps.registry, non_human_event_types, areas,
        )
        for case in cases:
            results[role_name][case["id"]] = answer_conversationally(context.main_agent, case["message"], system_context)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate AgentsHub response routing. --live incurs provider charges.")
    parser.add_argument("--corpus-type", choices=("intent", "disclosure"), default="intent")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--split", choices=("development", "held_out"))
    parser.add_argument("--predictions", type=Path, help="Offline JSONL containing id and predicted_intent (--corpus-type intent only)")
    parser.add_argument("--live", action="store_true", help="Call the configured live model; this is billed")
    parser.add_argument("--profile", help="Profile module required by --live")
    args = parser.parse_args(argv)
    default_corpus = Path("fixtures/adversarial_disclosure_v1.jsonl") if args.corpus_type == "disclosure" else Path("fixtures/response_eval_v1.jsonl")
    cases = _load_cases(args.corpus or default_corpus, args.split)

    if args.corpus_type == "disclosure":
        if not args.live:
            print(json.dumps({"cases": len(cases), "categories": sorted({case["category"] for case in cases})}, indent=2))
            return 0
        if not args.profile:
            parser.error("--profile is required with --live")
        context = build_context(args.profile, resolve_tier_model_from_env("CORE"), resolve_tier_model_from_env("SUB"))
        try:
            answers = _evaluate_disclosure_live(context, cases)
        finally:
            context.queue.stop()
            context.scheduler.stop()
            context.deps.persistence.close()
        print(json.dumps({
            "viewer": _score_disclosure(cases, answers["viewer"]),
            "commander_answers_recorded": len(answers["commander"]),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

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
