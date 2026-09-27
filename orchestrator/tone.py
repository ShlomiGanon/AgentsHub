"""Shared tone enforcement for every model-composed, user-facing reply (report
composition, event-data questions): a deterministic post-check for a banned opener,
catalog-driven so the banned phrases and examples are never raw literals outside
messages/he.py or messages/en.py (tests/test_hebrew_leakage.py's HARD RULE)."""

from messages import MessageCatalog


def banned_opener(text: str, catalog: MessageCatalog) -> str | None:
    """The banned phrase `text` opens with, if any -- checked against only the first ~80
    characters (an opener), not a scan of the whole reply, so a banned word used
    legitimately mid-sentence elsewhere is never a false positive."""

    opener = text.strip()[:80]
    for phrase in catalog.text("orchestrator.report_tone.banned_openers").split("|"):
        if phrase and phrase in opener:
            return phrase
    return None
