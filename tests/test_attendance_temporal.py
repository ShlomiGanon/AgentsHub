from datetime import datetime, timezone

from history import resolve_availability_period


REFERENCE = "2026-09-16T13:57:45+00:00"
TIMEZONE = "Asia/Jerusalem"


def _period(text: str, received_at: str = REFERENCE):
    result = resolve_availability_period(text, received_at, TIMEZONE)
    assert result is not None
    return result


def test_today_uses_the_received_at_local_calendar_day():
    period = _period("אני לא זמין היום")

    assert period.availability_start == "2026-09-15T21:00:00+00:00"
    assert period.availability_end == "2026-09-16T21:00:00+00:00"


def test_tomorrow_uses_the_day_after_received_at_in_profile_timezone():
    period = _period("אני לא זמין מחר")

    assert period.availability_start == "2026-09-16T21:00:00+00:00"
    assert period.availability_end == "2026-09-17T21:00:00+00:00"


def test_weekday_means_the_nearest_weekday_that_has_not_passed():
    period = _period("אני לא זמין ביום ראשון")

    assert period.availability_start == "2026-09-19T21:00:00+00:00"
    assert period.availability_end == "2026-09-20T21:00:00+00:00"


def test_relative_weekday_range_ends_at_the_declared_evening_cutoff():
    period = _period("אני במילואים מראשון עד שלישי בערב, לא זמין")

    assert period.availability_start == "2026-09-19T21:00:00+00:00"
    assert period.availability_end == "2026-09-22T17:00:00+00:00"


def test_weekday_range_can_cross_into_the_following_week():
    period = _period("אני לא זמין מיום שלישי עד יום שני בערב")

    assert period.availability_start == "2026-09-21T21:00:00+00:00"
    assert period.availability_end == "2026-09-28T17:00:00+00:00"


def test_explicit_absolute_date_is_a_full_local_calendar_day():
    period = _period("אני לא זמין בתאריך 2026-10-01")

    assert period.availability_start == "2026-09-30T21:00:00+00:00"
    assert period.availability_end == "2026-10-01T21:00:00+00:00"


def test_explicit_absolute_date_range_is_inclusive_of_its_end_day():
    period = _period("אני לא זמין מ-2026-10-01 עד 2026-10-03")

    assert period.availability_start == "2026-09-30T21:00:00+00:00"
    assert period.availability_end == "2026-10-03T21:00:00+00:00"


def test_unresolved_temporal_language_returns_none_for_clarification():
    assert resolve_availability_period("אני לא זמין בקרוב", REFERENCE, TIMEZONE) is None


def test_received_at_is_converted_to_asia_jerusalem_before_relative_resolution():
    period = _period("אני לא זמין היום", "2026-09-16T21:30:00+00:00")

    assert period.availability_start == "2026-09-16T21:00:00+00:00"
    assert period.availability_end == "2026-09-17T21:00:00+00:00"
