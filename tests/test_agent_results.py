from agents.results import UNCLEAR_TASK_PREFIX, AgentResult, parse_agent_output


def test_plain_output_is_success():
    result = parse_agent_output("Gate 3 is nominal, no smoke detected.")

    assert result == AgentResult(status="success", text="Gate 3 is nominal, no smoke detected.")


def test_unclear_task_sentinel_is_parsed_out():
    result = parse_agent_output(f"{UNCLEAR_TASK_PREFIX} the task did not say which gate to check")

    assert result.status == "unclear_task"
    assert result.text == "the task did not say which gate to check"


def test_sentinel_is_recognized_even_with_surrounding_whitespace():
    result = parse_agent_output(f"  \n{UNCLEAR_TASK_PREFIX} missing the target location\n  ")

    assert result.status == "unclear_task"
    assert result.text == "missing the target location"


def test_sentinel_text_never_leaks_the_raw_prefix_into_success_path():
    # A message that merely mentions the phrase mid-sentence is not the
    # sentinel — only a message *starting* with it is.
    result = parse_agent_output(f"Everything is fine, not an {UNCLEAR_TASK_PREFIX} situation.")

    assert result.status == "success"
