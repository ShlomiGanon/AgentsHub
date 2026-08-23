from history.precedent import PrecedentMatch
from orchestrator.precedent import determine_closure, look_up_precedent


def _match(event_id, resolved):
    return PrecedentMatch(
        event_id=event_id,
        classification="fire",
        area="north_sector",
        occurred_at="2026-08-01T00:00:00",
        protocol_name="status_check",
        steps_summary=[],
        outcome="succeeded" if resolved else None,
        resolved=resolved,
    )


class _ScriptedHistoryQueryService:
    def __init__(self, matches):
        self._matches = matches
        self.calls = []

    def search_precedents(self, target_event_id, classification, area, target_event_occurred_at):
        self.calls.append((target_event_id, classification, area, target_event_occurred_at))
        return self._matches


# -- look_up_precedent ----------------------------------------------------


def test_look_up_precedent_passes_arguments_through():
    service = _ScriptedHistoryQueryService([_match("evt-old", True)])

    result = look_up_precedent(service, "evt-new", "fire", "north_sector", "2026-08-20T10:00:00")

    assert service.calls == [("evt-new", "fire", "north_sector", "2026-08-20T10:00:00")]
    assert result == (_match("evt-old", True),)


def test_look_up_precedent_returns_empty_tuple_for_no_matches():
    service = _ScriptedHistoryQueryService([])

    assert look_up_precedent(service, "evt-new", "fire", "north", "t") == ()


# -- determine_closure ------------------------------------------------------


def test_high_risk_never_closes_even_with_a_resolved_match():
    precedents = (_match("evt-old", resolved=True),)

    assert determine_closure("high", "fire", precedents) is None


def test_low_risk_with_resolved_match_closes():
    precedents = (_match("evt-old", resolved=True),)

    assert determine_closure("low", "fire", precedents) == "evt-old"


def test_low_risk_with_only_unresolved_match_does_not_close():
    precedents = (_match("evt-old", resolved=False),)

    assert determine_closure("low", "fire", precedents) is None


def test_low_risk_with_no_matches_does_not_close():
    assert determine_closure("low", "fire", ()) is None


def test_human_activation_never_closes_even_with_a_resolved_match():
    precedents = (_match("evt-old", resolved=True),)

    assert determine_closure("low", "human_activation", precedents) is None


def test_most_recent_resolved_match_is_used_among_several():
    # search_precedents already returns most-recent-first; the first
    # resolved one encountered is used.
    precedents = (_match("evt-unresolved-recent", resolved=False), _match("evt-resolved-older", resolved=True))

    assert determine_closure("low", "fire", precedents) == "evt-resolved-older"
