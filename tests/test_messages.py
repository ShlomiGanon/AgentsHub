"""Fixed user-interface message catalog contracts."""

import re

import pytest

import messages.en
import messages.he
from messages import MessageCatalogError, get_catalog, validate_catalogs

# Standard emoji-carrying Unicode blocks: emoticons, misc symbols &
# pictographs (and its Extended-A block), transport/map symbols,
# supplemental symbols & pictographs, symbols & pictographs extended-A,
# dingbats, miscellaneous symbols (☀ ⚠ etc.), regional-indicator flag
# letters, and the variation selector / ZWJ used to render emoji sequences.
# Deliberately excludes the plain Arrows block (U+2190-U+21FF) and general
# punctuation like "…" — those are ordinary typographic symbols, not
# emoji, and this codebase's own dev-facing tooling (`tools/observability.py`)
# uses "→" intentionally in non-user-facing debug output.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF"
    "\U0000FE0F"
    "\U0000200D"
    "]"
)


def test_no_emoji_in_any_catalog_message():
    """AgentsHub — Closed Decision: No Emojis in Any User-Facing Message
    (2026-09-04): no user-facing message the bot sends may contain an emoji
    character, in either language. Every literal user-facing string in this
    codebase is composed through `messages.text()` from these two catalogs
    (confirmed by inspection — no `send_text`/`send_reply`/`send_with_buttons`
    call site anywhere in `bot/` passes a literal string), so scanning both
    `MESSAGES` dicts in full is an exhaustive check, not a sample."""

    for module in (messages.en, messages.he):
        for key, text in module.MESSAGES.items():
            found = _EMOJI_PATTERN.findall(text)
            assert not found, f"{module.__name__}.MESSAGES[{key!r}] contains emoji {found!r}: {text!r}"


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
