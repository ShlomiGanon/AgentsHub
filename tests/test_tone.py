"""orchestrator/tone.py — the shared, catalog-driven banned-opener check."""

from messages import get_catalog
from orchestrator.tone import banned_opener


def test_banned_opener_detects_a_known_hebrew_phrase():
    catalog = get_catalog("he")
    assert banned_opener("התקבל דיווח על שריפה קטנה.", catalog) == "התקבל"


def test_banned_opener_detects_a_known_english_phrase():
    catalog = get_catalog("en")
    assert banned_opener("Your report was received and logged.", catalog) == "Your report was received"


def test_banned_opener_catches_the_niklat_synonym_for_a_received_acknowledgement():
    # Found live (Phase A verification): the model used a different verb ("נקלט" = "was
    # registered/absorbed") for the same banned "update was received" idea -- a synonym the
    # original narrower phrase list ("התקבל"/"העדכון התקבל") did not catch.
    catalog = get_catalog("he")
    assert banned_opener("העדכון נקלט — אינך זמין ביישוב עקב מילואים.", catalog) is not None


def test_banned_opener_is_none_for_a_clean_opener():
    catalog = get_catalog("en")
    assert banned_opener("The small fire near the access road was logged.", catalog) is None


def test_banned_opener_ignores_a_match_outside_the_opening_window():
    # A banned word appearing only well past the opener (not what the reply "opens with")
    # must not trip the check -- it targets how the reply begins, not its entire content.
    catalog = get_catalog("en")
    padding = "x" * 90
    text = padding + " classified as unrelated background detail"
    assert banned_opener(text, catalog) is None
