import sys
from types import ModuleType

import pytest

import run_stack
from run_stack import reset_profile_databases


def test_main_starts_the_last_selected_profile(monkeypatch):
    started = []

    class FakeSupervisor:
        def __init__(self, profile_module):
            self.profile_module = profile_module

        def run(self):
            started.append(self.profile_module)

    monkeypatch.setattr(run_stack, "load_dotenv", lambda path: True)
    monkeypatch.setattr(run_stack, "load_selected_profile", lambda: "profiles.firefighting")
    monkeypatch.setattr(run_stack, "StackSupervisor", FakeSupervisor)

    run_stack.main()

    assert started == ["profiles.firefighting"]


def test_reset_removes_only_declared_databases_and_known_sidecars(tmp_path):
    database = tmp_path / "main.db"
    settings = tmp_path / "main.db.settings.json"
    unrelated = tmp_path / "keep.txt"
    for path in (database, settings, unrelated):
        path.write_text("x", encoding="utf-8")
    module = ModuleType("profiles.test_reset_profile")
    module.DB_PATH = str(database)
    module.RESETTABLE_DATABASES = (str(database),)
    sys.modules[module.__name__] = module
    try:
        removed = reset_profile_databases(module.__name__)
    finally:
        sys.modules.pop(module.__name__, None)
    assert set(removed) == {database.resolve(), settings.resolve()}
    assert unrelated.exists()


def test_reset_refuses_a_declared_non_database_path(tmp_path):
    module = ModuleType("profiles.test_unsafe_reset_profile")
    module.DB_PATH = str(tmp_path)
    module.RESETTABLE_DATABASES = (str(tmp_path),)
    sys.modules[module.__name__] = module
    try:
        with pytest.raises(ValueError, match="non-database"):
            reset_profile_databases(module.__name__)
    finally:
        sys.modules.pop(module.__name__, None)
