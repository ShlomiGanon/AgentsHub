"""Compatibility coverage for module paths replaced by the deep refactor."""

import importlib

import pytest


ALIASES = [
    ("agents.adapter", "agents.runtime"),
    ("agents.base", "agents.runtime"),
    ("agents.errors", "agents.contracts"),
    ("agents.history", "agents.builtins"),
    ("agents.reference", "agents.builtins"),
    ("agents.results", "agents.contracts"),
    ("api.auth", "api.http"),
    ("api.errors", "api.http"),
    ("api.ingestion", "api.routes"),
    ("api.management", "api.routes"),
    ("api.operations", "api.routes"),
    ("bot.api_client", "bot.contracts"),
    ("bot.commands", "bot.presentation"),
    ("bot.deps", "bot.contracts"),
    ("bot.formatting", "bot.presentation"),
    ("bot.holds", "bot.presentation"),
    ("bot.http_api_client", "bot.client"),
    ("bot.notifications", "bot.runtime"),
    ("bot.startup", "bot.runtime"),
    ("bot.telegram_client", "bot.client"),
    ("bot.users", "bot.presentation"),
    ("config.base", "config.models"),
    ("config.settings_store", "config.settings"),
    ("history.extraction", "history.events"),
    ("history.scheduler", "history.summaries"),
    ("history.time_utils", "history.events"),
    ("history.write", "history.events"),
    ("orchestrator.main_agent", "orchestrator.decisions"),
    ("orchestrator.precedent", "orchestrator.decisions"),
    ("orchestrator.queue", "orchestrator.runtime"),
    ("persistence.exceptions", "persistence.contracts"),
    ("persistence.interface", "persistence.contracts"),
    ("persistence.sqlite_backend", "persistence.sqlite"),
    ("profiles.reference", "profiles.example"),
    ("profiles.spec", "profiles.contracts"),
    ("protocols.editor", "protocols.service"),
    ("protocols.loader", "protocols.service"),
    ("protocols.model", "protocols.contracts"),
    ("registries.areas", "registries.registry"),
    ("registries.event_types", "registries.registry"),
    ("tools._terminal_client_shared", "tools.terminal"),
    ("tools.logging_config", "tools.observability"),
    ("tools.tracing", "tools.observability"),
]


@pytest.mark.parametrize(("legacy_path", "canonical_path"), ALIASES)
def test_legacy_module_alias_is_the_canonical_module(legacy_path: str, canonical_path: str):
    assert importlib.import_module(legacy_path) is importlib.import_module(canonical_path)


def test_special_package_facades_remain_importable():
    assert importlib.import_module("history.interface") is importlib.import_module("history")
    assert importlib.import_module("bot.interface") is importlib.import_module("bot")
    insights = importlib.import_module("orchestrator.insights")
    assert insights.InsightsAgent is importlib.import_module("orchestrator.decisions").InsightsAgent
