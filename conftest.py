"""Ensures the repo root is on sys.path regardless of how pytest is invoked,
so `import persistence.interface`, `import tests.helpers`, etc. resolve the
same way whether run as `pytest` or `python -m pytest`.

Also defines `test_core_model`/`test_sub_model` (below) — the test
suite's own model-tier config source, built from `TEST_`-prefixed real
process environment variables (shell export, CI secrets, ...), never a
file. Every function in the model-tier chain (`config.base.build_tier_model`/
`load_base_config`, `profiles.loader.load_profile`, `api.app.build_context`,
`bot.app.build_deps`) takes already-resolved `config.base.TierModel`
values as specific named parameters — none of them read `os.environ`, or
take a `Mapping` representing it, at all. Only four places in the
production system and automated test suite read `os.environ` for
model-tier config: `api.app.main`, `bot.app.main`, `cli.user_admin.main`,
and these two fixtures. (A handful of standalone, hand-run scripts under
`tests/` — never collected by pytest, never imported by anything — read
model-tier-shaped variables of their own accord too; see
`docs/profile_spec.md`'s "Model tiers" section.)

`real_tier_env` (below) is not a fifth such place — it never reads a
resolved value from `os.environ` itself. It only *writes* the real
`CORE_MODEL_*`/`SUB_MODEL_*` variables (mirroring `test_core_model`/
`test_sub_model`'s already-configured `TEST_` values) for the rare test
that must exercise one of the three real `main()` entry points end to
end, so that root's own environment read has something real to find.
"""

import os
import sys
from pathlib import Path

import pytest

from config.base import TierModel, build_tier_model

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _require_test_env(name: str) -> str:
    value = os.environ.get(f"TEST_{name}")
    if value is None:
        pytest.fail(
            f"Missing required environment variable TEST_{name} for the test suite. "
            "See docs/profile_spec.md's \"Model tiers\" section."
        )
    return value


def _tier_model_from_test_environ(prefix: str) -> TierModel:
    """Read one tier's provider/model name/API key from `TEST_`-prefixed
    real process environment variables — e.g. `TEST_CORE_MODEL_PROVIDER`
    for `prefix="CORE"`. The `*_MODEL_API_KEY_ENV` indirection is itself
    `TEST_`-prefixed too: if `TEST_CORE_MODEL_API_KEY_ENV=CORE_MODEL_KEY`,
    the actual key comes from `TEST_CORE_MODEL_KEY`.
    """

    provider = _require_test_env(f"{prefix}_MODEL_PROVIDER")
    model_name = _require_test_env(f"{prefix}_MODEL_NAME")
    api_key_env_name = _require_test_env(f"{prefix}_MODEL_API_KEY_ENV")
    api_key = _require_test_env(api_key_env_name)
    return build_tier_model(provider, model_name, api_key)


@pytest.fixture
def test_core_model() -> TierModel:
    """The "core" tier's `TierModel`, resolved from `TEST_CORE_MODEL_*`
    environment variables — pass as `core_model=` to whichever of
    `load_base_config`/`load_profile`/`build_context`/`build_deps` a test
    is exercising.
    """

    return _tier_model_from_test_environ("CORE")


@pytest.fixture
def test_sub_model() -> TierModel:
    """The "sub" tier's `TierModel`, resolved from `TEST_SUB_MODEL_*`
    environment variables — pass as `sub_model=` to whichever of
    `load_profile`/`build_context`/`build_deps` a test is exercising.
    """

    return _tier_model_from_test_environ("SUB")


def _mirror_real_tier_env(monkeypatch, prefix: str) -> None:
    provider = _require_test_env(f"{prefix}_MODEL_PROVIDER")
    model_name = _require_test_env(f"{prefix}_MODEL_NAME")
    api_key_env_name = _require_test_env(f"{prefix}_MODEL_API_KEY_ENV")
    api_key = _require_test_env(api_key_env_name)

    monkeypatch.setenv(f"{prefix}_MODEL_PROVIDER", provider)
    monkeypatch.setenv(f"{prefix}_MODEL_NAME", model_name)
    monkeypatch.setenv(f"{prefix}_MODEL_API_KEY_ENV", api_key_env_name)
    monkeypatch.setenv(api_key_env_name, api_key)


@pytest.fixture
def real_tier_env(monkeypatch) -> None:
    """Set the real `CORE_MODEL_*`/`SUB_MODEL_*` environment variables that
    a root `main()` (`api.app.main`, `bot.app.main`, `cli.user_admin.main`)
    reads directly, mirroring whatever `TEST_CORE_MODEL_*`/`TEST_SUB_MODEL_*`
    values the suite is already configured with. Only needed by a test that
    exercises one of those three real entry points end-to-end — everything
    below them takes an explicit `core_model=`/`sub_model=` `TierModel`
    instead (see `test_core_model`/`test_sub_model`, above), and needs no
    real environment variables at all.
    """

    _mirror_real_tier_env(monkeypatch, "CORE")
    _mirror_real_tier_env(monkeypatch, "SUB")
