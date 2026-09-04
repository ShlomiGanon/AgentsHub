import profiles.sub_agent_surveillance as profile_module
from profiles import load_profile


def test_surveillance_profile_loads_with_isolated_databases(monkeypatch, tmp_path, test_core_model, test_sub_model):
    monkeypatch.setenv("SURVEILLANCE_BOT_TOKEN", "dedicated-bot-token")
    monkeypatch.setattr(profile_module, "DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setattr(profile_module, "SURVEILLANCE_DB_PATH", str(tmp_path / "surveillance.db"))
    monkeypatch.setattr(profile_module.SubAgentSurveillanceAgent, "surveillance_db_path", profile_module.SURVEILLANCE_DB_PATH)

    loaded = load_profile(
        "profiles.sub_agent_surveillance",
        core_model=test_core_model,
        sub_model=test_sub_model,
    )

    assert loaded.profile_name == "sub agent surveillance"
    assert loaded.default_language == "he"
    assert loaded.api_port == 8904
    assert loaded.db_path != profile_module.SURVEILLANCE_DB_PATH
    assert tuple(agent.name for agent in loaded.agents) == ("surveillance_agent",)


def test_surveillance_profile_protocols_and_attributes():
    assert len(profile_module.PROTOCOLS) == 3
    protocol_map = {p.name: p for p in profile_module.PROTOCOLS}

    assert "query_surveillance_status" in protocol_map
    query_proto = protocol_map["query_surveillance_status"]
    assert "get_camera_feeds" in query_proto.approved_tools
    assert "get_drone_fleet_status" in query_proto.approved_tools
    assert "get_active_missions" in query_proto.approved_tools
    assert query_proto.approval_flag is False

    assert "dispatch_drone_to_incident" in protocol_map
    dispatch_proto = protocol_map["dispatch_drone_to_incident"]
    assert "dispatch_drone_to_area" in dispatch_proto.approved_tools

    assert "surveillance_area_scan" in protocol_map

    assert profile_module.BOT_TOKEN_ENV in ("SURVEILLANCE_BOT_TOKEN", "BOT_TOKEN")
    assert profile_module.SURVEILLANCE_CHAT_ID_ENV in ("SURVEILLANCE_CHAT_ID", "TEAM_STATUS_CHAT_ID")
    assert "north_gate" in profile_module.AREAS
    assert "surveillance_report" in profile_module.EVENT_TYPES
