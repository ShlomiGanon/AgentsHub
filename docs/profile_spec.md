# Profile Specification

What a profile module must expose so `profiles.loader.load_profile` can load
it, per work_plan.md §1.4. Written so a person can author a new profile from
this document without reading the loader's source.

A profile is a plain Python module. The loader reads the following
module-level names. All are required unless noted.

| Name | Type | Meaning |
|---|---|---|
| `AGENTS` | `list` of `profiles.spec.AgentSpec` | The specialist agents this deployment runs — **declared**, not constructed. Each entry is `AgentSpec(cls=SomeAgent, tier="core"\|"sub")`; `profiles.loader.load_profile` is the only place any of them actually gets built, using whichever already-resolved `TierModel` matches the tier named. See "Model tiers", below. |
| `PROTOCOLS` | `list` of protocol objects | Each fully populated: name, description, participating agent names, approved tool names, expected success output, criticality, approval flag. |
| `EVENT_TYPES` | `list[str]` | Must not include `"human_activation"` — that type is added automatically and a profile declaring it is a validation error. |
| `AREAS` | `list[str]` | |
| `DB_PATH` | `str` | No default — two profiles running at once must not collide. |
| `API_PORT` | `int` | No default, same reason. |
| `RETRY_COUNT` | `int` | Starting value only; the settings store owns it after first run. |
| `RISK_THRESHOLD` | `float` | Starting value only. |
| `LOOKBACK_WINDOW_DAYS` | `int` | Starting value only. |
| `BOT_TOKEN_ENV` | `str` | Name of the environment variable holding the Telegram bot token — never the token itself. |
| `MODEL_CREDENTIAL_ENVS` | `list[str]` | Names of the environment variables holding model credentials — never the values. |

A profile module must stay safe to commit and to send to another team in
full: it holds no secret values, only the names of the environment
variables that hold them.

## Model tiers

Every agent in the system picks a **tier** — `"core"` or `"sub"` — rather
than naming a model directly:

- **`"core"`** — the three core agents every deployment constructs
  (Main, History, Insights — see `orchestrator.flows.assemble_core_agents`),
  unconditionally, with no exception and no hardcoded fallback.
- **`"sub"`** — the specialist agents a profile's own `AGENTS` list
  declares (e.g. `ReferenceAgent` in `profiles/demo.py`). A profile is
  free to declare a specialist on `"core"` instead if it genuinely wants
  the stronger tier for that agent — nothing enforces "specialists must be
  sub" beyond convention.

The actual provider, model, and API key for each tier come from six
environment variables, read directly from the process environment (not
profile-declared names, unlike `BOT_TOKEN_ENV`/`MODEL_CREDENTIAL_ENVS`
below — every deployment reads these same six literal names):

| Environment variable | Meaning |
|---|---|
| `CORE_MODEL_PROVIDER` | Provider for the "core" tier (e.g. `openrouter`). |
| `CORE_MODEL_NAME` | Model name for the "core" tier (e.g. `anthropic/claude-3.5-sonnet`). |
| `CORE_MODEL_API_KEY_ENV` | Name of *another* environment variable that holds the "core" tier's actual API key. |
| `SUB_MODEL_PROVIDER` | Provider for the "sub" tier. |
| `SUB_MODEL_NAME` | Model name for the "sub" tier. |
| `SUB_MODEL_API_KEY_ENV` | Name of *another* environment variable that holds the "sub" tier's actual API key. |

Provider and model are deliberately separate variables, not one combined
string: it lets an operator switch a tier's provider (e.g. OpenRouter today,
a direct Anthropic or Google key later) independently of which specific
model within that provider is in use.

**The API key is a second level of indirection — the same convention as
`BOT_TOKEN_ENV`.** `CORE_MODEL_API_KEY_ENV`/`SUB_MODEL_API_KEY_ENV` each
name another environment variable that actually holds that tier's key;
they are never the key itself. This is what lets "core" and "sub" use two
genuinely independent API keys even when both sit on the same provider —
litellm's implicit, provider-named env lookup (e.g. `OPENROUTER_API_KEY`)
can only hold one value process-wide and cannot express that, for any
provider. The resolved key is passed explicitly as `crewai.LLM(model=...,
api_key=...)` (a real, documented crewai/LiteLLM constructor —
`agents/runtime.py`), never inferred from provider name.

`config.base.build_tier_model(provider, model_name, api_key)` is a pure
function — three plain strings in, one `config.base.TierModel` out
(`.model`, the joined `provider/model` string; `.api_key`, the key as
given). It never reads `os.environ` and never raises; it just joins and
wraps whatever it's handed.

A profile's own `AGENTS` list never calls `build_tier_model`, or reads
`os.environ`, or resolves anything itself — it only **declares** which
class goes on which tier, via `profiles.spec.AgentSpec`:

```python
from profiles.spec import AgentSpec

AGENTS = [
    AgentSpec(cls=SomeAgent, tier="sub"),
]
```

`profiles.loader.load_profile(module_path, core_model, sub_model)` is the
only place any of these specs actually gets built. It's already been
handed both tiers' resolved `TierModel`s by its own caller (see "Where
tier config comes from", below); for each `AgentSpec` in `AGENTS` it picks
`core_model` or `sub_model` by the spec's `tier`, and constructs
`spec.cls(model=tier_model.model, api_key=tier_model.api_key)`. An
`AGENTS` entry that isn't an `AgentSpec`, or names a tier other than
`"core"`/`"sub"`, fails loudly at load time, naming the bad index/tier —
see Failure behavior, below.

`agents.base.Agent.__init__` (and every concrete agent's own `__init__`,
per `docs/agent_authoring.md`) accepts `api_key` as a second, optional
parameter, defaulting to `None` — an agent constructed the old way, with
only a bare model string, still works exactly as before: `agents/runtime.py`
falls back to litellm's implicit env lookup when `api_key` is `None`.

**No default is ever substituted, for any of the six `CORE_MODEL_*`/
`SUB_MODEL_*` variables or for the key value they lead to.** The one place
any of them is read is a real root's own boundary (`api.app.main`,
`bot.app.main`, `cli.user_admin.main`) — if any of that tier's
`*_MODEL_PROVIDER`/`*_MODEL_NAME`/`*_MODEL_API_KEY_ENV` is unset, or the
variable `*_MODEL_API_KEY_ENV` names is itself unset, the root's own
env-reading helper raises `config.base.ModelTierError`, naming the missing
variable, before `build_tier_model` (which never fails) is even called —
the same "fail loud, at startup" behavior as every other required profile
value (see Failure behavior, below).

**`MODEL_CREDENTIAL_ENVS` remains, unchanged, as a separate mechanism —**
for any agent that is *not* declared through `AgentSpec`/the tier system.
It still declares, by name only, the environment variables holding model
credentials, and the loader still fails loudly, naming the variable, if
anything listed there is unset — this has not changed. What has changed
is which agents actually need it: `profiles/demo.py`'s `ReferenceAgent`
now resolves its key through `SUB_MODEL_API_KEY_ENV`'s indirection, not
through `MODEL_CREDENTIAL_ENVS` — so that profile declares
`MODEL_CREDENTIAL_ENVS = []`. It is still a required profile attribute
(the loader fails if it's missing entirely, per Failure behavior below),
just legitimately empty when every agent a profile declares goes through
the tier system. A profile that constructs an agent the old way — a bare
model string, no tier, no `AgentSpec` — still needs that agent's
provider's credential variable listed in `MODEL_CREDENTIAL_ENVS`, exactly
as before.

### Where tier config comes from

Nothing below a real entry point ever reads `os.environ` for model-tier
config, and nothing below it takes a `Mapping` representing it either —
every function in the chain takes specific, already-resolved values as
named parameters:

| Function | Parameters |
|---|---|
| `config.base.build_tier_model(provider, model_name, api_key)` | three plain strings, required — pure, never raises |
| `config.base.load_base_config(core_model)` | one `TierModel`, required |
| `profiles.loader.load_profile(module_path, core_model, sub_model)` | two `TierModel`s, required |
| `api.app.build_context(module_path, core_model, sub_model)` | two `TierModel`s, required |
| `api.app.create_app(module_path, core_model, sub_model)` | two `TierModel`s, required |
| `bot.app.build_deps(module_path, core_model, sub_model)` | two `TierModel`s, required |

Exactly **four** places in the whole codebase read `os.environ` for
model-tier config — the three real entry points, each via its own private
`_tier_model_from_environ` helper that reads `{PREFIX}_MODEL_PROVIDER`/
`{PREFIX}_MODEL_NAME`/`{PREFIX}_MODEL_API_KEY_ENV` (plus the variable the
third one names) and calls `build_tier_model`, catching `ModelTierError`
and reporting it as that entry point's own kind of failure — and
`conftest.py`'s `test_core_model`/`test_sub_model` fixtures, below:

- `api.app.main` — `ModelTierError` becomes `SystemExit`.
- `bot.app.main` — `ModelTierError` becomes `SystemExit`.
- `cli.user_admin.main` — `ModelTierError` is caught, printed to stderr, and becomes exit code `1`.
- `conftest.py`'s `test_core_model`/`test_sub_model` fixtures (below).

No function in the production system or automated test suite reads
`os.environ` implicitly, or takes a `Mapping` standing in for it, on its
own initiative — the decision is always made explicitly, once, at one of
these four places, and threaded down as plain `TierModel` values from
there.

Outside that count: `tests/sanity_check_real_model_call.py` is a
standalone, hand-run diagnostic that deliberately does not match
`pytest.ini`'s `python_files = test_*.py` glob. Its `production`,
`key`, `openrouter`, and `crewai` modes cover the previous live-model
checks. It never runs in CI or the normal suite.

A profile module's own top-level code (e.g. `profiles/demo.py`'s
`AGENTS` list) never reads `os.environ`, and never resolves a `TierModel`
itself, at all — `AGENTS` only *declares* `profiles.spec.AgentSpec(cls,
tier)` entries; `profiles.loader.load_profile` is the only place any of
them gets built, using the `core_model`/`sub_model` its own caller
already resolved (see "Model tiers", above). This is what closes the gap
that made a profile module special in the first place: Python's import
mechanism has no way to pass an argument into a module being imported, so
under the old (`resolve_tier_model` call at profile top level) design a
profile always had to read `os.environ` directly, no matter what its
loader had been given. Deferring construction to the loader removes that
need entirely — a profile module can now be imported with zero
environment variables set.

**Tests use a different, explicit source — never a file, never a
default, never the real `os.environ`.** `conftest.py`'s `test_core_model`/
`test_sub_model` fixtures each build a `config.base.TierModel` from
`TEST_`-prefixed real process environment variables — set these yourself
(shell export, a CI secret, ...) before running the suite:

```
TEST_CORE_MODEL_PROVIDER, TEST_CORE_MODEL_NAME, TEST_CORE_MODEL_API_KEY_ENV
TEST_SUB_MODEL_PROVIDER,  TEST_SUB_MODEL_NAME,  TEST_SUB_MODEL_API_KEY_ENV
```

plus a `TEST_`-prefixed variable for whatever `TEST_CORE_MODEL_API_KEY_ENV`
and `TEST_SUB_MODEL_API_KEY_ENV` each name — e.g. if
`TEST_CORE_MODEL_API_KEY_ENV=CORE_MODEL_KEY`, also set `TEST_CORE_MODEL_KEY`
to the real (fake, test-only) key value. A test then passes
`test_core_model`/`test_sub_model` as `core_model=`/`sub_model=` to
whichever of `load_base_config`/`load_profile`/`build_context`/
`build_deps` it's exercising. Each fixture fails the requesting test
immediately, naming what's missing, if any required `TEST_` variable
isn't set. There is no `.env.test` file, or any other file — the whole
mechanism is real environment variables in, a `TierModel` out.

The rare test that must exercise one of the three real `main()` entry
points end to end (not just the functions below it) needs the real,
literal `CORE_MODEL_*`/`SUB_MODEL_*` variables set too, since that's what
`main()` itself reads — `conftest.py`'s `real_tier_env` fixture does this
by mirroring whatever `test_core_model`/`test_sub_model` are already
configured with into the real variable names, via `monkeypatch.setenv`,
for the duration of that one test.

## The agent/protocol structural contract

Sections 3 (Agent Framework) and 4 (Protocol Engine) define the real
`Agent` and `Protocol` classes. `profiles.loader.validate_profile` checks
`AGENTS` and `PROTOCOLS` entries **structurally** (attribute presence), not
by type:

- Each item in `AGENTS` must expose `.name` (`str`) and a callable that
  returns its exposed tools (by convention, `.exposed_tools()` returning
  something iterable of tool names).
- Each item in `PROTOCOLS` must expose `.name`, `.description`,
  `.participating_agents` (agent names), `.approved_tools` (tool names),
  `.criticality`, and `.approval_flag` (must be `True` or `False`
  explicitly — an absent/`None` flag is a validation failure).

Any object satisfying this shape works, including the fake stand-ins used
in tests before the real classes exist — **with one exception**:
`.criticality` must be a real `protocols.model.CriticalityLevel` member
(`LOW`, `MEDIUM`, or `HIGH`), not merely present. A plain string like
`"low"` satisfies every structural check above but is not accepted —
`api/routes.py` and `protocols/service.py` both call `.name` on it
(crashing on anything else), and `orchestrator/decisions.py`'s high-risk
tie-break compares it by severity, which only a real, ordered
`CriticalityLevel` guarantees; a string would compare alphabetically
instead and could silently select the wrong protocol. This is the same
kind of exception `.approval_flag` already is — most fields here stay
duck-typed, but not the ones a wrong value could get dangerously wrong
without raising anything.

## Failure behavior

- Missing module / bad launch argument: the loader fails immediately,
  naming the argument. There is no default profile.
- Missing required name: the loader fails, naming the missing attribute.
- Missing environment variable named in `BOT_TOKEN_ENV` or
  `MODEL_CREDENTIAL_ENVS`: the loader fails at load time, naming the
  variable — never at first use.
- The process environment is missing model-tier config (any of
  `CORE_MODEL_PROVIDER`/`CORE_MODEL_NAME`/`CORE_MODEL_API_KEY_ENV`, the
  `SUB_` equivalents, or the variable a `*_MODEL_API_KEY_ENV` names): the
  real entry point (`api.app.main`/`bot.app.main`/`cli.user_admin.main`)
  fails before `load_profile` is even called, naming the missing
  variable. See "Model tiers", above.
- An `AGENTS` entry isn't a `profiles.spec.AgentSpec`, or names a tier
  other than `"core"`/`"sub"`: the loader fails at load time, naming the
  bad entry's index (and, for a bad tier name, the tier it named).
- Structural/content problems (bad protocol references, duplicate
  `human_activation`, missing event types/areas, etc.): reported by
  `profiles.loader.validate_profile`, which collects every failure before stopping rather
  than failing on the first.
