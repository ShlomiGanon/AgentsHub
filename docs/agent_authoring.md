# Adding an Agent

Five steps. `agents/reference.py` is the working example — read it
alongside this rather than expecting this document to reproduce it.

1. **Subclass `agents.base.Agent`** in a new file under `agents/`.
2. **Set three class-level attributes**: `name` (a short, unique
   identifier — the registry key), `role` (what this agent is for and
   good at, written for the Main Agent to read when deciding who to task
   and what to ask them — not for a human), `system_prompt` (its
   instructions).
3. **Implement each tool as a method**, decorated with `@agents.tooling.tool(name, description, side_effecting=..., idempotent=...)`.
   `description` is written for a model to act on ("queries sensor
   status", not "sensor tool"). `idempotent` is required when
   `side_effecting=True` and forbidden when it's `False` — there's no
   meaningful value for a read-only tool.
4. **Accept `model` in `__init__`** and pass it to `super().__init__(model)`
   — this is what lets the same agent class run on different models in
   different profiles.
5. **Declare the agent in a profile's `AGENTS` list**, via
   `profiles.spec.AgentSpec(cls=YourAgent, tier="core"|"sub")` — never
   construct it there yourself. `profiles.loader.load_profile` is the only
   place any `AgentSpec` actually gets built, using whichever already-
   resolved `TierModel` matches the tier named (see
   `docs/profile_spec.md`'s "Model tiers" section); this is what lets the
   same agent class run on different models in different profiles without
   the profile module itself ever touching `os.environ`.

## What happens if a step is skipped

- Omit `side_effecting` (or get the `idempotent` pairing wrong): the
  `@tool` decorator raises immediately, at class-definition time — the
  agent fails to import at all, not later during a run.
- Omit `name`/`role`/`system_prompt`: the agent fails to *construct*,
  naming which attribute is missing (`agents/base.py`'s `Agent.__init__`).
- Write the agent but never declare it in a profile: it's simply never
  loaded — `agents.registry` only knows about what's explicitly passed to
  it, nothing self-registers.
- Declare it as anything other than an `AgentSpec` (e.g. an already-
  constructed instance, the pre-`AgentSpec` shape): `load_profile` fails
  loudly at load time, naming the bad `AGENTS` index.
