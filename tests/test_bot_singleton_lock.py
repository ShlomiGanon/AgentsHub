"""bot/singleton_lock.py (work_plan.md §8.1's "run one bot per deployment")."""

import pytest

from bot.singleton_lock import AlreadyRunningError, SingleInstanceLock


def test_acquire_creates_the_lock_file_with_the_pid(tmp_path):
    import os

    lock_path = tmp_path / "deployment.db.bot.lock"
    lock = SingleInstanceLock(lock_path)

    lock.acquire()

    assert lock_path.exists()
    assert lock_path.read_text() == str(os.getpid())
    lock.release()


def test_a_second_lock_on_the_same_path_is_refused(tmp_path):
    lock_path = tmp_path / "deployment.db.bot.lock"
    first = SingleInstanceLock(lock_path)
    second = SingleInstanceLock(lock_path)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_release_removes_the_lock_file_so_a_new_process_can_start(tmp_path):
    lock_path = tmp_path / "deployment.db.bot.lock"
    first = SingleInstanceLock(lock_path)
    first.acquire()
    first.release()

    assert not lock_path.exists()

    second = SingleInstanceLock(lock_path)
    second.acquire()
    second.release()


def test_release_without_acquire_does_not_raise(tmp_path):
    lock = SingleInstanceLock(tmp_path / "never_acquired.lock")
    lock.release()  # must not raise


def test_context_manager_releases_on_exit(tmp_path):
    lock_path = tmp_path / "deployment.db.bot.lock"

    with SingleInstanceLock(lock_path):
        assert lock_path.exists()

    assert not lock_path.exists()
