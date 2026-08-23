# Profile Specification

What a profile module must expose so `profiles.loader.load_profile` can load
it, per work_plan.md §1.4. Written so a person can author a new profile from
this document without reading the loader's source.

A profile is a plain Python module. The loader reads the following
module-level names. All are required unless noted.

| Name | Type | Meaning |
|---|---|---|
| `AGENTS` | `list` of constructed agent instances | The specialist agents this deployment runs. Construct them here — don't just name a class — so two profiles can run the same agent class on different models. |
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

## The agent/protocol structural contract

Sections 3 (Agent Framework) and 4 (Protocol Engine) define the real
`Agent` and `Protocol` classes. Until they land, `profiles.validate` checks
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
in tests before the real classes exist.

## Failure behavior

- Missing module / bad launch argument: the loader fails immediately,
  naming the argument. There is no default profile.
- Missing required name: the loader fails, naming the missing attribute.
- Missing environment variable named in `BOT_TOKEN_ENV` or
  `MODEL_CREDENTIAL_ENVS`: the loader fails at load time, naming the
  variable — never at first use.
- Structural/content problems (bad protocol references, duplicate
  `human_activation`, missing event types/areas, etc.): reported by
  `profiles.validate`, which collects every failure before stopping rather
  than failing on the first.
