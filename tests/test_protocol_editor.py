import importlib
import sys
import uuid

import pytest

from agents.reference import ReferenceAgent
from protocols.editor import ProtocolEditError, add_protocol, read_protocols, remove_protocol, replace_protocol
from protocols.loader import ProtocolSet
from protocols.model import CriticalityLevel, Protocol

_PROFILE_TEMPLATE = """
from protocols.model import Protocol, CriticalityLevel

AGENTS = []
PROTOCOLS = [
    Protocol(
        name="existing",
        description="applies to X",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="y",
        criticality=CriticalityLevel.LOW,
        approval_flag=False,
    ),
]
EVENT_TYPES = ["fire"]
AREAS = ["north"]
DB_PATH = {db_path!r}
API_PORT = 9999
RETRY_COUNT = 1
RISK_THRESHOLD = 0.5
LOOKBACK_WINDOW_DAYS = 10
BOT_TOKEN_ENV = "TEST_TOKEN"
MODEL_CREDENTIAL_ENVS = []
"""


@pytest.fixture
def profile_module(tmp_path, monkeypatch):
    module_name = f"editor_test_profile_{uuid.uuid4().hex}"
    content = _PROFILE_TEMPLATE.format(db_path=str(tmp_path / "test.db"))
    (tmp_path / f"{module_name}.py").write_text(content, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return module_name


def _reimport(module_name):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _protocol(**overrides):
    fields = dict(
        name="new_one",
        description="applies to Y",
        participating_agents=(),
        approved_tools=(),
        expected_success_output="z",
        criticality=CriticalityLevel.HIGH,
        approval_flag=True,
    )
    fields.update(overrides)
    return Protocol(**fields)


def test_read_protocols_is_a_pass_through():
    protocol = _protocol()
    protocol_set = ProtocolSet(protocols=(protocol,))

    assert read_protocols(protocol_set) == (protocol,)


def test_add_protocol_reports_the_running_system_is_unchanged(profile_module):
    module = importlib.import_module(profile_module)

    result = add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    assert "unchanged" in result.message
    assert "next start" in result.message


def test_add_protocol_does_not_touch_the_already_loaded_set(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    assert {p.name for p in module.PROTOCOLS} == {"existing"}


def test_add_protocol_is_visible_only_after_reimport(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    reloaded = _reimport(profile_module)
    assert {p.name for p in reloaded.PROTOCOLS} == {"existing", "new_one"}


def test_add_protocol_rejects_a_name_that_already_exists(profile_module):
    module = importlib.import_module(profile_module)
    dup = _protocol(name="existing")

    with pytest.raises(ProtocolEditError, match="already exists"):
        add_protocol(profile_module, tuple(module.PROTOCOLS), {}, dup)


def test_add_protocol_rejects_an_unresolvable_agent_reference(profile_module):
    module = importlib.import_module(profile_module)
    bad = _protocol(participating_agents=("ghost",))

    with pytest.raises(ProtocolEditError, match="ghost"):
        add_protocol(profile_module, tuple(module.PROTOCOLS), {}, bad)


def test_add_protocol_rejects_a_tool_the_named_agent_does_not_expose(profile_module):
    module = importlib.import_module(profile_module)
    agent = ReferenceAgent(model="m")
    bad = _protocol(participating_agents=("reference_agent",), approved_tools=("not_real",))

    with pytest.raises(ProtocolEditError, match="not_real"):
        add_protocol(profile_module, tuple(module.PROTOCOLS), {"reference_agent": agent}, bad)


def test_replace_protocol_updates_the_named_entry(profile_module):
    module = importlib.import_module(profile_module)
    updated = _protocol(name="existing", description="applies to Z now", criticality=CriticalityLevel.MEDIUM)

    replace_protocol(profile_module, tuple(module.PROTOCOLS), {}, updated)

    reloaded = _reimport(profile_module)
    assert len(reloaded.PROTOCOLS) == 1
    assert reloaded.PROTOCOLS[0].description == "applies to Z now"
    assert reloaded.PROTOCOLS[0].criticality == CriticalityLevel.MEDIUM


def test_replace_protocol_rejects_an_unknown_name(profile_module):
    module = importlib.import_module(profile_module)
    ghost = _protocol(name="ghost")

    with pytest.raises(ProtocolEditError, match="use add"):
        replace_protocol(profile_module, tuple(module.PROTOCOLS), {}, ghost)


def test_remove_protocol_deletes_the_named_entry(profile_module):
    module = importlib.import_module(profile_module)

    remove_protocol(profile_module, tuple(module.PROTOCOLS), "existing")

    reloaded = _reimport(profile_module)
    assert reloaded.PROTOCOLS == []


def test_remove_protocol_rejects_an_unknown_name(profile_module):
    module = importlib.import_module(profile_module)

    with pytest.raises(ProtocolEditError):
        remove_protocol(profile_module, tuple(module.PROTOCOLS), "does_not_exist")


def test_file_remains_importable_after_a_write(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    reloaded = _reimport(profile_module)
    assert {p.name for p in reloaded.PROTOCOLS} == {"existing", "new_one"}


def test_write_leaves_no_tmp_file_behind(profile_module, tmp_path):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    assert list(tmp_path.glob("*.tmp")) == []


def test_a_write_touches_only_the_protocols_assignment(profile_module):
    module = importlib.import_module(profile_module)

    add_protocol(profile_module, tuple(module.PROTOCOLS), {}, _protocol())

    reloaded = _reimport(profile_module)
    # everything else in the file survived untouched
    assert reloaded.EVENT_TYPES == ["fire"]
    assert reloaded.AREAS == ["north"]
    assert reloaded.API_PORT == 9999
