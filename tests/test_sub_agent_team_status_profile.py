import profiles.sub_agent_team_status as profile_module
from profiles import load_profile


def test_profile_loads_with_isolated_databases(monkeypatch, tmp_path, test_core_model, test_sub_model):
    monkeypatch.setenv("TEAM_STATUS_BOT_TOKEN", "dedicated-bot-token")
    monkeypatch.setattr(profile_module, "DB_PATH", str(tmp_path / "history.db"))
    monkeypatch.setattr(profile_module, "TEAM_STATUS_DB_PATH", str(tmp_path / "team-status.db"))
    monkeypatch.setattr(profile_module.SubAgentTeamStatusAgent, "status_db_path", profile_module.TEAM_STATUS_DB_PATH)

    loaded = load_profile(
        "profiles.sub_agent_team_status",
        core_model=test_core_model,
        sub_model=test_sub_model,
    )

    assert loaded.profile_name == "sub agent team status"
    assert loaded.default_language == "he"
    assert loaded.api_port == 8903
    assert loaded.db_path != profile_module.TEAM_STATUS_DB_PATH
    assert tuple(agent.name for agent in loaded.agents) == ("team_status_agent",)


def test_profile_exposes_only_the_approved_status_protocol():
    assert len(profile_module.PROTOCOLS) == 1
    protocol = profile_module.PROTOCOLS[0]
    assert protocol.name == "report_team_availability"
    assert protocol.participating_agents == ("team_status_agent",)
    assert protocol.approved_tools == ("report_team_availability",)
    assert protocol.approval_flag is False
    assert profile_module.BOT_TOKEN_ENV == "TEAM_STATUS_BOT_TOKEN"
    assert profile_module.TEAM_STATUS_CHAT_ID_ENV == "TEAM_STATUS_CHAT_ID"
    assert profile_module.SubAgentTeamStatusAgent.attendance_check_hour == 8
    assert profile_module.SubAgentTeamStatusAgent.response_window_hours == 1
