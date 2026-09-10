import uuid

import pytest

from cli.group_admin import main
from persistence.sqlite_store import SQLitePersistence
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_GROUP_ADMIN_BOT_TOKEN"
MODEL_CRED_ENV = "TEST_GROUP_ADMIN_MODEL_KEY"


@pytest.fixture
def profile_module(tmp_path, monkeypatch):
    module_name = f"group_admin_test_profile_{uuid.uuid4().hex}"

    db_path = tmp_path / "deployment.db"
    write_profile_module(
        tmp_path,
        monkeypatch,
        module_name,
        bot_token_env=BOT_TOKEN_ENV,
        model_cred_env=MODEL_CRED_ENV,
        extra_prelude="from agents.reference import ReferenceAgent\nfrom profiles.spec import AgentSpec\n",
        overrides={
            "DB_PATH": f"DB_PATH = {str(db_path)!r}",
            # One declared specialist, so the command has a real agent name to validate against.
            "AGENTS": 'AGENTS = [AgentSpec(cls=ReferenceAgent, tier="sub")]',
        },
    )
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")
    return module_name, db_path


def _groups(db_path):
    store = SQLitePersistence(str(db_path))
    try:
        return {group["chat_id"]: group for group in store.list_groups()}
    finally:
        store.close()


def test_add_binds_a_group_to_main_agent(profile_module, capsys, real_tier_env):
    module_name, db_path = profile_module

    exit_code = main(["--profile", module_name, "add", "--chat-id", "-1001", "--agent", "main_agent", "--label", "ops"])

    assert exit_code == 0
    assert "main_agent" in capsys.readouterr().out
    stored = _groups(db_path)["-1001"]
    assert stored["agent_name"] == "main_agent"
    assert stored["label"] == "ops"


def test_add_accepts_a_profile_specialist_by_name(profile_module, real_tier_env):
    module_name, db_path = profile_module

    exit_code = main(["--profile", module_name, "add", "--chat-id", "-1002", "--agent", "reference_agent"])

    assert exit_code == 0
    assert _groups(db_path)["-1002"]["agent_name"] == "reference_agent"


def test_add_rejects_an_agent_the_profile_does_not_declare(profile_module, capsys, real_tier_env):
    module_name, db_path = profile_module

    exit_code = main(["--profile", module_name, "add", "--chat-id", "-1003", "--agent", "no_such_agent"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "no_such_agent" in err and "main_agent" in err
    assert "-1003" not in _groups(db_path)


def test_update_rebinds_an_existing_group(profile_module, real_tier_env):
    module_name, db_path = profile_module

    main(["--profile", module_name, "add", "--chat-id", "-1004", "--agent", "reference_agent"])
    main(["--profile", module_name, "update", "--chat-id", "-1004", "--agent", "main_agent", "--label", "all"])

    stored = _groups(db_path)["-1004"]
    assert stored["agent_name"] == "main_agent"
    assert stored["label"] == "all"


def test_remove_deletes_a_binding_and_unknown_fails(profile_module, capsys, real_tier_env):
    module_name, db_path = profile_module

    main(["--profile", module_name, "add", "--chat-id", "-1005", "--agent", "main_agent"])
    assert main(["--profile", module_name, "remove", "--chat-id", "-1005"]) == 0
    assert "-1005" not in _groups(db_path)

    assert main(["--profile", module_name, "remove", "--chat-id", "-1005"]) == 1
    assert "error" in capsys.readouterr().err


def test_list_reports_every_binding(profile_module, capsys, real_tier_env):
    module_name, _ = profile_module

    main(["--profile", module_name, "add", "--chat-id", "-1006", "--agent", "main_agent", "--label", "one"])
    main(["--profile", module_name, "add", "--chat-id", "-1007", "--agent", "reference_agent"])
    capsys.readouterr()

    main(["--profile", module_name, "list"])

    out = capsys.readouterr().out
    assert "-1006\tmain_agent\tone" in out
    assert "-1007\treference_agent\t" in out


def test_unknown_profile_fails_before_touching_any_database(capsys, real_tier_env):
    exit_code = main(["--profile", "no_such_profile_module", "list"])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err
