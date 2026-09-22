"""Task 67 — one GTCA core, two operational organization types.

`OperationalScope` says which operational instance this is; `OperationalProfile`
says what kind of organization it is. A response team and a fire station share
every mechanism in the core and differ only in what they contain and how they
speak. The profile is trusted configuration: no model and no message may choose
it.
"""

import logging
from types import SimpleNamespace

import pytest

from messages import get_catalog, get_current_catalog, set_current_catalog
from persistence import OperationalScope, open_team_status_persistence
from profiles import (
    FIRE_STATION,
    RESPONSE_TEAM,
    OperationalProfileError,
    current_operational_profile,
    declared_operational_profiles,
    operational_profile,
    operational_profile_context,
    profile_for_scope,
    profile_id_for_scenario,
)


FIRE_OPENING = (
    "מעדכן סד\"כ פותח: 6 כבאים, "
    "רכב אשד 3 וכרמל 1 במבצעיות."
)


@pytest.fixture
def deployment():
    import profiles.unified_test as unified_test

    return SimpleNamespace(
        live_operational_profile=RESPONSE_TEAM,
        simulations=unified_test.SIMULATIONS,
        protocols=unified_test.PROTOCOLS,
    )


# --- §27 profile resolution --------------------------------------------------


@pytest.mark.parametrize(
    "scenario_id,expected",
    (
        ("SEC_001_PHASE_1", RESPONSE_TEAM),
        ("SEC_001_PHASE_2", RESPONSE_TEAM),
        ("SEC_001_PHASE_3", RESPONSE_TEAM),
        ("FIRE_002_PHASE_1", FIRE_STATION),
        ("FIRE_002_PHASE_2", FIRE_STATION),
        ("FIRE_002_PHASE_3", FIRE_STATION),
    ),
)
def test_each_official_fixture_resolves_to_its_declared_organization(deployment, scenario_id, expected):
    scope = OperationalScope.simulation(scenario_id, "run-task67")

    assert profile_for_scope(deployment, scope).profile_id == expected


def test_the_support_fixture_belongs_to_the_deployment_organization(deployment):
    """The overall-picture fixture declares no domain, so it is not guessed at."""

    scope = OperationalScope.simulation("overall_picture_query", "run-task67")

    assert profile_for_scope(deployment, scope).profile_id == RESPONSE_TEAM


def test_the_mapping_comes_from_canonical_metadata_not_the_identifier():
    """A fixture named like the other family still follows its declared domain."""

    fire_named_sec = SimpleNamespace(
        official_metadata={"domain": "FIRE_AND_RESCUE"}, domain="FIRE_AND_RESCUE"
    )
    sec_named_fire = SimpleNamespace(
        official_metadata={"domain": "FIRST_RESPONDERS_TEAM"}, domain="FIRST_RESPONDERS_TEAM"
    )

    assert profile_id_for_scenario(fire_named_sec, RESPONSE_TEAM) == FIRE_STATION
    assert profile_id_for_scenario(sec_named_fire, FIRE_STATION) == RESPONSE_TEAM


def test_a_fixture_may_declare_its_organization_explicitly():
    explicit = SimpleNamespace(
        official_metadata={"domain": "FIRST_RESPONDERS_TEAM", "operational_profile": FIRE_STATION},
        domain="FIRST_RESPONDERS_TEAM",
    )

    assert profile_id_for_scenario(explicit, RESPONSE_TEAM) == FIRE_STATION


def test_message_text_cannot_choose_the_organization(deployment):
    """A report about fire does not make the reporting organization a fire station."""

    sec_scope = OperationalScope.simulation("SEC_001_PHASE_3", "run-task67")

    resolved = profile_for_scope(deployment, sec_scope)

    assert resolved.profile_id == RESPONSE_TEAM
    # The resolver is given no message at all — there is nothing for text to reach.
    assert "text" not in profile_for_scope.__code__.co_varnames


def test_an_undeclared_organization_is_refused_not_invented():
    with pytest.raises(OperationalProfileError):
        operational_profile("volunteer_militia")


def test_only_the_two_declared_organizations_exist():
    assert declared_operational_profiles() == (FIRE_STATION, RESPONSE_TEAM)


def test_live_uses_the_deployment_declared_organization(deployment):
    assert profile_for_scope(deployment, OperationalScope.live()).profile_id == RESPONSE_TEAM

    fire_deployment = SimpleNamespace(live_operational_profile=FIRE_STATION, simulations=())
    assert profile_for_scope(fire_deployment, OperationalScope.live()).profile_id == FIRE_STATION


def test_a_deployment_that_declares_nothing_stays_a_response_team():
    """Backward compatibility: an existing installation is never migrated."""

    legacy = SimpleNamespace(simulations=())

    assert profile_for_scope(legacy, OperationalScope.live()).profile_id == RESPONSE_TEAM


# --- §29 roster semantics ----------------------------------------------------


def test_each_organization_names_its_roster_and_members_differently():
    team = operational_profile(RESPONSE_TEAM)
    fire = operational_profile(FIRE_STATION)
    catalog = get_catalog("he")

    assert catalog.text(team.roster_label_key) != catalog.text(fire.roster_label_key)
    assert catalog.text(team.member_label_key) != catalog.text(fire.member_label_key)


def test_the_fire_roster_is_not_labelled_as_a_readiness_team():
    fire = operational_profile(FIRE_STATION)
    catalog = get_catalog("he")

    assert catalog.text(fire.roster_label_key) != catalog.text("profile.response_team.roster")


def test_membership_stays_inside_its_own_scope_regardless_of_organization(tmp_path):
    """Changing organization type never moves an identity between scopes."""

    store = open_team_status_persistence(str(tmp_path / "team.db"))
    sec = OperationalScope.simulation("SEC_001_PHASE_1", "run-a")
    fire = OperationalScope.simulation("FIRE_002_PHASE_1", "run-b")
    store.ensure_scope(sec, baseline={"team": {"members": [{"telegram_identity": "1", "full_name": "Eli"}], "approve": True}})
    store.ensure_scope(fire, baseline={"team": {"members": [{"telegram_identity": "9", "full_name": "Omri"}], "approve": True}})

    sec_ids = {m["telegram_identity"] for m in store.get_members(scope=sec)}
    fire_ids = {m["telegram_identity"] for m in store.get_members(scope=fire)}

    assert sec_ids == {"1"}
    assert fire_ids == {"9"}
    assert sec_ids.isdisjoint(fire_ids)


# --- §30 resource catalogue --------------------------------------------------


def test_fire_resources_resolve_through_the_fire_catalogue():
    fire = operational_profile(FIRE_STATION)

    resolved = fire.resolve_resources(FIRE_OPENING)

    assert [item["name"] for item in resolved] == ["ASHED", "CARMEL"]
    assert [item["count"] for item in resolved] == [3, 1]


def test_a_response_team_resolves_no_fire_resources():
    team = operational_profile(RESPONSE_TEAM)

    assert team.resolve_resources(FIRE_OPENING) == ()
    assert team.resources == ()


def test_an_unknown_resource_stays_unresolved():
    fire = operational_profile(FIRE_STATION)

    assert fire.resolve_resources("רכב נמר 7 יצא לדרך.") == ()


def test_the_generic_agent_no_longer_owns_canonical_fire_resource_names():
    from pathlib import Path

    source = Path(__file__).resolve().parent.parent / "agents" / "team_status_agent.py"
    body = source.read_text(encoding="utf-8")

    assert "ASHED" not in body
    assert "CARMEL" not in body


def test_extraction_resolves_resources_against_the_active_organization(tmp_path):
    from agents.team_status_agent import TeamStatusAgent

    class _Agent(TeamStatusAgent):
        status_db_path = str(tmp_path / "team.db")

        def __init__(self):
            self.status_store = open_team_status_persistence(self.status_db_path)

    agent = _Agent()
    occurred = "2026-09-09T07:00:00+00:00"

    as_fire = agent.extract_report(
        FIRE_OPENING, received_at=occurred, scenario_time=occurred, profile=operational_profile(FIRE_STATION)
    )
    as_team = agent.extract_report(
        FIRE_OPENING, received_at=occurred, scenario_time=occurred, profile=operational_profile(RESPONSE_TEAM)
    )

    assert set(as_fire.entities) == {"ASHED", "CARMEL"}
    assert as_fire.business_fields["resources_count"] == 2
    # The headcount is still read; only the vehicles are organization-specific.
    assert as_team.business_fields["manpower_count"] == 6
    assert as_team.business_fields["resources_count"] == 0
    assert as_team.entities == ()


# --- §33 protocol composition ------------------------------------------------


def test_each_organization_exposes_only_declared_protocols(deployment):
    fire = operational_profile(FIRE_STATION)

    exposed = fire.protocol_catalogue(deployment.protocols)
    exposed_names = {protocol.name for protocol in exposed}
    declared_names = {protocol.name for protocol in deployment.protocols}

    assert exposed_names <= declared_names, "filtering must never invent a protocol"
    assert exposed_names, "the fire station must expose something"


def test_a_profile_cannot_make_an_undeclared_protocol_executable():
    fire = operational_profile(FIRE_STATION)

    assert fire.protocol_catalogue(()) == ()
    assert fire.allows_protocol("hazmat_isolation_lockdown") is False


def test_protocol_filtering_is_tested_on_typed_registry_output(deployment):
    team = operational_profile(RESPONSE_TEAM)

    exposed = team.protocol_catalogue(deployment.protocols)

    assert all(hasattr(protocol, "name") and hasattr(protocol, "approved_tools") for protocol in exposed)


# --- §17/§31 context composition ---------------------------------------------


def test_both_organizations_enable_the_domains_the_core_supports():
    team = operational_profile(RESPONSE_TEAM)
    fire = operational_profile(FIRE_STATION)

    for profile in (team, fire):
        assert profile.allows_domain("team")
        assert profile.allows_domain("surveillance")
        assert profile.allows_agent("team_status_agent")

    assert fire.allows_domain("operational_facts")


# --- §25 concurrent runs -----------------------------------------------------


def test_two_organizations_coexist_without_process_global_state():
    team = operational_profile(RESPONSE_TEAM)
    fire = operational_profile(FIRE_STATION)

    assert current_operational_profile() is None
    with operational_profile_context(team):
        assert current_operational_profile().profile_id == RESPONSE_TEAM
        with operational_profile_context(fire):
            assert current_operational_profile().profile_id == FIRE_STATION
        assert current_operational_profile().profile_id == RESPONSE_TEAM
    assert current_operational_profile() is None


def test_the_profile_object_is_immutable():
    fire = operational_profile(FIRE_STATION)

    with pytest.raises(Exception):
        fire.profile_id = RESPONSE_TEAM


# --- §32 terminology ---------------------------------------------------------


@pytest.fixture
def hebrew_catalog():
    original = get_current_catalog()
    set_current_catalog(get_catalog("he"))
    try:
        yield
    finally:
        set_current_catalog(original)


def _snapshot():
    from orchestrator.situational_picture import (
        SituationalSnapshot,
        SnapshotProvenance,
        TeamSnapshot,
    )

    team = TeamSnapshot(
        total=3, available=2, unavailable=1, not_reported=0, pending_identity=0,
        status="ok",
        provenance=SnapshotProvenance(source="store", as_of="2026-09-09T11:30:00+00:00"),
        operational_manpower=6, effective_manpower=5, operational_resources=("ASHED-3",),
    )
    return SituationalSnapshot(
        cameras=None, drones=None, team=team, recent_count=0,
        relevant_recent_events=(), inconsistencies=(),
        generated_at="2026-09-09T11:30:00+00:00",
    )


def test_a_fire_picture_does_not_label_firefighters_as_a_readiness_team(hebrew_catalog):
    from orchestrator.situational_picture import render_typed_snapshot

    with operational_profile_context(operational_profile(FIRE_STATION)):
        text = render_typed_snapshot(_snapshot(), include_findings=False, include_recent_reports=False)

    catalog = get_catalog("he")
    assert catalog.text("profile.fire_station.roster") in text
    assert catalog.text("profile.response_team.roster") not in text


def test_a_response_team_picture_uses_its_own_roster_label(hebrew_catalog):
    from orchestrator.situational_picture import render_typed_snapshot

    with operational_profile_context(operational_profile(RESPONSE_TEAM)):
        text = render_typed_snapshot(_snapshot(), include_findings=False, include_recent_reports=False)

    assert get_catalog("he").text("profile.response_team.roster") in text


def test_an_unbound_render_keeps_the_previous_default_label(hebrew_catalog):
    """Backward compatibility for any path that has not yet bound a profile."""

    from orchestrator.situational_picture import render_typed_snapshot

    text = render_typed_snapshot(_snapshot(), include_findings=False, include_recent_reports=False)

    assert get_catalog("he").text("profile.response_team.roster") in text


# --- §28 scenario world materialization --------------------------------------


@pytest.fixture
def worlds(tmp_path):
    """Two agent-declared stores shared by every scope, as the runtime has."""

    from persistence import open_surveillance_persistence

    team = open_team_status_persistence(str(tmp_path / "team.db"))
    surveillance = open_surveillance_persistence(
        str(tmp_path / "surveillance.db"), seed_demo_data=True, seed_profile="profiles.unified_test"
    )
    return team, surveillance


def _materialize(team, surveillance, scope, members):
    baseline = {"team": {"members": members, "approve": True}}
    team.ensure_scope(scope, baseline=baseline)
    surveillance.ensure_scope(scope, baseline=baseline)


def test_a_new_sec_run_materializes_its_own_response_team_world(worlds):
    team, surveillance = worlds
    scope = OperationalScope.simulation("SEC_001_PHASE_1", "run-sec-a")

    _materialize(team, surveillance, scope, [{"telegram_identity": "1", "full_name": "Eli"}])

    assert {m["full_name"] for m in team.get_members(scope=scope)} == {"Eli"}
    assert surveillance.list_cameras(scope=scope)


def test_a_new_fire_run_materializes_its_own_fire_station_world(worlds):
    team, surveillance = worlds
    scope = OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire-a")

    _materialize(team, surveillance, scope, [{"telegram_identity": "9", "full_name": "Omri"}])

    assert {m["full_name"] for m in team.get_members(scope=scope)} == {"Omri"}


def test_loading_either_scenario_never_mutates_live(worlds):
    team, surveillance = worlds
    live_before = [dict(m) for m in team.get_members(approved_only=False, scope=OperationalScope.live())]

    _materialize(team, surveillance, OperationalScope.simulation("SEC_001_PHASE_1", "run-sec-b"),
                 [{"telegram_identity": "1", "full_name": "Eli"}])
    _materialize(team, surveillance, OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire-b"),
                 [{"telegram_identity": "9", "full_name": "Omri"}])

    live_after = [dict(m) for m in team.get_members(approved_only=False, scope=OperationalScope.live())]
    assert live_after == live_before


def test_loading_a_fire_run_does_not_mutate_a_sec_run(worlds):
    team, surveillance = worlds
    sec = OperationalScope.simulation("SEC_001_PHASE_1", "run-sec-c")
    _materialize(team, surveillance, sec, [{"telegram_identity": "1", "full_name": "Eli"}])
    sec_before = [dict(m) for m in team.get_members(scope=sec)]

    _materialize(team, surveillance, OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire-c"),
                 [{"telegram_identity": "9", "full_name": "Omri"}])

    assert [dict(m) for m in team.get_members(scope=sec)] == sec_before


def test_a_new_run_inherits_no_mutable_state_from_a_previous_run(worlds):
    from datetime import timedelta
    from persistence import runtime_now

    team, surveillance = worlds
    members = [{"telegram_identity": "9", "full_name": "Omri"}]
    first = OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire-1")
    _materialize(team, surveillance, first, members)

    opened = runtime_now()
    team.open_cycle(opened.isoformat()[:10], opened.isoformat(), (opened + timedelta(hours=1)).isoformat(), scope=first)
    team.record_operational_state(
        manpower_count=6, resources=[{"name": "ASHED", "count": 3, "status": "operational"}],
        source_event_id="e1", received_at=opened.isoformat(), scope=first,
    )
    surveillance.update_camera_feed("CAM-02", "offline for cleaning", status="offline",
                                    updated_at=opened.isoformat(), scope=first)

    second = OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire-2")
    _materialize(team, surveillance, second, members)

    assert team.operational_state(scope=second) is None
    assert team.latest_cycle(scope=second) is None
    fresh = next(c for c in surveillance.list_cameras(scope=second) if c["camera_id"] == "CAM-02")
    stale = next(c for c in surveillance.list_cameras(scope=first) if c["camera_id"] == "CAM-02")
    assert stale["status"] == "offline"
    assert fresh["status"] != "offline"


def test_a_resource_committed_in_a_fire_run_is_absent_from_a_sec_run(worlds):
    from persistence import runtime_now

    team, surveillance = worlds
    fire = OperationalScope.simulation("FIRE_002_PHASE_1", "run-fire-d")
    sec = OperationalScope.simulation("SEC_001_PHASE_1", "run-sec-d")
    _materialize(team, surveillance, fire, [{"telegram_identity": "9", "full_name": "Omri"}])
    _materialize(team, surveillance, sec, [{"telegram_identity": "1", "full_name": "Eli"}])

    team.record_operational_state(
        manpower_count=6, resources=[{"name": "ASHED", "count": 3, "status": "operational"}],
        source_event_id="e1", received_at=runtime_now().isoformat(), scope=fire,
    )

    assert team.operational_state(scope=fire)["resources"][0]["name"] == "ASHED"
    assert team.operational_state(scope=sec) is None


# =============================================================================
# Task 68 — a loaded run is a complete, inspectable world before step 1
# =============================================================================
#
# Task 67's validation surfaced the gap these cover: provisioning ran over the
# group-scoped registry that message routing produces, so a run received only
# the domain whose group happened to send the first message. The world is now
# materialized once, from the full registry, at run creation.


SEC_SCENARIO = "SEC_001_PHASE_1"
FIRE_SCENARIO = "FIRE_002_PHASE_1"


@pytest.fixture
def run_deployment():
    """The deployment as run creation sees it — personas included, since the
    scenario baseline resolves its roster through declared persona keys."""

    import profiles.unified_test as unified_test

    return SimpleNamespace(
        live_operational_profile=RESPONSE_TEAM,
        simulations=unified_test.SIMULATIONS,
        simulation_users=unified_test.SIMULATION_USERS,
        protocols=unified_test.PROTOCOLS,
    )


@pytest.fixture
def world(tmp_path):
    """Real agents over real stores, registered the way the server registers them."""

    from agents import AgentRegistry
    from agents.surveillance_agent import SurveillanceAgent
    from agents.team_status_agent import TeamStatusAgent

    class _Team(TeamStatusAgent):
        status_db_path = str(tmp_path / "team.db")

    class _Surveillance(SurveillanceAgent):
        surveillance_db_path = str(tmp_path / "surveillance.db")
        surveillance_seed_enabled = True
        surveillance_seed_profile = "profiles.unified_test"

    team = _Team("mock")
    surveillance = _Surveillance("mock")
    return SimpleNamespace(
        team=team.status_store,
        cameras=surveillance.surveillance_store,
        full=AgentRegistry({"team_status_agent": team, "surveillance_agent": surveillance}),
        # What `/Msg` narrows the registry to when a team message arrives.
        routed=AgentRegistry({"team_status_agent": team}),
    )


def _provision(deployment, registry, scope):
    from profiles import provision_operational_world

    return provision_operational_world(deployment, registry, scope)


# --- §23 a fresh response-team run, before any step --------------------------


def test_a_fresh_sec_run_is_complete_before_any_step(world, run_deployment):
    scope = OperationalScope.simulation(SEC_SCENARIO, "t68-sec-1")

    result = _provision(run_deployment, world.full, scope)

    assert result.ready is True
    assert result.failures == ()
    assert result.profile_id == RESPONSE_TEAM
    assert result.domains == ("surveillance_agent", "team_status_agent")

    assert len(world.team.get_members(scope=scope)) == 6, "the roster exists before step 1"
    assert world.cameras.list_cameras(scope=scope), "the camera world exists before any camera message"
    assert world.cameras.list_drones(scope=scope), "the drone world exists before any drone message"


# --- §24 a fresh fire-station run, before any step ---------------------------


def test_a_fresh_fire_run_is_complete_before_any_step(world, run_deployment):
    scope = OperationalScope.simulation(FIRE_SCENARIO, "t68-fire-1")

    result = _provision(run_deployment, world.full, scope)

    assert result.ready is True
    assert result.profile_id == FIRE_STATION
    assert result.domains == ("surveillance_agent", "team_status_agent")

    assert len(world.team.get_members(scope=scope)) == 3
    assert world.cameras.list_cameras(scope=scope)
    assert world.cameras.list_drones(scope=scope)

    # The catalogue is known before step 1; the quantities are not. Declaring
    # that a fire station understands water tenders gives this station none.
    assert [item.resource_id for item in operational_profile(FIRE_STATION).resources] == ["ASHED", "CARMEL"]
    assert world.team.operational_state(scope=scope) is None, (
        "resource quantities must come from a report, never from provisioning"
    )


def test_a_fresh_run_carries_no_attendance_or_operational_history(world, run_deployment):
    """A world may exist with an empty history — materialization reports nothing."""

    scope = OperationalScope.simulation(FIRE_SCENARIO, "t68-fire-empty")

    _provision(run_deployment, world.full, scope)

    assert world.team.latest_cycle(scope=scope) is None
    assert world.team.operational_state(scope=scope) is None


# --- §25 the first message updates the world, it does not create it ----------


def test_the_camera_world_exists_before_the_first_camera_message(world, run_deployment):
    """The regression this task exists to remove: a FIRE run used to raise
    'operational scope has not been initialized' on a camera read until a
    surveillance-group message happened to arrive."""

    scope = OperationalScope.simulation(FIRE_SCENARIO, "t68-fire-2")
    _provision(run_deployment, world.full, scope)

    before = {c["camera_id"]: c["status"] for c in world.cameras.list_cameras(scope=scope)}
    target = sorted(before)[0]

    world.cameras.update_camera_feed(
        target, "offline for cleaning", status="offline",
        updated_at="2026-09-09T10:00:00+00:00", scope=scope,
    )

    after = {c["camera_id"]: c["status"] for c in world.cameras.list_cameras(scope=scope)}
    assert set(after) == set(before), "the report changed state, it did not create the world"
    assert after[target] == "offline"


def test_provisioning_from_a_routed_registry_would_leave_a_domain_missing(world, run_deployment):
    """Pins the root cause, so provisioning can never inherit routing's narrowing again."""

    from persistence import SurveillancePersistenceError

    scope = OperationalScope.simulation(FIRE_SCENARIO, "t68-fire-3")

    routed = _provision(run_deployment, world.routed, scope)
    assert routed.domains == ("team_status_agent",)
    with pytest.raises(SurveillancePersistenceError):
        world.cameras.list_cameras(scope=scope)

    complete = _provision(run_deployment, world.full, scope)
    assert complete.domains == ("surveillance_agent", "team_status_agent")
    assert world.cameras.list_cameras(scope=scope)


# --- §26 re-provisioning is idempotent ---------------------------------------


def test_re_provisioning_a_running_scenario_never_resets_its_state(world, run_deployment):
    from datetime import timedelta

    from persistence import runtime_now

    scope = OperationalScope.simulation(SEC_SCENARIO, "t68-sec-2")
    _provision(run_deployment, world.full, scope)

    target = sorted(c["camera_id"] for c in world.cameras.list_cameras(scope=scope))[0]
    world.cameras.update_camera_feed(
        target, "intermittent interference", status="degraded",
        updated_at="2026-09-06T08:15:00+00:00", scope=scope,
    )
    opened = runtime_now()
    world.team.open_cycle(
        opened.isoformat()[:10], opened.isoformat(), (opened + timedelta(hours=1)).isoformat(), scope=scope
    )
    members_before = [dict(m) for m in world.team.get_members(scope=scope)]

    again = _provision(run_deployment, world.full, scope)

    assert again.ready is True
    cameras = {c["camera_id"]: c["status"] for c in world.cameras.list_cameras(scope=scope)}
    assert cameras[target] == "degraded", "re-ensure must not reset mutated state to baseline"
    assert world.team.latest_cycle(scope=scope) is not None
    assert [dict(m) for m in world.team.get_members(scope=scope)] == members_before


# --- §27 a fresh run of the same fixture starts from baseline ----------------


def test_a_fresh_run_of_the_same_fixture_inherits_nothing(world, run_deployment):
    from persistence import runtime_now

    first = OperationalScope.simulation(SEC_SCENARIO, "t68-sec-a")
    _provision(run_deployment, world.full, first)
    target = sorted(c["camera_id"] for c in world.cameras.list_cameras(scope=first))[0]
    world.cameras.update_camera_feed(
        target, "interference", status="degraded",
        updated_at="2026-09-06T08:15:00+00:00", scope=first,
    )
    world.team.record_operational_state(
        manpower_count=4, resources=[], source_event_id="e1",
        received_at=runtime_now().isoformat(), scope=first,
    )

    second = OperationalScope.simulation(SEC_SCENARIO, "t68-sec-b")
    _provision(run_deployment, world.full, second)

    first_cameras = {c["camera_id"]: c["status"] for c in world.cameras.list_cameras(scope=first)}
    second_cameras = {c["camera_id"]: c["status"] for c in world.cameras.list_cameras(scope=second)}
    assert first_cameras[target] == "degraded"
    assert second_cameras[target] != "degraded", "a new run must not inherit the previous run's state"
    assert world.team.operational_state(scope=second) is None


# --- §28 two organizations run side by side ----------------------------------


def test_two_organizations_run_side_by_side_without_leaking(world, run_deployment):
    from persistence import runtime_now

    sec = OperationalScope.simulation(SEC_SCENARIO, "t68-x-sec")
    fire = OperationalScope.simulation(FIRE_SCENARIO, "t68-x-fire")
    sec_result = _provision(run_deployment, world.full, sec)
    fire_result = _provision(run_deployment, world.full, fire)

    assert (sec_result.profile_id, fire_result.profile_id) == (RESPONSE_TEAM, FIRE_STATION)
    sec_names = {m["full_name"] for m in world.team.get_members(scope=sec)}
    fire_names = {m["full_name"] for m in world.team.get_members(scope=fire)}
    assert sec_names.isdisjoint(fire_names)

    target = sorted(c["camera_id"] for c in world.cameras.list_cameras(scope=fire))[0]
    world.cameras.update_camera_feed(
        target, "offline for cleaning", status="offline",
        updated_at="2026-09-09T10:00:00+00:00", scope=fire,
    )
    world.team.record_operational_state(
        manpower_count=6, resources=[{"name": "ASHED", "count": 3, "status": "operational"}],
        source_event_id="e1", received_at=runtime_now().isoformat(), scope=fire,
    )

    sec_cameras = {c["camera_id"]: c["status"] for c in world.cameras.list_cameras(scope=sec)}
    assert sec_cameras[target] != "offline", "camera state must not cross runs"
    assert world.team.operational_state(scope=sec) is None, "fire resources must not cross runs"
    assert world.team.operational_state(scope=fire)["manpower_count"] == 6


# --- §29 LIVE is never touched -----------------------------------------------


def test_provisioning_simulation_runs_never_mutates_live(world, run_deployment):
    live = OperationalScope.live()
    members_before = [dict(m) for m in world.team.get_members(approved_only=False, scope=live)]
    state_before = world.team.operational_state(scope=live)
    cameras_before = sorted((c["camera_id"], c["status"]) for c in world.cameras.list_cameras(scope=live))
    drones_before = sorted((d["drone_id"], d["status"]) for d in world.cameras.list_drones(scope=live))

    _provision(run_deployment, world.full, OperationalScope.simulation(SEC_SCENARIO, "t68-live-sec"))
    _provision(run_deployment, world.full, OperationalScope.simulation(FIRE_SCENARIO, "t68-live-fire"))

    assert [dict(m) for m in world.team.get_members(approved_only=False, scope=live)] == members_before
    assert world.team.operational_state(scope=live) == state_before
    assert sorted((c["camera_id"], c["status"]) for c in world.cameras.list_cameras(scope=live)) == cameras_before
    assert sorted((d["drone_id"], d["status"]) for d in world.cameras.list_drones(scope=live)) == drones_before


# --- a domain that cannot be materialized is reported, not skipped -----------


def test_a_domain_that_cannot_be_provisioned_is_reported_not_skipped(world, run_deployment):
    from agents import AgentRegistry

    class _BrokenAgent:
        name = "broken_agent"

        def ensure_operational_scope(self, scope, baseline=None):
            raise RuntimeError("store unavailable")

    registry = AgentRegistry({
        "team_status_agent": world.full.get("team_status_agent"),
        "broken_agent": _BrokenAgent(),
    })
    scope = OperationalScope.simulation(SEC_SCENARIO, "t68-broken")

    result = _provision(run_deployment, registry, scope)

    assert result.ready is False
    assert any("broken_agent" in failure for failure in result.failures)
    assert result.domains == ("team_status_agent",), "a healthy domain is still reported"


# --- run creation is worth one log line; a per-message re-ensure is not ------


def test_run_creation_announces_the_world_but_the_per_request_re_ensure_does_not(
    world, run_deployment, caplog
):
    scope = OperationalScope.simulation(SEC_SCENARIO, "t68-log")

    with caplog.at_level(logging.INFO, logger="profiles.simulation_provisioning"):
        _provision(run_deployment, world.full, scope)
        created = [r.event for r in caplog.records if hasattr(r, "event")]

        caplog.clear()
        from profiles import initialize_operational_scope

        initialize_operational_scope(run_deployment, world.full, scope)
        re_ensured = [r.event for r in caplog.records if hasattr(r, "event")]

    assert created == ["run_provisioned"]
    assert re_ensured == [], "re-ensuring an existing world must not log once per message"


def test_an_incomplete_world_is_logged_even_from_the_silent_path(world, run_deployment, caplog):
    from agents import AgentRegistry
    from profiles import initialize_operational_scope

    class _BrokenAgent:
        name = "broken_agent"

        def ensure_operational_scope(self, scope, baseline=None):
            raise RuntimeError("store unavailable")

    registry = AgentRegistry({"broken_agent": _BrokenAgent()})
    scope = OperationalScope.simulation(SEC_SCENARIO, "t68-log-broken")

    with caplog.at_level(logging.ERROR, logger="profiles.simulation_provisioning"):
        initialize_operational_scope(run_deployment, registry, scope)

    assert [r.event for r in caplog.records if hasattr(r, "event")] == ["run_provisioning_failed"]
