import pytest

from api.auth import authenticate, require
from api.errors import AuthenticationError, AuthorizationError
from auth.permissions import PermissionLevel
from persistence.sqlite_backend import SQLitePersistence


@pytest.fixture
def store(tmp_path):
    backend = SQLitePersistence(str(tmp_path / "auth_test.db"))
    backend.write_user("viewer-1", "viewer")
    backend.write_user("commander-1", "commander")
    yield backend
    backend.close()


def test_authenticate_returns_the_registered_level(store):
    assert authenticate(store, "viewer-1") == PermissionLevel.VIEWER
    assert authenticate(store, "commander-1") == PermissionLevel.COMMANDER


def test_authenticate_rejects_an_unregistered_identity(store):
    with pytest.raises(AuthenticationError):
        authenticate(store, "nobody")


def test_authenticate_rejects_a_missing_identity(store):
    with pytest.raises(AuthenticationError):
        authenticate(store, None)

    with pytest.raises(AuthenticationError):
        authenticate(store, "")


def test_require_permits_an_action_at_or_above_its_level():
    require(PermissionLevel.COMMANDER, "approve_run")  # does not raise
    require(PermissionLevel.VIEWER, "send_message")  # does not raise


def test_require_rejects_an_action_below_its_level():
    with pytest.raises(AuthorizationError):
        require(PermissionLevel.VIEWER, "approve_run")
