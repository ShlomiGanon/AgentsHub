"""Behind-the-Scenes Standalone Live Agent Execution & Communication Dashboard.

Renders an independent, high-performance, cybernetic dark-themed dashboard
that operators can open in a separate window or tab to watch multi-agent
reasoning, inter-agent communication, parallel execution branches, tool side-effects,
and database verifications in real-time.
"""

from __future__ import annotations

import html
import json
import re

from messages import get_current_catalog

from api.admin_bts_assets import HTML_PAGE_TEMPLATE


def render_behind_the_scenes_html(*, trace_id: str = "", profile_name: str = "") -> str:
    """Return the complete standalone HTML page for Behind the Scenes."""
    catalog = get_current_catalog()
    strings = {
        key[len("admin.simulator."):]: template
        for key, template in catalog.messages.items()
        if key.startswith("admin.simulator.bts.")
    }
    # Also expose job_stopped from the simulator catalog (used by the live beacon).
    if "admin.simulator.job_stopped" in catalog.messages:
        strings["job_stopped"] = catalog.messages["admin.simulator.job_stopped"]

    def _kwargs(raw: str | None) -> dict[str, object]:
        """Parse catalog-format kwargs from a template ``t()`` call."""

        if not raw:
            return {}
        values: dict[str, object] = {}
        for name, quoted, number in re.findall(r"(\w+)=(?:'([^']*)'|(\d+))", raw):
            values[name] = int(number) if number else quoted
        return values

    def _replace_t(match: re.Match[str]) -> str:
        """Replace one ``{{ t('key') }}`` match with escaped catalog text."""

        return html.escape(catalog.text(match.group(1), **_kwargs(match.group(2))))

    page = re.sub(r"\{\{\s*t\('([^']+)'(?:,\s*(.*?))?\s*\)\s*\}\}", _replace_t, HTML_PAGE_TEMPLATE)
    page = page.replace("__SAFE_TRACE_ID__", html.escape(trace_id or ""))
    page = page.replace("__BTS_LANG__", html.escape(catalog.language))
    page = page.replace("__BTS_DIR__", "rtl" if catalog.language == "he" else "ltr")
    page = page.replace("__BTS_STRINGS__", json.dumps(strings, ensure_ascii=False))
    return page
