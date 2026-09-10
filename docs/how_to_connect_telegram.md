# How to Connect Telegram

Operator guide for connecting this system's Telegram bot to a real Telegram account.

## 1. Overview

This guide covers connecting the Telegram bot to a real Telegram account for a
single localhost deployment — the scope `operator_guide.md` itself states it
assumes. It does **not** cover production hardening (secrets management, TLS,
backups, monitoring); for that, see `docs/PRODUCTION_READY.md`.

## 2. Step 1 — Create the bot with @BotFather

This is the standard Telegram flow (not specific to this project):

1. Open a chat with **@BotFather** on Telegram.
2. Send:
   ```
   /newbot
   ```
3. When prompted, choose a display name for the bot, then a username — it
   must end in `bot` (e.g. `my_ops_bot`).
4. BotFather replies with an API token, a string of the form:
   ```
   123456789:AAExampleTokenTextGoesHereXXXXXXXXXXX
   ```

Keep this token secret — anyone who has it can control the bot as if they
were you.

## 3. Step 2 — Store the token as an environment variable

Per `operator_guide.md`, a profile file holds no secret values — only the
*name* of the environment variable that holds the token (`BOT_TOKEN_ENV`).
The profile file itself is meant to be safe to commit and share in full.

1. Open your profile module (e.g. `profiles/demo.py`) and find its
   `BOT_TOKEN_ENV` value — that's the *name* of the variable you need to set,
   not the token itself. For example, if the profile has:
   ```python
   BOT_TOKEN_ENV = "MY_DEPLOYMENT_BOT_TOKEN"
   ```
2. Export the real token from Step 1 under that exact name, in the shell you
   will launch the API and the bot from:
   ```
   export MY_DEPLOYMENT_BOT_TOKEN="123456789:AAExampleTokenTextGoesHereXXXXXXXXXXX"
   ```
   (Replace `MY_DEPLOYMENT_BOT_TOKEN` with whatever your profile's
   `BOT_TOKEN_ENV` actually says, and the value with your own token.)

## 4. Step 3 — Register the required users

The only way to add, change, or remove a user is `cli.user_admin`, run from
the shell on the machine hosting the deployment — never through the API or
the bot.

### 4.1 Add the first human commander

A fresh deployment has no users at all; the first commander is added against
an empty database — there is no separate bootstrap step:

```
python -m cli.user_admin --profile <profile_module> add --telegram-id <id> --level commander
```

`<id>` is your own Telegram identity (the numeric ID or handle your account
resolves to).

### 4.2 Add the bot's own service identity

Beyond human users, **one more identity must be registered before the bot
can do anything**: its own service identity, `bot-service`, at **commander**
level:

```
python -m cli.user_admin --profile <profile_module> add --telegram-id bot-service --level commander
```

**Do this at the same time as Step 4.1, not as an afterthought.** Skipping
this step lets the deployment *appear* to start correctly — the bot connects
to Telegram, the API serves — and then every single bot-to-API call fails
authentication the moment anyone uses it. Nothing about startup itself will
fail, which is exactly why it's easy to miss.

### 4.3 Set the bot-service key

Registering the `bot-service` identity above is not, by itself, enough:
`bot-service` is a fixed, public string — visible in this very file — so
without one more thing, anyone who can reach the API and sends that string
as their identity would authenticate as it, at commander level. That one
more thing is `BOT_SERVICE_KEY`: a secret only the bot process and the API
process know, checked in addition to (not instead of) the identity above.

Generate one and set it in the environment `python -m api.app` and
`python -m bot.app` both run from — the same shell(s) `BOT_TOKEN` is
already set in per Step 2 above:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
export BOT_SERVICE_KEY="<the value that just printed>"
```

Both processes read it directly from the environment, not from the profile
— unlike `BOT_TOKEN_ENV`, there is no profile-level indirection for this
one, so the variable name is always exactly `BOT_SERVICE_KEY`. If it's
unset, the bot logs a startup warning and every call it makes as its own
service identity (notification delivery, the commander roster,
profile-change checks, resolving a Telegram user) is rejected — everything
else (a human's own messages, questions, reports) is unaffected either way,
since those authenticate as that human's own identity, not `bot-service`.

## 5. Step 4 — Run the API and the bot

The bot talks to the API over real HTTP, so both processes must be running.

Start the bot with:

```
python -m bot.app <profile_module>
```

**Open question — please confirm before relying on this:** the exact command
to launch the API process was not found in the two documents this guide is
based on (`operator_guide.md`, `progress.md`). Check the project's README, or
run:

```
python -m api.app --help
```

to confirm the correct invocation before proceeding — do not guess at the
syntax.

## 6. Step 5 — Verify the connection

1. Open a chat with your bot on Telegram and send it a message.
2. Confirm you get a response back.
3. If you (or whoever is testing) is **not** registered as a user, or is
   registered below commander level for an action that requires it, expect
   an authentication failure at this point rather than a normal reply — this
   is expected behavior for an unregistered or non-commander identity, not a
   sign anything is broken.

## 6a. Optional — add the bot to Telegram groups

The bot can also work inside group chats, with each group routed to the
sub-agent its messages belong to. Nothing here is required for private
chats.

1. **Add the bot to the group.** In Telegram, add the bot as a member. For the
   bot to see ordinary (non-command) messages in a group it must either have
   *Group Privacy* disabled in @BotFather (`/setprivacy` -> Disable) or be a
   group administrator. Without one of those, only `/commands`, button presses
   and replies to the bot reach it.
2. **Get the chat ID.** As soon as it is added, the bot posts one message with
   the group's chat ID (a negative number such as `-1001234567890`). Until the
   group is registered the bot ignores everything said there and only logs
   `bot_group_unregistered`.
3. **Bind the group to an agent.** Either:
   - Admin panel: `http://<host>:<API_PORT>/admin/` -> *Telegram groups* ->
     *Add a group* (chat ID, label, and the agent to route to), or
   - Command line, against the same deployment database:
     ```
     python -m cli.group_admin --profile <profile.module> add --chat-id -1001234567890 --agent team_status_agent --label "readiness team"
     python -m cli.group_admin --profile <profile.module> list
     ```
   `--agent main_agent` gives the group the full system (same as a private
   chat). Any specialist name from the profile's `AGENTS` restricts the group
   to that agent's protocols and tools (plus history questions). A change made
   from the command line is picked up by a running API within about a minute;
   admin-panel changes apply immediately.
4. **Readiness-team attendance.** Bind the readiness team's group to
   `team_status_agent`. Once the roster is approved, every day after the
   profile's attendance hour the bot posts the attendance prompt to that group
   with two buttons. Each member's press is recorded as *their own* response
   (the Telegram identity that pressed), so members must be registered users
   (Step 3) and on the approved roster; pressing *Unavailable* asks that member
   for a reason and number of days in the group, and only that member's next
   message completes it.

## 7. Optional — verbose diagnostic logging

For the first real run against Telegram, it can help to turn on verbose
diagnostic logging. Set this in the process environment **before** starting
the API or the bot:

```
export DEBUG_VERBOSE_LOGGING=true
```

This is read once at startup, not reconfigurable while running (`false`,
`0`, unset, or any other value is off). It adds detail such as `model_io`
records — the full prompt sent to the model and the full raw response,
before any parsing — plus successful tool-call detail and internal
precedent-search detail, all normally off by default.

**Warning:** this output is sensitive. `model_io` records can contain the
full original event or message text verbatim, prompt structure, and model
output. Do not leave this on in normal operation, and do not route it to a
shared log collector without the same care you'd give the raw event text
itself.

## 8. Known limitation

`crewai` is not installed in this environment — a deliberate choice pending
an API key. As a result, the bot and API will start and respond normally,
but any flow that requires an actual AI call (intent classification, risk
assessment, protocol selection, and similar steps) will fail at that point
rather than silently succeeding. Keep this in mind when interpreting
unexpected failures during Step 5 verification — a failure at an AI-call
step is expected in this environment, not necessarily a misconfiguration.
