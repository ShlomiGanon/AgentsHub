"""Small duck-typed stand-ins for Agent/Protocol, used only by tests.

Sections 3/4 haven't landed the real classes yet — profiles.validate
checks structural shape (see profiles/spec.py), so these fakes are enough
to exercise every branch of that validation without depending on fixtures
meant to represent a *valid* profile. `criticality` is the one field
`profiles.validate` requires to be a real `CriticalityLevel` enum member
specifically (§1.6, tightened after the Mission 8 coverage audit) — every
other field here stays a plain, minimal stand-in.
"""

from dataclasses import dataclass, field

from protocols.model import CriticalityLevel


@dataclass(frozen=True)
class FakeAgent:
    name: str
    tools: tuple = ()

    def exposed_tools(self):
        return self.tools


@dataclass(frozen=True)
class FakeProtocol:
    name: str = "fake_protocol"
    description: str = "a description"
    participating_agents: tuple = ()
    approved_tools: tuple = ()
    expected_success_output: str = "a description of what success looks like"
    criticality: object = CriticalityLevel.LOW
    approval_flag: object = False


class ShapelessProtocol:
    """Exposes none of the attributes profiles.spec requires — used to
    exercise the missing-attribute branch of validation.
    """


# A complete, valid set of profile module attribute assignments. Tests that
# need a profile module on disk (profiles.loader reads real files, not
# in-memory objects) start from this and omit/prepend what the test needs.
PROFILE_ATTR_LINES = {
    "AGENTS": "AGENTS = []",
    "PROTOCOLS": "PROTOCOLS = []",
    "EVENT_TYPES": 'EVENT_TYPES = ["fire"]',
    "AREAS": 'AREAS = ["north"]',
    "DB_PATH": 'DB_PATH = "test.db"',
    "API_PORT": "API_PORT = 9999",
    "RETRY_COUNT": "RETRY_COUNT = 1",
    "RISK_THRESHOLD": "RISK_THRESHOLD = 0.5",
    "LOOKBACK_WINDOW_DAYS": "LOOKBACK_WINDOW_DAYS = 10",
}


def write_profile_module(tmp_path, monkeypatch, module_name, *, bot_token_env, model_cred_env, omit=(), overrides=None, extra_prelude=""):
    """Write a syntactically real profile module under `tmp_path` and put
    `tmp_path` on sys.path, so profiles.loader.load_profile can import it
    by name. Returns nothing — call load_profile(module_name) after.
    """

    monkeypatch.syspath_prepend(str(tmp_path))

    lines = dict(PROFILE_ATTR_LINES)
    lines["BOT_TOKEN_ENV"] = f'BOT_TOKEN_ENV = "{bot_token_env}"'
    lines["MODEL_CREDENTIAL_ENVS"] = f'MODEL_CREDENTIAL_ENVS = ["{model_cred_env}"]'
    lines.update(overrides or {})

    body = extra_prelude + "\n".join(line for key, line in lines.items() if key not in omit)
    (tmp_path / f"{module_name}.py").write_text(body, encoding="utf-8")
