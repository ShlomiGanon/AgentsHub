import json
import logging

from tools.logging_config import configure_logging
from tools.tracing import get_trace_id, new_trace_id, trace_context


def test_log_record_is_a_single_json_object_with_named_fields(capsys):
    configure_logging("test_profile")
    logging.getLogger("test").info("something happened", extra={"risk_level": "high"})

    output = capsys.readouterr().out.strip()
    record = json.loads(output)

    assert record["message"] == "something happened"
    assert record["profile_name"] == "test_profile"
    assert record["risk_level"] == "high"
    assert "timestamp" in record
    assert "trace_id" in record


def test_trace_id_is_attached_to_every_record_within_the_context(capsys):
    configure_logging("test_profile")

    with trace_context() as trace_id:
        logging.getLogger("test").info("step one")
        logging.getLogger("test").info("step two")

    lines = capsys.readouterr().out.strip().splitlines()
    records = [json.loads(line) for line in lines]

    assert records[0]["trace_id"] == trace_id
    assert records[1]["trace_id"] == trace_id


def test_trace_context_restores_previous_value_on_exit():
    outer_id = new_trace_id()

    with trace_context(outer_id):
        with trace_context() as inner_id:
            assert get_trace_id() == inner_id
            assert inner_id != outer_id

        assert get_trace_id() == outer_id
