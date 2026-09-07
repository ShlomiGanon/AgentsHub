import uuid

import pytest

from cli.user_admin import main
from persistence.sqlite_store import SQLitePersistence
from tests.helpers import write_profile_module

BOT_TOKEN_ENV = "TEST_USER_ADMIN_BOT_TOKEN"
MODEL_CRED_ENV = "TEST_USER_ADMIN_MODEL_KEY"


@pytest.fixture
def profile_module(tmp_path, monkeypatch):
    # Python caches imported modules by name — a name reused across tests
    # would silently resolve to whichever test imported it first, pointing
    # every later test at that first test's database. One unique name per
    # test avoids that.
    module_name = f"user_admin_test_profile_{uuid.uuid4().hex}"

    db_path = tmp_path / "deployment.db"
    write_profile_module(
        tmp_path,
        monkeypatch,
        module_name,
        bot_token_env=BOT_TOKEN_ENV,
        model_cred_env=MODEL_CRED_ENV,
        overrides={"DB_PATH": f"DB_PATH = {str(db_path)!r}"},
    )
    monkeypatch.setenv(BOT_TOKEN_ENV, "token")
    monkeypatch.setenv(MODEL_CRED_ENV, "key")
    return module_name, db_path


def test_add_first_commander_against_an_empty_database(profile_module, capsys, real_tier_env):
    module_name, db_path = profile_module

    exit_code = main(["--profile", module_name, "add", "--telegram-id", "1001", "--level", "commander"])

    assert exit_code == 0
    assert "commander" in capsys.readouterr().out

    store = SQLitePersistence(str(db_path))
    try:
        assert store.read_user("1001") == {"telegram_identity": "1001", "permission_level": "commander"}
    finally:
        store.close()


def test_update_changes_an_existing_users_level(profile_module, real_tier_env):
    module_name, db_path = profile_module

    main(["--profile", module_name, "add", "--telegram-id", "2002", "--level", "viewer"])
    main(["--profile", module_name, "update", "--telegram-id", "2002", "--level", "commander"])

    store = SQLitePersistence(str(db_path))
    try:
        assert store.read_user("2002")["permission_level"] == "commander"
    finally:
        store.close()


def test_remove_deletes_a_user(profile_module, real_tier_env):
    module_name, db_path = profile_module

    main(["--profile", module_name, "add", "--telegram-id", "3003", "--level", "viewer"])
    exit_code = main(["--profile", module_name, "remove", "--telegram-id", "3003"])

    assert exit_code == 0
    store = SQLitePersistence(str(db_path))
    try:
        assert store.read_user("3003") is None
    finally:
        store.close()


def test_remove_unknown_user_fails_with_nonzero_exit(profile_module, capsys, real_tier_env):
    module_name, _ = profile_module

    exit_code = main(["--profile", module_name, "remove", "--telegram-id", "does-not-exist"])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_list_reports_every_registered_user(profile_module, capsys, real_tier_env):
    module_name, _ = profile_module

    main(["--profile", module_name, "add", "--telegram-id", "4004", "--level", "viewer"])
    main(["--profile", module_name, "add", "--telegram-id", "5005", "--level", "commander"])
    capsys.readouterr()  # discard the add commands' output

    main(["--profile", module_name, "list"])

    out = capsys.readouterr().out
    assert "4004" in out and "viewer" in out
    assert "5005" in out and "commander" in out


def test_level_outside_the_enum_is_rejected(profile_module, real_tier_env):
    module_name, _ = profile_module

    with pytest.raises(SystemExit):
        main(["--profile", module_name, "add", "--telegram-id", "6006", "--level", "supreme_leader"])


def test_unknown_profile_fails_before_touching_any_database(capsys, real_tier_env):
    exit_code = main(["--profile", "no_such_profile_module", "add", "--telegram-id", "1", "--level", "viewer"])

    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_main_fails_loudly_naming_the_missing_tier_env_var(monkeypatch, capsys):
    for name in ("CORE_MODEL_PROVIDER", "CORE_MODEL_NAME", "CORE_MODEL_API_KEY_ENV"):
        monkeypatch.delenv(name, raising=False)

    exit_code = main(["--profile", "fixtures.profiles.minimal_profile", "list"])

    assert exit_code == 1
    assert "CORE_MODEL_PROVIDER" in capsys.readouterr().err
