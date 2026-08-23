import pytest

from agents.tooling import ToolInfo, exposed_tools_for, tool, tool_info_of


def test_tool_requires_side_effecting_explicitly():
    # side_effecting is a required keyword-only parameter with no default —
    # omitting it entirely is a TypeError from Python itself, which is
    # exactly "no default" enforced as strongly as possible.
    with pytest.raises(TypeError, match="side_effecting"):
        tool("t", "does a thing")

def test_side_effecting_true_requires_idempotent():
    with pytest.raises(ValueError, match="idempotent"):
        tool("t", "does a thing", side_effecting=True)


def test_idempotent_forbidden_for_read_only_tool():
    with pytest.raises(ValueError, match="no meaning"):
        tool("t", "does a thing", side_effecting=False, idempotent=True)


def test_read_only_tool_decorates_successfully():
    @tool("check_status", "Returns a canned status.", side_effecting=False)
    def check_status(self):
        return "ok"

    info = tool_info_of(check_status)
    assert info == ToolInfo(name="check_status", description="Returns a canned status.", side_effecting=False, idempotent=None)


def test_side_effecting_tool_decorates_successfully():
    @tool("record_action", "Records that it acted.", side_effecting=True, idempotent=False)
    def record_action(self):
        return "recorded"

    info = tool_info_of(record_action)
    assert info.side_effecting is True
    assert info.idempotent is False


def test_exposed_tools_for_derives_from_actual_methods():
    class Toy:
        @tool("a", "does a", side_effecting=False)
        def a(self):
            return None

        @tool("b", "does b", side_effecting=True, idempotent=True)
        def b(self):
            return None

        def not_a_tool(self):
            return None

    names = {t.name for t in exposed_tools_for(Toy())}
    assert names == {"a", "b"}


def test_a_tool_added_to_the_class_shows_up_without_a_hand_maintained_list():
    class Toy:
        @tool("a", "does a", side_effecting=False)
        def a(self):
            return None

    assert len(exposed_tools_for(Toy())) == 1

    class ToyWithMore(Toy):
        @tool("c", "does c", side_effecting=False)
        def c(self):
            return None

    assert len(exposed_tools_for(ToyWithMore())) == 2
