import profiles.sub_agent_surveillance as profile_module
from profiles import load_profile


def test_surveillance_profile_loads_with_isolated_databases(monkeypatch, tmp_path, test_core_model, test_sub_model):
    monkeypatch.setenv("SURVEILLANCE_BOT_TOKEN", "dedicated-bot-token")
    monkeypatch.setattr(profile_module, "DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setattr(profile_module, "SURVEILLANCE_DB_PATH", str(tmp_path / "surveillance.db"))
    monkeypatch.setattr(
        profile_module,
        "RESETTABLE_DATABASES",
        (profile_module.DB_PATH, profile_module.SURVEILLANCE_DB_PATH),
    )
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
    assert len(profile_module.PROTOCOLS) == 8
    protocol_map = {p.name: p for p in profile_module.PROTOCOLS}

    assert "query_camera_status" in protocol_map
    query_proto = protocol_map["query_camera_status"]
    assert "get_camera_feeds" in query_proto.approved_tools
    assert query_proto.approved_tools == ("get_camera_feeds",)
    assert query_proto.approval_flag is False

    assert protocol_map["query_drone_fleet_status"].approved_tools == ("get_drone_fleet_status",)
    assert protocol_map["query_active_drone_missions"].approved_tools == ("get_active_missions",)
    assert protocol_map["query_surveillance_overview"].approved_tools == ("get_surveillance_overview",)

    assert "dispatch_drone_to_incident" in protocol_map
    dispatch_proto = protocol_map["dispatch_drone_to_incident"]
    assert dispatch_proto.approved_tools == ("dispatch_drone_to_area",)
    assert dispatch_proto.approval_flag is True

    recall_proto = protocol_map["return_drone_to_base"]
    assert recall_proto.approved_tools == ("return_drone_to_base",)
    assert recall_proto.approval_flag is True

    assert "surveillance_area_scan" in protocol_map
    assert protocol_map["surveillance_area_scan"].approved_tools == ("get_surveillance_overview",)
    assert protocol_map["update_camera_observation"].approval_flag is True
    assert profile_module.MAX_ITER == 2
    assert profile_module.RETRY_COUNT == 1

    assert profile_module.BOT_TOKEN_ENV == "SURVEILLANCE_BOT_TOKEN"
    assert profile_module.SURVEILLANCE_CHAT_ID_ENV == "SURVEILLANCE_CHAT_ID"
    assert "north_gate" in profile_module.AREAS
    assert "surveillance_report" in profile_module.EVENT_TYPES
    assert "drone_recall" in profile_module.EVENT_TYPES
