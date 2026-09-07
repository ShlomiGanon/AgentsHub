"""Validated lookup and formatting for fixed user-interface messages."""

from dataclasses import dataclass
from contextvars import ContextVar
from string import Formatter
from types import MappingProxyType
from typing import Literal, Mapping

from messages.en import MESSAGES as ENGLISH_MESSAGES
from messages.he import MESSAGES as HEBREW_MESSAGES

Language = Literal["en", "he"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("en", "he")


class MessageCatalogError(ValueError):
    """The fixed-message catalogs or a requested formatting operation are invalid."""


def _placeholder_names(template: str) -> frozenset[str]:
    names: set[str] = set()
    try:
        parsed = Formatter().parse(template)
        for _literal, field_name, _format_spec, _conversion in parsed:
            if field_name is not None:
                names.add(field_name)
    except ValueError as exc:
        raise MessageCatalogError(f"invalid message template: {exc}") from exc
    return frozenset(names)


def validate_catalogs(
    english: Mapping[str, str] = ENGLISH_MESSAGES,
    hebrew: Mapping[str, str] = HEBREW_MESSAGES,
) -> None:
    """Fail when languages have different keys or incompatible placeholders."""

    english_keys = set(english)
    hebrew_keys = set(hebrew)
    if english_keys != hebrew_keys:
        missing_from_english = sorted(hebrew_keys - english_keys)
        missing_from_hebrew = sorted(english_keys - hebrew_keys)
        details = []
        if missing_from_english:
            details.append(f"missing from en: {', '.join(missing_from_english)}")
        if missing_from_hebrew:
            details.append(f"missing from he: {', '.join(missing_from_hebrew)}")
        raise MessageCatalogError("message catalog keys differ (" + "; ".join(details) + ")")

    for key in sorted(english_keys):
        if not isinstance(english[key], str) or not isinstance(hebrew[key], str):
            raise MessageCatalogError(f"message {key!r} must be a string in every language")
        english_fields = _placeholder_names(english[key])
        hebrew_fields = _placeholder_names(hebrew[key])
        if english_fields != hebrew_fields:
            raise MessageCatalogError(
                f"message {key!r} has different placeholders in en and he: "
                f"{sorted(english_fields)} != {sorted(hebrew_fields)}"
            )


@dataclass(frozen=True)
class MessageCatalog:
    """One immutable, profile-selected user-interface message catalog."""

    language: Language
    messages: Mapping[str, str]

    def text(self, key: str, **values: object) -> str:
        try:
            template = self.messages[key]
        except KeyError as exc:
            raise MessageCatalogError(f"unknown message key: {key!r}") from exc

        expected = _placeholder_names(template)
        supplied = frozenset(values)
        if supplied != expected:
            raise MessageCatalogError(
                f"message {key!r} requires placeholders {sorted(expected)}, "
                f"got {sorted(supplied)}"
            )
        try:
            return template.format(**values)
        except (KeyError, ValueError, TypeError) as exc:
            raise MessageCatalogError(f"could not format message {key!r}: {exc}") from exc


_CATALOGS: Mapping[Language, MessageCatalog] = MappingProxyType(
    {
        "en": MessageCatalog("en", MappingProxyType(dict(ENGLISH_MESSAGES))),
        "he": MessageCatalog("he", MappingProxyType(dict(HEBREW_MESSAGES))),
    }
)
_current_catalog: ContextVar[MessageCatalog] = ContextVar(
    "current_message_catalog", default=_CATALOGS["en"]
)


def get_catalog(language: str) -> MessageCatalog:
    validate_catalogs()
    try:
        return _CATALOGS[language]  # type: ignore[index]
    except KeyError as exc:
        raise MessageCatalogError(
            f"unsupported DEFAULT_LANGUAGE {language!r}; expected one of {SUPPORTED_LANGUAGES}"
        ) from exc


def get_current_catalog() -> MessageCatalog:
    return _current_catalog.get()


def set_current_catalog(catalog: MessageCatalog) -> None:
    _current_catalog.set(catalog)
