"""Fixed user-interface message catalog contracts."""

import pytest

from messages import MessageCatalogError, get_catalog, validate_catalogs


def test_english_and_hebrew_catalogs_have_matching_keys_and_placeholders():
    validate_catalogs()


def test_catalog_formats_the_profile_selected_language():
    english = get_catalog("en")
    hebrew = get_catalog("he")

    assert english.text("status.thinking") == "The model is thinking..."
    assert hebrew.text("status.thinking") == "המודל חושב..."
    assert "abc123" in english.text("status.async_ack", task_id="abc123")
    assert "abc123" in hebrew.text("status.async_ack", task_id="abc123")


def test_catalog_rejects_missing_extra_or_unknown_format_fields():
    catalog = get_catalog("en")

    with pytest.raises(MessageCatalogError, match="requires placeholders"):
        catalog.text("status.async_ack")
    with pytest.raises(MessageCatalogError, match="requires placeholders"):
        catalog.text("status.thinking", unexpected="value")
    with pytest.raises(MessageCatalogError, match="unknown message key"):
        catalog.text("does.not.exist")


def test_catalog_validation_rejects_missing_language_key():
    with pytest.raises(MessageCatalogError, match="keys differ"):
        validate_catalogs({"one": "One"}, {})


def test_catalog_validation_rejects_placeholder_drift():
    with pytest.raises(MessageCatalogError, match="different placeholders"):
        validate_catalogs({"one": "Value {value}"}, {"one": "Value {other}"})


def test_unsupported_language_is_rejected():
    with pytest.raises(MessageCatalogError, match="unsupported DEFAULT_LANGUAGE"):
        get_catalog("fr")
