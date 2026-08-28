"""Compatibility coverage for module paths replaced by the deep refactor."""

import importlib

import pytest


ALIASES = [
    ("agents.adapter", "agents.runtime"),
    ("agents.base", "agents.runtime"),
    ("agents.registry", "agents.runtime"),
    ("agents.errors", "agents.contracts"),
    ("agents.builtins", "agents.standard_agents"),
    ("agents.history", "agents.standard_agents"),
    ("agents.reference", "agents.standard_agents"),
    ("agents.results", "agents.contracts"),
    ("api.auth", "api.request_boundary"),
    ("api.errors", "api.request_boundary"),
    ("api.http", "api.request_boundary"),
    ("api.ingestion", "api.routes"),
    ("api.management", "api.routes"),
    ("api.operations", "api.routes"),
    ("bot.api_client", "bot.contracts"),
    ("bot.commands", "bot.interactions"),
    ("bot.deps", "bot.contracts"),
    ("bot.formatting", "bot.interactions"),
    ("bot.holds", "bot.interactions"),
    ("bot.presentation", "bot.interactions"),
    ("bot.client", "bot.transports"),
    ("bot.http_api_client", "bot.transports"),
    ("bot.runtime", "bot.background_services"),
    ("bot.notifications", "bot.background_services"),
    ("bot.startup", "bot.background_services"),
    ("bot.telegram_client", "bot.transports"),
    ("bot.users", "bot.interactions"),
    ("config.base", "config.environment"),
    ("config.models", "config.environment"),
    ("config.settings", "config.live_settings"),
    ("config.settings_store", "config.live_settings"),
    ("history.events", "history.event_pipeline"),
    ("history.extraction", "history.event_pipeline"),
    ("history.scheduler", "history.summaries"),
    ("history.time_utils", "history.event_pipeline"),
    ("history.write", "history.event_pipeline"),
    ("orchestrator.decisions", "orchestrator.reasoning"),
    ("orchestrator.main_agent", "orchestrator.reasoning"),
    ("orchestrator.precedent", "orchestrator.reasoning"),
    ("orchestrator.question_flow", "orchestrator.reasoning"),
    ("orchestrator.queue", "orchestrator.event_queue"),
    ("orchestrator.runtime", "orchestrator.event_queue"),
    ("persistence.exceptions", "persistence.contracts"),
    ("persistence.interface", "persistence.contracts"),
    ("persistence.sqlite", "persistence.sqlite_store"),
    ("persistence.sqlite_backend", "persistence.sqlite_store"),
    ("profiles.example", "profiles.template"),
    ("profiles.reference", "profiles.template"),
    ("profiles.spec", "profiles.contracts"),
    ("protocols.editor", "protocols.repository"),
    ("protocols.loader", "protocols.repository"),
    ("protocols.service", "protocols.repository"),
    ("protocols.model", "protocols.contracts"),
    ("tools._terminal_client_shared", "tools.terminal_support"),
    ("tools.terminal", "tools.terminal_support"),
    ("tools.logging_config", "tools.observability"),
    ("tools.tracing", "tools.observability"),
]


@pytest.mark.parametrize(("legacy_path", "canonical_path"), ALIASES[:43])
def test_legacy_module_alias_is_the_canonical_module(legacy_path: str, canonical_path: str):
    assert importlib.import_module(legacy_path) is importlib.import_module(canonical_path)


def test_special_package_facades_remain_importable():
    for legacy_path, canonical_path in ALIASES[43:]:
        assert importlib.import_module(legacy_path) is importlib.import_module(canonical_path)

    assert importlib.import_module("history.interface") is importlib.import_module("history")
    assert importlib.import_module("bot.interface") is importlib.import_module("bot")
    insights = importlib.import_module("orchestrator.insights")
    assert insights.InsightsAgent is importlib.import_module("orchestrator.reasoning").InsightsAgent

    api_contracts = importlib.import_module("api.contracts")
    assert api_contracts.ApiContext is importlib.import_module("api.app").ApiContext
    assert api_contracts.ApiError is importlib.import_module("api.request_boundary").ApiError
