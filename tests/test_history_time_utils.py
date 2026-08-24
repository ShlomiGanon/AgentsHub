"""history/time_utils.py — direct, targeted tests.

Every existing test exercises this module only indirectly, through
history/scheduler.py's/history/summarize.py's own tests calling
reconcile() — none of them target this module's own boundary conditions.
This file does.
"""

from datetime import datetime, timedelta, timezone

import pytest

from history.time_utils import (
    UTC,
    add_month,
    day_bounds,
    iter_days,
    iter_months,
    iter_years,
    month_bounds,
    parse_timestamp,
    storage_timestamp,
    year_bounds,
)

# -- parse_timestamp ----------------------------------------------------


def test_parse_timestamp_accepts_a_z_suffix_as_utc():
    parsed = parse_timestamp("2026-08-24T09:00:00Z")

    assert parsed == datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


def test_parse_timestamp_treats_a_naive_timestamp_as_already_utc():
    parsed = parse_timestamp("2026-08-24T09:00:00")

    assert parsed == datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


def test_parse_timestamp_converts_a_non_utc_offset_to_utc():
    # 02:00 at +05:00 is 21:00 UTC the *previous* day — the exact
    # near-midnight-crossing-a-day case worth being explicit about.
    parsed = parse_timestamp("2026-03-15T02:00:00+05:00")

    assert parsed == datetime(2026, 3, 14, 21, 0, 0, tzinfo=UTC)


def test_parse_timestamp_strips_surrounding_whitespace():
    assert parse_timestamp("  2026-08-24T09:00:00Z  ") == datetime(2026, 8, 24, 9, 0, 0, tzinfo=UTC)


def test_parse_timestamp_rejects_unparseable_input_with_value_error():
    # history/extraction.py's own call site catches exactly ValueError
    # (wrapping it as ExtractionExecutionError) — confirming the existing
    # contract, not inventing a new one.
    with pytest.raises(ValueError):
        parse_timestamp("not a timestamp at all")


def test_parse_timestamp_rejects_an_empty_string():
    with pytest.raises(ValueError):
        parse_timestamp("")


# -- storage_timestamp ----------------------------------------------------


def test_storage_timestamp_round_trips_through_parse_timestamp():
    original = "2026-08-24T09:00:00Z"
    assert storage_timestamp(parse_timestamp(original)) == "2026-08-24T09:00:00"


def test_storage_timestamp_strips_timezone_and_truncates_to_seconds():
    value = datetime(2026, 8, 24, 9, 30, 15, 123456, tzinfo=UTC)

    assert storage_timestamp(value) == "2026-08-24T09:30:15"


def test_storage_timestamp_converts_a_non_utc_aware_value_to_utc_first():
    value = datetime(2026, 3, 15, 2, 0, 0, tzinfo=timezone(timedelta(hours=5)))

    assert storage_timestamp(value) == "2026-03-14T21:00:00"


# -- day_bounds -----------------------------------------------------------


def test_day_bounds_is_midnight_to_midnight_utc():
    start, end = day_bounds(datetime(2026, 8, 24, 15, 30, tzinfo=UTC))

    assert start == datetime(2026, 8, 24, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 25, 0, 0, tzinfo=UTC)


def test_day_bounds_crosses_a_month_end():
    start, end = day_bounds(datetime(2026, 1, 31, 12, 0, tzinfo=UTC))

    assert start == datetime(2026, 1, 31, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 2, 1, 0, 0, tzinfo=UTC)


def test_day_bounds_crosses_a_year_end():
    start, end = day_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))

    assert start == datetime(2026, 12, 31, 0, 0, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, 0, 0, tzinfo=UTC)


def test_day_bounds_uses_the_given_datetimes_own_date_not_a_utc_renormalization():
    # day_bounds does not itself convert to UTC — it takes .date() from
    # whatever it's handed. Production code always feeds it the output of
    # parse_timestamp (already UTC), so this is documenting the contract,
    # not a bug: a caller that skips parse_timestamp and passes a
    # non-UTC-aware value gets bounds for *that* value's own calendar
    # date, not the UTC-equivalent one.
    non_utc = datetime(2026, 3, 15, 2, 0, 0, tzinfo=timezone(timedelta(hours=5)))  # 21:00 UTC on the 14th

    start, _end = day_bounds(non_utc)

    assert start == datetime(2026, 3, 15, 0, 0, tzinfo=UTC)  # the 15th, not the 14th


# -- month_bounds / add_month ----------------------------------------------


def test_month_bounds_within_a_year():
    start, end = month_bounds(datetime(2026, 3, 15, tzinfo=UTC))

    assert start == datetime(2026, 3, 1, tzinfo=UTC)
    assert end == datetime(2026, 4, 1, tzinfo=UTC)


def test_month_bounds_rolls_december_into_next_january():
    start, end = month_bounds(datetime(2026, 12, 15, tzinfo=UTC))

    assert start == datetime(2026, 12, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_add_month_rolls_the_year_over():
    assert add_month(datetime(2026, 12, 1, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)


def test_add_month_within_a_year():
    assert add_month(datetime(2026, 3, 1, tzinfo=UTC)) == datetime(2026, 4, 1, tzinfo=UTC)


# -- year_bounds ------------------------------------------------------------


def test_year_bounds():
    start, end = year_bounds(datetime(2026, 6, 15, tzinfo=UTC))

    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


def test_year_bounds_at_the_very_edge_of_the_year():
    start, end = year_bounds(datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC))

    assert start == datetime(2026, 1, 1, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, tzinfo=UTC)


# -- iter_days / iter_months / iter_years — leap years and multi-boundary --
# -- crossings --------------------------------------------------------------


def test_iter_days_counts_29_days_in_a_leap_year_february():
    # 2028 is a leap year.
    days = list(iter_days(datetime(2028, 2, 1, tzinfo=UTC), datetime(2028, 3, 1, tzinfo=UTC)))

    assert len(days) == 29
    assert days[-1][0] == datetime(2028, 2, 29, tzinfo=UTC)


def test_iter_days_counts_28_days_in_a_non_leap_year_february():
    # 2026 is not a leap year.
    days = list(iter_days(datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)))

    assert len(days) == 28
    assert days[-1][0] == datetime(2026, 2, 28, tzinfo=UTC)


def test_iter_days_each_pair_is_exactly_one_day_apart_and_contiguous():
    days = list(iter_days(datetime(2026, 1, 30, tzinfo=UTC), datetime(2026, 2, 2, tzinfo=UTC)))

    assert days == [
        (datetime(2026, 1, 30, tzinfo=UTC), datetime(2026, 1, 31, tzinfo=UTC)),
        (datetime(2026, 1, 31, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
        (datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 2, 2, tzinfo=UTC)),
    ]


def test_iter_months_crosses_a_year_boundary():
    months = list(iter_months(datetime(2026, 11, 1, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC)))

    assert months == [
        (datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
        (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        (datetime(2027, 1, 1, tzinfo=UTC), datetime(2027, 2, 1, tzinfo=UTC)),
    ]


def test_iter_years_across_multiple_years():
    years = list(iter_years(datetime(2024, 6, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)))

    assert years == [
        (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
        (datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
    ]


def test_iter_days_with_start_equal_to_end_yields_nothing():
    assert list(iter_days(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))) == []
