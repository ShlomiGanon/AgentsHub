"""Event-type and area registries (work_plan.md §2.1, §2.2).

Entry points: `registries.event_types`, `registries.areas`. Both wrap a
closed set read from the active profile at startup — extraction, the
clarification prompt, and precedent search all read from these rather than
from the profile directly.
"""
