# AgentsHub — Investigation Inputs for a Future `/help` Command Design

**Purpose:** Read-only investigation of how bot command registration and
viewer/commander permission actually work today, to inform a future
`/help` design decision. No code changed in this pass.

---

### 1. Where are bot commands registered today?

- **What I found:** Two separate, only loosely-related registration
  points in `bot/app.py`:
  - The **Telegram command-menu data** (the `setMyCommands` list,
    `TELEGRAM_UX_FINDINGS.MD` A.2's finding) is `_bot_commands(catalog)`
    (`bot/app.py:107-116`) — a plain function returning a
    `list[tuple[str, str]]` of `(command_name, description)` pairs:
    ```python
    def _bot_commands(catalog) -> list[tuple[str, str]]:
        return [
            ("start", catalog.text("command.menu_start")),
            ("profile", catalog.text("command.menu_profile")),
            ("settings", catalog.text("command.menu_settings")),
        ]
    ```
    This is called once at startup (`_post_init`, `bot/app.py:523-527`)
    and turned into `telegram.BotCommand` objects passed to
    `bot.set_my_commands(...)`. It carries **only a name and a
    description string** — no role field, no handler reference, no
    sub-action information.
  - The **actual PTB handler wiring** (which function runs for which
    `/command`) is a separate, hardcoded block in `register_handlers`
    (`bot/app.py:509-517`):
    ```python
    REGISTERED_COMMANDS = ("profile", "settings")
    ...
    assert REGISTERED_COMMANDS == ("profile", "settings")
    application.add_handler(CommandHandler("start", _guarded(_on_start_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[0], _guarded(_on_profile_command)))
    application.add_handler(CommandHandler(REGISTERED_COMMANDS[1], _guarded(_on_settings_command)))
    ```
    `REGISTERED_COMMANDS` is a plain tuple of two strings, asserted
    against itself immediately below (a guard against silent drift, not
    a data source anything else reads). `/start` isn't in this tuple at
    all — it's wired directly.
  - These two registration points are **not derived from each other** —
    `_bot_commands` is a separate, manually-kept-in-sync list; nothing
    enforces that every `CommandHandler` has a corresponding
    `_bot_commands` entry or vice versa (there is no test doing that
    cross-check today, unlike e.g. `test_file_catalog.py`'s pattern for
    a different kind of list).
- **Implication for a static `/help` design:** There is no single
  existing "list of commands" data structure a `/help` generator could
  just read — it would need to build its own, informed by (but not
  literally reusing) both `_bot_commands` and `REGISTERED_COMMANDS`.

---

### 2. How is viewer-vs-commander permission currently expressed for each command?

- **What I found: enforced at call time, deep inside each handler body —
  not declared upfront alongside command registration.** Neither
  `_bot_commands` nor `REGISTERED_COMMANDS` carries anything
  permission-related. Concretely, per command:
  - `/profile` (`_on_profile_command`, `bot/app.py:419-473`) is not one
    permission decision — it's a small sub-action dispatcher, and each
    branch checks independently:
    - `view` / `diff` → `check_permission(caller, RequestedOperation.VIEW_PROFILE_OVERVIEW, ...)` inline in `bot/app.py` (lines 428-430, 441-443).
    - `add` / `edit` / `remove` → no check visible in `bot/app.py` at
      all; delegated into `interactions.write_protocol`
      (`bot/app.py:459-466` → `bot/interactions.py:330-335`), which
      internally does `check_permission(caller, _PROTOCOL_WRITE_OPERATIONS[action], messages)`.
  - `/settings` (`_on_settings_command`, `bot/app.py:476-506`) same
    shape: `view` checks `RequestedOperation.VIEW_SETTINGS` inline
    (`bot/app.py:485-487`); `set` delegates to
    `interactions.change_setting`, which checks
    `RequestedOperation.CHANGE_SETTINGS` internally
    (`bot/interactions.py:405-408`).
  - `/start` has no permission check at all (it only branches on
    registered-vs-unregistered, `bot/app.py:119-130`) — every registered
    caller of either role gets the same welcome text.
  - **One real, narrow exception worth naming precisely**:
    `bot/interactions.py:323-327` does have a genuine static data table
    — but it's scoped to `/profile`'s three write sub-actions only, not
    to commands in general:
    ```python
    _PROTOCOL_WRITE_OPERATIONS: dict[str, RequestedOperation] = {
        "add": RequestedOperation.CREATE_PROTOCOL,
        "edit": RequestedOperation.UPDATE_PROTOCOL,
        "remove": RequestedOperation.DELETE_PROTOCOL,
    }
    ```
  - What genuinely *is* a complete, static, upfront table — but at the
    **operation** level, not the **command** level — is
    `auth/permissions.py`'s `RequestedOperation` (the full vocabulary of
    21 operations) and `ViewerAllowedAction` (the exhaustive viewer
    allowlist: an `Enum` whose 9 members are exactly the operations a
    viewer may perform; a commander is authorized for every
    `RequestedOperation` unconditionally, `is_permitted`,
    `auth/permissions.py:79-92`). This is real, importable, read-at-import-time
    data: `{member.value for member in ViewerAllowedAction}` gives the
    complete viewer-permitted operation set with no code execution
    needed beyond that. It is genuinely the mechanism the user
    described as "already authorizes viewer commands" — it's just
    indexed by *operation*, not by *Telegram command name*.
- **Implication for a static `/help` design:** Shape 2 (call-time
  enforcement) is what actually exists, **except** that the missing
  piece — mapping a concrete "thing a user can type" to the
  `RequestedOperation` it triggers — is smaller than it looks, because
  every command/sub-action already funnels through exactly one
  `RequestedOperation` (see the full list under question 3). A `/help`
  generator doesn't need to refactor every handler; it needs one new,
  small, hand-written table (command/sub-action → `RequestedOperation`,
  the same shape `_PROTOCOL_WRITE_OPERATIONS` already uses for the one
  case that needs it) and can then cross-reference `ViewerAllowedAction`
  — which already exists exactly as-is — for the actual yes/no per role.

---

### 3. Does today's command-menu registration already filter by role?

- **What I found: no.** `_bot_commands(catalog)` takes only a `catalog`
  argument — no caller, no role, no permission level — and returns the
  same fixed 3-entry list unconditionally. `_post_init` calls
  `started_application.bot.set_my_commands([...])` once, at bot startup,
  with no `scope` argument (python-telegram-bot's `set_my_commands`
  supports a `scope`/per-chat argument for exactly this kind of
  filtering, but it isn't used here) — every user, viewer or commander,
  sees the identical Telegram "/" command picker. Role-based
  restriction only happens later, at execution time, per question 2.
  - Separately, but directly relevant: `docs/allowed_calls.md`'s
    "Operation matrix" **already contains the complete, hand-maintained
    mapping** from every entry point (API routes and bot
    commands/sub-actions alike) to its `RequestedOperation` and a
    precomputed "Viewer allowed: yes/no" column — including exactly the
    bot rows a `/help` command would need:
    ```
    | bot `/profile view`, `/profile diff`   | view_profile_overview                              | yes |
    | bot `/profile add|edit|remove`         | create_protocol / update_protocol / delete_protocol | no  |
    | bot `/settings view`                   | view_settings                                       | no  |
    | bot `/settings set ...`                | change_settings                                     | no  |
    ```
    This document states outright that it "reflects the completed
    implementation... not a proposal," and cross-referencing it against
    `check_permission`'s actual call sites (question 2) confirms it's
    currently accurate. **Caveat:** it is a hand-maintained Markdown
    file, not executable data, and nothing in the test suite enforces
    that it stays in sync with the code (no `test_file_catalog.py`-style
    check exists for it) — so it's an excellent, already-correct answer
    key for building a `/help` table by hand, not itself a thing
    `/help` could parse and trust unattended at runtime.
- **Implication for a static `/help` design:** No existing per-role
  filtering to reuse at the command-menu layer. But the *content*
  needed to build one correctly already exists and is already verified
  accurate, in `docs/allowed_calls.md` — a `/help` implementation would
  be transcribing already-known-correct facts into code, not
  rediscovering them.

---

### 4. Is there already a description/help-text field on each command's registration?

- **What I found: yes, but only one short description per *top-level*
  command, and no permission/role annotation on it.**
  `_bot_commands` already pairs each of the 3 top-level commands with a
  one-line catalog-driven description (`messages/en.py:163-165`):
  ```python
  "command.menu_start": "Get started",
  "command.menu_profile": "View or edit the active profile",
  "command.menu_settings": "View or change live settings",
  ```
  These exist solely to populate Telegram's native "/" picker tooltip
  text (per question 1) — plain strings, no structured field for role,
  no sub-action breakdown (`/profile`'s description doesn't distinguish
  `view` from `add`/`edit`/`remove`).
  Separately, `command.profile_usage` / `command.settings_usage`
  (`messages/en.py:172-173`) are plain **syntax** reminders shown only
  when a sub-command is malformed/incomplete (e.g. `"Usage: /profile
  view | diff | add ... | edit ... | remove <name>"`) — not
  role-annotated either, and not really "help text" in the sense of
  explaining *what* a command does.
- **Implication for a static `/help` design:** Adding a `/help` command
  with per-role descriptions means adding new fields/text, not just
  reading existing ones — the existing `command.menu_*` descriptions can
  likely be reused for the top-level summary line, but a
  sub-action-level breakdown (if wanted) and any role annotation would
  be new catalog keys and new data, not a refactor of what's there.

---

## Summary for the upcoming design decision

The two things still open for the user to decide, unchanged by this
investigation: (1) whether `/help` output includes description text
beyond the bare command list, and (2) the exact data shape to build the
startup-time cache from. This pass's concrete finding relevant to both:
the correct command → `RequestedOperation` → viewer-allowed facts already
exist and are already verified accurate in `docs/allowed_calls.md`'s
Operation matrix — a `/help` implementation is a transcription task
against that matrix (plus, if wanted, a description string per line),
not a rediscovery or a handler-refactoring task.
