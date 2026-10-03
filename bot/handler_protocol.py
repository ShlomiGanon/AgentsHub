"""Parse /profile protocol-write command fields into an API payload."""

from bot import interactions


def _parse_protocol_write_command(rest: str, catalog=None) -> tuple[str, dict] | str:
    """Parse pipe-separated protocol fields into (name, payload), or return an error message."""

    fields = [part.strip() for part in rest.split("|")]
    messages = catalog or interactions._catalog()
    if len(fields) != 7:
        return messages.text("protocol.expected_fields")

    name, description, agents_csv, tools_csv, expected_output, criticality, flag_text = fields

    if flag_text.lower() not in ("true", "false"):
        return messages.text("protocol.flag_boolean")

    payload = {
        "name": name,
        "description": description,
        "participating_agents": [a.strip() for a in agents_csv.split(",") if a.strip()],
        "approved_tools": [t.strip() for t in tools_csv.split(",") if t.strip()],
        "expected_success_output": expected_output,
        "criticality": criticality,
        "approval_flag": flag_text.lower() == "true",
    }
    return name, payload
