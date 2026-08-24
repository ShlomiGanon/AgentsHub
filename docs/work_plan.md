# Work Plan — Field-Report Multi-Agent System

## System Overview

The system ingests **operational events** — sensor reports arriving in real time, plus text sent by people through Telegram — decides how to handle each one, acts on it through specialist AI agents, and maintains a durable historical record. Both sources deliver free-form English text. The central design commitment is that nothing important lives in a model's memory: every event and every action taken is written to a database, and questions about the past are answered from that record rather than from recall.

### Profile

What the system *is* on any given run. A profile is a Python module loaded at startup, named as a launch argument, carrying the specialist agents, the protocols, the event types, the areas, the database path, the API port, the starting values of the three live settings, and the names of the environment variables holding the secrets. It constructs its agents rather than merely naming them, so two otherwise identical profiles can run the same agents on different models. One codebase therefore serves a firefighting deployment and a military one as two separate runs with two separate databases, sharing nothing. Nothing in a profile changes while the system is running: writes go to the profile file and take effect on the next start. Only three values are live — the retry count, the risk threshold, and the precedent lookback window — and each is persisted to a JSON store the moment it changes.

### Persistence Layer

The single gateway to storage for every other subsystem. It exists to keep the choice of database engine reversible: everything above it works against one narrow interface, so replacing SQLite means rewriting one file and nothing else. It owns the event schema, the summary tables, the held-event store, and the user-to-permission mapping, and it opens whichever database the active profile names.

### Agent Framework

The machinery that makes every specialist agent look identical from the outside. An abstract agent class defines the shape; each concrete agent inherits it and implements its own tools, taking its model as a constructor argument. All agents are invoked through one function, and each exposes its role and tool list on request — which is what lets the Main Agent write a task for an agent it knows nothing else about, and lets profile validation confirm a referenced tool exists.

### Protocol Engine

A protocol is a named playbook: which agents participate, which tools they may use, what output counts as success, how critical it is, and whether it may run before a human says yes. Protocols are not keyed to event types — the Main Agent chooses between them by reading their descriptions. Criticality only breaks ties between candidates; the approval flag is a separate field and the only thing that stops a run. Protocols come from the profile and are fixed for the run. The engine executes the steps the Main Agent produced; it does not compose them itself.

### History System

The system's long-term memory. It has two halves: a database recording every event verbatim and in structured form, and a History Agent that compresses those records into rolling daily, monthly, and yearly summaries. It answers two kinds of question: what happened during a period, and whether an event like this one has been seen and handled before. Its responsibility is fidelity — a summary that quietly reconciles two contradictory reports has destroyed the information it was meant to preserve.

### Main Agent

The orchestrator, and the only component that makes judgment calls. For every event it assesses risk, chooses a protocol by description, looks for precedent, and then either closes the event on that precedent or writes each participating agent a specific task and judges the result. It is also the router for everything a person sends: it decides whether a message is a question, a report of something that happened, or a request for action.

Three situations send an event to a human. Text that cannot be classified waits for a commander to classify it. A selection where no protocol fits, or several fit equally at low risk, waits for a commander to choose — at high risk it proceeds on the most critical candidate instead. And a protocol carrying the approval flag waits before running at all. A commander's own request skips the flag, since they have already decided; it does not skip the other two, because neither is a question of permission.

### Insights Agent

The synthesis step at the end of every protocol run. It receives the task each sub-agent was given and the result each returned, compares the picture against the historical record, and forms a single conclusion about the event. Holding both halves is what lets it distinguish an agent that failed from an agent that was asked the wrong question. Its conclusion is not delivered directly — the Main Agent judges whether the insight holds and whether the protocol completed successfully.

The Main Agent, the History Agent, and the Insights Agent are structural rather than domain-specific. They load on every run regardless of profile, and take their models from the base configuration.

### API Layer

The system's external contract, operating asynchronously: callers submit work, receive an acknowledgment, and collect the result later. It accepts sensor events and messages, exposes the loaded profile for reading and for editing on disk, and exposes runtime state and the three live settings. Authentication and the permission model are enforced here.

### Frontend — Telegram Bot

The human surface, serving commanders and viewers. Everything a user sends arrives through one path and is routed by intent. Commanders additionally resolve held events, approve pending runs, review events closed on precedent, edit the profile for the next run, and change the live settings. User accounts are not managed from the bot at all; they are added by an administrator running a command outside the system.

---

## 1. Foundations

### 1.1 Set up the repository and module skeleton
*Requires: nothing*

- Create one top-level package per subsystem — `persistence`, `agents`, `protocols`, `history`, `orchestrator`, `api`, `bot` — plus a `profiles` package that will hold one module per deployment, and a `tools` package for shared helpers that belong to no subsystem.
- Give each package a single module that defines everything the other packages may call. Everything else in the package is internal. For example, other packages call `persistence.interface`, never a module that knows about SQLite.
- Add an automated check that fails the build when one package imports a module of another package that is not its declared entry point. A simple import-graph test is enough; the point is to catch the violation the day it appears rather than after fifty files depend on it.
- Write down, in one short file, which package is allowed to call which. The orchestrator calls everything; the persistence layer calls nothing; the bot calls only the API.

### 1.2 Define the domain vocabulary
*Requires: nothing*

- Write one document listing every entity the system passes between subsystems — Profile, Event, Message, Protocol, Agent, Tool, Run, Step, Precedent, Insight, Summary, User — and for each, its exact fields, the type of each field, and whether it may be empty.
- Define a **Step** precisely, because it is the contract between the Main Agent and the executor: the name of one agent, the task text written for that agent, and the list of tool names that agent may use during this step. Nothing else. The executor must be able to run a step without consulting the protocol.
- Define a **Precedent** as a prior event with the same classification and area, whose occurrence timestamp falls inside the lookback window, together with how it was handled — which protocol ran, what the agents did — and how it ended. State explicitly that a precedent record without an ending is unusable, since closure requires knowing the prior event was resolved.
- Define the two hold states an event may enter, **held for clarification** and **held for approval**, and state that an event is in at most one at a time. Clarification always precedes approval in the flow, so an event cannot be in both.
- Reserve **human activation** as a built-in event type present in every deployment. It marks an event that came from a person asking for an action rather than from anything observed in the field. Note that it is the only classification not drawn from the profile.
- Treat this document as the reference every later task builds against. When a later task and this document disagree, this document is wrong and must be corrected first — not worked around.

### 1.3 Build the base configuration
*Requires: 1.1*

- Create one configuration file holding only values true of every deployment. Today that is the model each of the three core agents runs on; keep it open for later additions of the same kind.
- Name the three models separately — one for the Main Agent, one for the History Agent, one for the Insights Agent — rather than a single shared value. The Main Agent makes the heavy judgments and justifies a strong model; the History Agent summarizes large volumes of text and can use a cheaper one.
- Load this file before any profile is loaded, since the core agents are constructed regardless of which profile was named.
- Reject any deployment-specific value in this file — ports, database paths, agent rosters, protocols. If a value differs between the firefighting and the military deployment, it belongs in the profile.

### 1.4 Define the profile structure
*Requires: 1.2*

- Specify a profile as a Python module exposing a fixed set of names the loader reads. Write the specification down so a person can author a new profile from it without reading the loader's source.
- Have the profile **construct** its specialist agents — actual instances, each built with its model passed to the constructor — and expose them as a list. Constructing rather than naming is what lets two profiles run the same agent class on different models without touching the class.
- Have the profile expose its protocols as a list of protocol objects, each fully populated: name, description, participating agent names, approved tool names, expected success output, criticality level, and approval flag.
- Have the profile expose its event types and its areas as plain lists of strings.
- Have the profile expose the path to its database file and the port its API binds to. Two profiles running at once must not collide on either, so neither may have a default.
- Have the profile expose the starting values for the retry count, the risk threshold, and the precedent lookback window. These are only starting values; once a deployment has run, the settings store takes over.
- Have the profile expose the **names** of the environment variables holding the bot token and the model credentials — never the values. The file must stay safe to commit and to send to another team in full.

### 1.5 Implement profile loading and selection
*Requires: 1.4, 1.3*

- Read the profile module named by a launch argument. Accept a module path, resolve it, and import it once.
- Fail immediately and clearly when the argument is missing or names a module that does not exist. There is no default profile, because a system that quietly starts as the wrong deployment is worse than one that does not start.
- Freeze what was loaded. Hold the agents, protocols, registries, paths, and port in one immutable structure that every subsystem reads from, and provide no path that mutates it while the process runs.
- Construct the three core agents from the base configuration on every run, before or after the profile as convenient, but always — a profile has no say over whether they exist.
- Read every environment variable the profile names at load time, not at first use. A missing bot token must stop the system at startup, not three hours later when the first notification is sent. Name the missing variable in the failure.
- Take the database path and the port from the loaded profile and pass them to the persistence layer and the API rather than reading them again elsewhere, so there is exactly one place either value comes from.

### 1.6 Validate the profile at startup
*Requires: 1.5*

- Run this validation after loading and before the API accepts anything. A failure here stops the process.
- For every protocol, look up each agent it names in the set the profile constructed. Fail on the first name that does not resolve, and say which protocol named which missing agent.
- For every protocol, collect the tools its named agents expose by calling their exposure functions, and confirm every tool the protocol approves appears among them. A protocol may approve fewer tools than its agents own; it may never approve one that exists nowhere.
- Confirm every protocol carries a non-empty description, a criticality level, and an approval flag that was explicitly set. Treat an absent flag as a validation failure rather than defaulting it to either value — this is the field that decides whether an action runs unattended, and a silent default is the dangerous outcome.
- Confirm the profile declares at least one event type and at least one area, since extraction has nothing to choose from otherwise.
- Report every failure found, not only the first, so an author fixing a profile sees the whole list in one run. Then stop.

**Criticality is validated strictly, like the approval flag, not just for presence** — tightened after the Mission 8 coverage audit found that a profile could satisfy every structural (duck-typed) check with a plain string (e.g. `"low"`) instead of a real `protocols.model.CriticalityLevel` member, and nothing rejected it. That reached two consumers that call `.name` on it and crash (`api/protocols.py`, `protocols/editor.py`) — and, more dangerously, `orchestrator/selection.py`'s high-risk tie-break, which silently picked the *wrong* candidate: Python compares strings alphabetically ("high" < "low" < "medium"), not by severity, so a string-typed criticality could pick the least-critical protocol with no error raised anywhere. Fixed at this one validation boundary (`profiles/validate.py`, `isinstance(protocol.criticality, CriticalityLevel)`) rather than making all three consumers newly defensive — the same choice already made for the approval flag, and for the same reason: some fields are safety-critical enough that duck-typing them isn't worth what it would cost to get wrong.

### 1.7 Build the runtime settings store
*Requires: 1.5, 1.4*

- Hold exactly three values: the retry attempt count, the risk threshold, and the precedent lookback window. Nothing else is live; everything else needs a restart.
- On the first start of a deployment, when no settings file exists beside the database, take the three starting values from the profile and write them out.
- On every later start, read the JSON file and prefer it over the profile. An operator who raised the risk threshold during an incident must not find it silently reverted after a restart.
- Write the complete set to the JSON file the moment any single value changes, before confirming the change to whoever made it. A confirmation sent before the write can outlive a crash that lost it.
- Serve the current values through accessor functions rather than handing out a copy at startup. Anything that caches a value locally will keep using a stale threshold after a change.
- Keep the settings file beside the profile's database, not beside the profile, since it belongs to one deployment's running state rather than to its definition.

### 1.8 Establish structured logging and run tracing
*Requires: 1.1, 1.5*

- Emit logs as structured records with named fields, not formatted sentences, so a run can later be reassembled by querying rather than by reading.
- Put the active profile name on every record, so two deployments logging to the same collector never blur together.
- Generate one trace ID when an event or message enters the system, and attach it to every record produced while handling it — through extraction, every agent call, every tool call, and the final write.
- Log the intent decision for every message: which of question, report, or request it was judged to be, and why. This is the first branch and the easiest one to misdiagnose later.
- Log the extraction result, naming every field left empty. An empty field is a decision, not an absence, and it explains a later clarification hold.
- Log the assessed risk level with its stated reason, and the selected protocol with its stated reason. Both are model judgments and both must be reviewable after the fact.
- Log every hold with which kind it was — unclassifiable event, ambiguous selection, or flagged protocol — since the three look alike in the outcome and differ completely in cause.
- Log the precedent lookup: the window searched, which prior events matched, and whether the match closed the event.
- Log, for every step, the task text the Main Agent wrote and the result the agent returned. This pair is what makes a bad run diagnosable.
- Log every tool call, every tool call blocked by the permission check, and every retry with the cause that triggered it.
- Log the insight text and the final verdict.

### 1.9 Implement the permission model
*Requires: 1.2*

- Define an enum with two members, commander and viewer, ordered so that "at least commander" is a meaningful comparison and a third level can be inserted later without rewriting comparisons.
- Build one table mapping each action to the minimum level it requires: send a message, view history, resolve a held event, approve a pending run, edit the profile, change live settings. Keep it in a single module so adding a level or an action means editing one place.
- Write every authorization check as a question about the action — "does this level permit resolving a hold" — never as a test for a specific level. A check written as "is this user a commander" will be missed when a third level is added, and will silently grant or deny the wrong thing.
- Provide one function the API and the bot both call, so the two cannot drift apart.

### 1.10 Build the user administration command
*Requires: 1.9, 2.7*

- Provide a command-line program, run from the shell on the machine hosting the deployment, that adds, changes, and removes users.
- Take the profile name as an argument, resolve its database path through the same loader the system uses, and write to that database. Two deployments have two user lists and the command must never write to the wrong one.
- Take a Telegram identity and a permission level per user, and validate the level against the enum rather than accepting free text.
- Make it work against a database with no users at all. A fresh deployment has no commander, so no in-system path could ever create the first one — this command is the only way in.
- Expose no equivalent through the API or the bot, and make that absence deliberate rather than incidental: user management is the one capability that stays outside the running system.

---

## 2. Data Layer

### 2.1 Build the event-type registry
*Requires: 1.2, 1.5*

- Read the event types the active profile declares into an in-memory list at startup.
- Add the built-in human-activation type to that list on every run, whatever the profile declares, and reject a profile that tries to declare it itself as a duplicate.
- Serve the combined list to extraction, which uses it as the closed set it must classify into; to the clarification prompt, which offers it to a commander as choices; and to anything validating a type on an incoming event.
- Hold the list fixed for the life of the run. Provide no add or remove operation — a new type means editing the profile and restarting.
- When an event arrives carrying a type that is not on the list, do not accept it and do not silently map it to something near it. Treat it as unclassified, which routes it to a clarification hold.

### 2.2 Build the area registry
*Requires: 1.2, 1.5*

- Read the areas the active profile declares into an in-memory list at startup.
- Serve the list to extraction, which must resolve a reported location to one of these names, and to history queries that filter by area.
- Hold the list fixed for the life of the run, with no add or remove operation.
- Treat an unresolvable area the same way extraction treats any unresolvable field: leave it empty rather than guessing the nearest match. Unlike classification, an empty area does not hold the event — it only narrows what precedent search can find.

### 2.3 Design the event schema
*Requires: 1.2, 2.1, 2.2*

- Store the envelope: a generated event ID, the timestamp the system received it, the source (sensor or Telegram), and the identity of the sender — the sensor identifier or the Telegram user.
- Store an occurrence timestamp separate from the received timestamp. Set it equal to the received timestamp for sensor events, which report in real time. Extract it from the text for Telegram-originated events, where a person may be describing something from hours earlier.
- Store a flag recording that the occurrence timestamp fell back to the received timestamp because extraction found none. Without it, a fallback is indistinguishable from a real match, and every summary built on it looks equally trustworthy.
- Store the classification, constrained to the event-type registry, and the area, constrained to the area registry. Both may be empty; an empty classification is what a clarification hold resolves.
- Store the entities involved, a one-line normalized description of what happened, and a severity as extracted from the text.
- Store the assessed risk level and the reason the Main Agent gave for it, and the selected protocol and the reason it was chosen. Both reasons are needed to review a decision later.
- Store the clarification hold as four fields: whether it is held, what could not be resolved, which commander resolved it, and the classification they chose.
- Store the approval hold as four fields: whether it is held, why it was held (flagged protocol or ambiguous selection), which commander answered, and when.
- Store the precedent result: the IDs of prior events that matched, and whether one of them closed this event without a run.
- Store one record per executed step, each holding the agent name, the exact task text it was given, the tools it was allowed, the result it returned, and the number of attempts it took. The task text is not a duplicate of anything else — it is what the Insights Agent and any later reviewer need in order to tell a failed agent from a badly-asked question.
- Store the insight text produced at the end of the run, and, where the run failed, the reason.
- Store one run outcome field covering every possible ending: succeeded, failed, uncertain, closed on precedent, or declined at approval. A three-valued verdict alone cannot express the last two, and an event with no outcome at all is indistinguishable from one still running.

### 2.4 Design the user table
*Requires: 1.2, 1.9*

- Store each Telegram identity with its permission level, one row per user.
- Keep the table in the same database file as the events. Users then belong to a deployment the same way its events do, and nothing has to be filtered by profile at query time.
- Add no notion of a global or shared user. A person who works both deployments is two rows in two databases.

### 2.5 Preserve raw text
*Requires: 2.3*

- Store the text exactly as received, byte for byte, in its own column beside the structured fields extracted from it.
- Never overwrite it. Not when extraction is re-run, not when a commander corrects a classification, not when a later process normalizes anything. Every correction writes to the structured fields and leaves the original untouched.
- Treat this column as the recovery path for every extraction mistake. When a field turns out wrong months later, the raw text is the only thing that can settle what was actually reported.

### 2.6 Design the summary tables
*Requires: 2.3*

- Create three tables — daily, monthly, yearly — with the same shape, rather than one table with a level column, so an index on a period means the same thing in each.
- Store per row: the summary text, the start and end of the period it covers, the time it was generated, and an index for lookup.
- Assign every event to a period by its **occurrence** timestamp, never its received timestamp. A report that arrives today about last night belongs to last night, or every question about a period returns the wrong set.
- Have the summary text retain how each event was handled and how it ended — which protocol ran, what was done, what the outcome was — not only what was observed. Precedent search reads these summaries before it reads any raw event, and a summary that dropped the handling is useless to it.

### 2.7 Define the persistence interface
*Requires: 1.2*

- Define the full set of operations every other subsystem may perform: append an event; fetch events in a time range; fetch events by type and area within a window; write a summary; fetch summaries in a range; store, list, and resolve held events for both hold states; and read, write, and delete users.
- Keep every engine-specific statement inside one implementation module. Swapping SQLite for another engine must mean writing one new module against this interface and changing which one is constructed.
- Allow no SQL string, no engine-specific type, and no engine-specific exception to appear above this boundary. An error must be raised as an interface-level error, or the layer above ends up catching SQLite exceptions and the abstraction is already broken.
- Take the database path as a construction argument rather than reading it from anywhere, so the same code serves any deployment and the tests can point it at a temporary file.

### 2.8 Define the indexing strategy
*Requires: 2.6*

- Index events by occurrence timestamp, since every history question and every summary boundary reads on it.
- Index events by classification and by area **together**, because precedent search filters on both at once and runs on every single event that enters the system. A pair of separate single-column indexes will not serve it well.
- Index summaries by their period boundaries in each of the three tables.
- Ensure a question about an arbitrary date range can be assembled from the smallest number of summary rows plus, where no summary covers a span, the raw events for that span alone.

### 2.9 Implement the SQLite backend
*Requires: 2.7, 1.5*

- Implement every operation in the interface against SQLite, with all schema knowledge confined to this module.
- Open the database file the active profile names. Never derive it from a default or a working directory, so two deployments cannot end up on one file.
- Enable WAL mode at connection time, so readers do not block on a write in progress.
- Route every write through one serialized queue with a single writer. SQLite locks the entire database on write, and this system writes on every incoming event while insights and summaries write concurrently — without the queue, lock errors appear under exactly the load the demonstration will produce.
- Let reads run concurrently. Only writes need the queue.

### 2.10 Build schema migrations
*Requires: 2.9*

- Provide a migration runner that creates the full schema from an empty file, so a new deployment needs no manual setup.
- Provide numbered, ordered migrations that upgrade an existing database, and record the applied version inside the database itself.
- Make the runner safe to invoke on an already-current database — it should apply nothing and exit cleanly.
- Point it at whichever database the active profile opened, so migrating one deployment never touches another.

### 2.11 Write the backend-swap conformance suite
*Requires: 2.7, 2.9*

- Write the whole suite against the persistence interface, constructing whichever implementation is under test through a fixture. No test may reference SQLite, a SQL string, or a file layout.
- Cover every operation in the interface, including the failure cases: fetching a range with no rows, resolving a hold that is not held, writing a summary for a period that already has one.
- Make passing this suite unchanged the definition of a valid replacement engine, and say so in the suite's own documentation.
- Run it in the normal test run, not as an occasional check, since its value is entirely in catching the day someone leaks engine-specific behaviour upward.

### 2.12 Build the seed dataset
*Requires: 2.10*

- Write fixture events spanning several months as **completed historical records** — each with its classification, risk level, protocol, steps, and outcome already set. They are not inputs to be processed; they are what the database would look like after months of running.
- Include partial reports, where extraction plausibly left fields empty, and contradictory reports about the same occurrence, so summarization can be checked for reconciling them away.
- Include reports whose occurrence timestamp falls before their received timestamp, to exercise period assignment and late-arrival handling.
- Include records at both high and low assessed risk, since several behaviours branch on the threshold.
- Include repeated events of the same classification and area, spaced so that some fall inside a typical lookback window and some outside it, so precedent search has both a match and a near-miss to find.
- Include at least one prior event that was **not** resolved, so closure can be checked for refusing to rely on it.
- Include text that no classification fits, so the clarification path can be driven from fixtures rather than only live.
- Include human-activation records, so their exclusion from precedent closure is testable.

### 2.13 Add held-event lookup by event ID
*Requires: 2.7, 2.9*

- Add `fetch_held_event(kind, event_id) -> dict | None` to the persistence interface and the SQLite backend, following the same precedent `fetch_event`/`update_event` already set.
- Return the hold whether it is resolved or still pending, including `resolved_by` and `resolved_at` when set, so a caller can distinguish "pending", "resolved by X at T", and "no such hold" in one lookup.
- Extend the backend-swap conformance suite (§2.11) with this method, same as every other persistence method.

---

## 3. Agent Framework

### 3.1 Define the abstract agent class
*Requires: 1.2*

- Declare `process(text, allowed_tools) -> result_text` as the only public entry point. Every caller in the system reaches every agent through this signature and no other.
- Take the model identifier as a constructor argument and store it on the instance. This is what lets one agent class appear in two profiles running two different models without a subclass or a conditional.
- Require each concrete agent to declare its role and system prompt, and to implement its tools as methods inside its own class, so the agent is one self-contained file.
- Provide, in the base class, everything common: holding the descriptor, the CrewAI instance, the exposure function, and the invocation path. A new agent should have to write only what makes it different.
- Keep the base class small. Every additional requirement it imposes is a requirement on every future agent, and the goal is that adding one is a single file and a line in a profile.

### 3.2 Define the agent descriptor
*Requires: 3.1*

- Hold the agent's name, its role, its system prompt, its tool list, and the model it was constructed with.
- Write the role as a description of what this agent is for and what it is good at, aimed at being read by the Main Agent when it decides who to task and what to ask them. A role written only for a human reader will produce poor task text.
- Hold one CrewAI instance, created when the agent is constructed and reused for the life of the run. Rebuilding it per call discards whatever state the framework keeps and costs time on every event.
- Expose the descriptor through the registry so the Main Agent can read roles without constructing anything or reaching into agent internals.

### 3.3 Implement the tool-exposure function
*Requires: 3.1*

- Give every agent a public function that returns the tools it implements, each with its name and a description of what the tool does and when it is appropriate.
- Make it callable by anything, with no privileged access — profile validation calls it at startup, the Main Agent calls it when writing tasks, and the question flow calls it when deciding what may be passed.
- Return the description in terms a model can act on. "Queries sensor status" is usable; "sensor tool" is not.
- Derive the list from the agent's actual implementation rather than a hand-maintained constant, so a tool cannot be added without appearing here.

### 3.4 Classify tools by side effect
*Requires: 3.3*

- Mark every tool either read-only or side-effecting, where side-effecting means it changes something outside this system.
- Mark every side-effecting tool as idempotent or not, meaning whether running it twice with the same input leaves the world in the same state as running it once.
- Return both marks from the exposure function alongside the name and description, since two separate consumers depend on them: the retry policy, which may only replay a step whose tools are read-only or idempotent, and the question flow, which may only pass read-only tools.
- Require the marks explicitly on every tool, with no default. An unmarked tool that the retry policy assumes is safe is the exact path to a duplicated action in the field.

### 3.5 Implement the CrewAI adapter
*Requires: 3.2*

- Construct individual CrewAI Agents only. Do not use Crew, do not use Tasks, and do not let CrewAI orchestrate between agents — orchestration lives in the protocol executor, and a second orchestrator competing with it is the failure this decision was made to avoid.
- Build each agent's CrewAI instance once from its descriptor: role, system prompt, tools, model.
- Invoke the instance with the task text for a single step, and extract the result as text.
- Keep the instance alive on the descriptor between calls rather than reconstructing per invocation.

### 3.6 Implement model routing
*Requires: 3.5, 1.3*

- Route each invocation to the model its agent was constructed with, read from the descriptor at call time.
- Construct the three core agents with the models named in the base configuration, since no profile constructs them and nothing else would supply one.
- Resolve credentials from the environment variables the profile named, already read at load time.
- Ensure changing one agent's model has no effect on any other — no shared client configured once with a single model, no global default that some agents fall through to.

### 3.7 Enforce tool permissions at call time
*Requires: 3.5, 3.3*

- Check every tool an agent attempts against the `allowed_tools` list passed on that specific call, and refuse anything not on it.
- Perform the check per invocation, never once at construction. The allowed set comes from the protocol and differs between runs, so the same agent legitimately has different permissions on two consecutive events — binding tools at construction would make that impossible to express.
- Block the call, return a refusal the agent can act on, and log the attempt with the agent, the tool, and the step. A blocked attempt is a signal worth reviewing, not a routine event to swallow.
- Treat this as a security boundary rather than a convenience. It is the only thing standing between a protocol's declared authority and every tool every loaded agent happens to own.

### 3.8 Build the agent registry
*Requires: 3.2, 1.5*

- Register the three core agents on every run, from the base configuration.
- Register every specialist agent instance the active profile constructed, and accept agents from no other source — nothing may register itself at import time, or a half-finished class left in the tree becomes live.
- Support lookup by name and enumeration of everything loaded.
- Return each agent's role and tool list together as one structure. The Main Agent needs both to write a task, and profile validation needs both to resolve a protocol's references; splitting them means two calls and two chances to drift.

### 3.9 Implement the unclear-task signal
*Requires: 3.1*

- Give the agent a way to report that the task it was handed is unclear or unactionable — a distinct return, not an exception and not an error string.
- Keep it separate from an execution failure, because the two lead to opposite retries: a failure resends the same text, an unclear task is sent back to the Main Agent to be rewritten. Collapsing them produces a loop that resends text the agent already said it could not act on.
- Require the agent to state what is missing — which parameter, which context, which ambiguity — so the rewrite has something to work from.
- Count this against the same attempt limit as a failure, so an agent that keeps reporting its task unclear eventually exhausts the run rather than looping.

### 3.10 Handle timeouts and agent errors
*Requires: 3.5*

- Detect model errors, timeouts, and output that cannot be parsed into a result, and surface each as a distinct outcome the executor can branch on.
- Apply a timeout to every agent invocation. Without one, a hung model call stops the serial event queue behind it.
- Distinguish a call that failed from a call that returned successfully with a poor result. Only the first is retried; the second is the success judgment's problem, and retrying it wastes calls and can duplicate side effects.
- Include enough context in the returned error — the agent, the step, the underlying cause — to log it usefully without the executor having to reconstruct it.

### 3.11 Build the reference agent
*Requires: 3.1, 3.3, 3.4, 3.8*

- Implement one minimal specialist agent whose purpose is to be the template a real agent is copied from, and to make the pipeline runnable before any real agent exists.
- Give it two stub tools: one read-only, returning a canned status, and one side-effecting and non-idempotent, recording that it acted. Both are needed — the read-only tool exercises the question flow's restriction, and the side-effecting one is the only way to test that a retry does not repeat an action.
- Take its model as a constructor argument like any other agent, so the demonstration profile constructs it the same way a real profile would.
- Give it a real role and system prompt rather than placeholders, since it is what someone will read when writing the first real agent.
- Keep it short enough to read in a minute.

### 3.12 Document the agent-authoring path
*Requires: 3.11*

- Write down every step to add an agent: subclass the base class, write the role and system prompt, implement the tools as methods, mark each tool read-only or side-effecting and idempotent or not, and construct the agent in a profile with its model.
- Point at the reference agent as the working example rather than reproducing its code in the document, so the two cannot drift apart.
- State what happens if a step is skipped — an unmarked tool fails validation, an agent not constructed in a profile is never loaded — so the failure modes are recognizable.
- Keep it to one page. A longer document stops being read and stops being updated.

---

## 4. Protocol Engine

### 4.1 Define the protocol model
*Requires: 1.2, 3.8*

- Hold the protocol's name, its description, the agents that participate, the tools those agents are approved to use within it, and the output that counts as success.
- Write the description as the mechanism by which this protocol is chosen. Protocols are not bound to event types; the Main Agent reads every description and picks one. A description must therefore state both when the protocol applies and when it does not, in terms a model can discriminate on. "Handles sensor faults" is not enough; "applies when a sensor reports implausible or contradictory readings, not when the sensor is unreachable" is.
- Hold a criticality level used for exactly one purpose: choosing between candidates that fit equally well. It is not a risk level, not a priority for scheduling, and it does not decide whether the protocol runs.
- Hold an approval flag stating whether the protocol may run before a human says yes. This is the only field that stops a run.
- Keep criticality and the flag fully independent. A critical protocol may be one you want run instantly and unattended; a low-criticality protocol may touch something you always want a person to see first. Any code that infers one from the other reintroduces the coupling this separation exists to remove.
- Write the expected success output as something a model can compare a result against — a description of what a successful outcome looks like, not a string to match exactly.

### 4.2 Load protocols from the profile
*Requires: 4.1, 1.5*

- Instantiate every protocol the active profile declares, at startup, into an in-memory set.
- Hold the set fixed for the life of the run, with no add, edit, or remove operation reachable from the running system.
- Depend on startup validation having already rejected anything unusable, so this step does no checking of its own and the running system never handles a malformed protocol.
- Expose the set for reading — the Main Agent needs every description as context on every event, and the API serves it on read.

### 4.3 Implement profile protocol editing
*Requires: 4.2, 1.4*

- Return the currently loaded protocol set on a read. Reads describe what is running, not what is on disk.
- On a write, modify the profile file on disk — adding, replacing, or removing one protocol — leaving the loaded set untouched.
- Validate every write against the currently loaded agents and their exposed tools before accepting it, using the same checks startup validation runs. Writing a protocol that names a nonexistent agent would otherwise turn the next restart into a failure to boot, discovered at the worst moment.
- Require the approval flag to be present on every write and never supply it by default, matching the startup rule.
- Return on every write a statement that nothing changed in the running system and the edit applies from the next start. This must be unambiguous, because the whole class of dangerous mistakes here is a person believing a protocol is live when it is not.
- Write the file safely — write to a temporary file and move it into place — so an interrupted edit cannot leave a profile that fails to import.

### 4.4 Build the protocol executor
*Requires: 4.2, 3.7, 1.2*

- Accept a list of steps and execute them in order, each step being one agent, the task text written for it, and its approved tools.
- Compose no task text. Use exactly what the Main Agent produced, unmodified — no prefixes, no appended context, no templating. The Insights Agent will later be shown this text as what the agent was asked, and it must be true.
- Pass each step's approved tools to the agent invocation so the permission check has the right set for that call.
- Keep each step's task text together with its result, its allowed tools, and its attempt count, and return the whole list. Both halves reach the Insights Agent, and the pair is what distinguishes a failed agent from a badly-asked question.
- Keep the steps independent enough that one can be logged, retried, or reported on without touching the others.
- Return control to the Main Agent when every step has finished or the run has failed. The executor decides nothing about what happens next.

### 4.5 Implement the retry policy
*Requires: 4.4, 3.4, 3.9, 3.10, 1.7*

- Read the attempt limit from the runtime settings store on each run rather than caching it, so a change during an incident takes effect.
- On an execution failure — a model error, a timeout, unparseable output — resend the same task text unchanged. The text was not the problem.
- On an unclear-task signal, do not resend. Return the step to the Main Agent, which rewrites that one step's task from what the agent said was missing, and execute the rewritten step in place of the original.
- Before any retry, check the step's tools. Retry freely when every tool used is read-only, and when a side-effecting tool is marked idempotent.
- Never replay a step whose side-effecting, non-idempotent tool already acted. Record the step as failed and stop retrying it. Re-running such a step is how one alert becomes two dispatches.
- Apply a backoff between attempts, so a transient model outage is not hammered three times in a second.
- Count execution failures and unclear-task signals against the same limit, so neither can loop indefinitely.

### 4.6 Implement retry exhaustion handling
*Requires: 4.5*

- Record the run as failed, naming the step that exhausted its attempts and the cause of the last failure.
- Write the partial results — every step that did succeed — onto the event record rather than discarding them. A run that failed at step four still learned what steps one through three found.
- Notify whoever originated the event, with enough detail to act: which event, which step, what failed.
- Move on to the next event. A failed run must never stop the queue, and must never leave the current event in an in-flight state that survives a restart.

### 4.7 Author the demonstration profile
*Requires: 4.1, 3.11, 2.1, 1.4*

- Declare a small set of event types and areas plausible for the demonstration domain.
- Construct the reference agent with a model, exactly as a real profile would construct a real agent.
- Declare several protocols, each naming the reference agent and approving some subset of its two stub tools. Give at least one protocol only the read-only tool and at least one the side-effecting tool, so the permission boundary and the idempotency path are both reachable.
- Write descriptions distinct enough that a clear match is genuinely clear, and include at least two protocols whose descriptions overlap enough to force a tie. The tie is required by the selection tests and cannot be produced later without editing the profile.
- Set a criticality level on every protocol, with distinct values on the two tie candidates so the most critical one is unambiguous.
- Set an approval flag on every protocol, with at least one flagged and at least one not, so both branches of the approval path are testable.
- Set the starting values for the retry count, the risk threshold, and the lookback window.

### 4.8 Leave a seam for task-based execution
*Requires: 4.4*

- Structure the executor so that the list of steps could later be replaced by an ordered structure with dependencies between them, without redesigning the interface between the Main Agent and the executor.
- Keep step execution behind one function boundary, so an alternative execution mode could be selected per protocol later.
- Implement none of that mode now, and add no field, flag, or branch for it. The seam is a shape, not a feature.

---

## 5. History System

### 5.1 Implement the history-write path
*Requires: 2.7, 2.5*

- Write every incoming event to the database before anything else happens to it, with its raw text and whatever structured fields extraction produced. An event that is lost before its first write is lost entirely.
- Write every step record back onto the same event record as the run proceeds, rather than accumulating them in memory and writing once at the end, so a crash mid-run leaves a partial but honest record.
- Write the insight and the final verdict when the run ends.
- Write an event that ended without running — closed on precedent, declined at approval, or still held — as completely as one that ran, including why it ended that way. These records are read later by precedent search, and one that omits its ending is unusable there.

### 5.2 Build extraction
*Requires: 5.1, 3.5, 2.1, 2.2*

- Extract the structured fields from the free-form English text on both the sensor and the Telegram paths. Sensors send text like people do, so there is one extraction, not two.
- Restrict the classification to the event-type registry and the area to the area registry, passing both lists as the closed sets to choose from.
- Extract an occurrence timestamp only for Telegram-originated events, resolving relative expressions like "last night" against the received timestamp. For sensor events, set it equal to the received timestamp without asking the model.
- Leave every unresolvable field empty rather than filling it with a plausible guess. A guessed area silently corrupts every later query about that area, and nothing downstream can tell a guess from a fact.
- Report classification failure explicitly as its own outcome, not as an empty field among others, because it is the one that triggers a clarification hold.
- Record which fields were left empty, so the log and the clarification prompt can say what was missing.

### 5.3 Build the History Agent
*Requires: 5.1, 3.5, 1.3*

- Implement it as a core agent on the standard framework, loaded on every run, taking its model from the base configuration.
- Give it two capabilities: summarize a set of events into a period summary, and answer a question against events and summaries it is given.
- Give it read-only tools only. It reads the record; it never acts on the world.
- Write its system prompt to preserve rather than smooth. A summarizer left to its own instincts will resolve two contradictory reports into one coherent account, which is precisely the failure this system cannot tolerate.

### 5.4 Build the summarization pipeline
*Requires: 5.3, 2.6*

- Roll raw events into daily summaries, daily summaries into monthly, and monthly into yearly. Each level summarizes the level below, not the raw events, so cost stays bounded as the record grows.
- Select the events or summaries for a period by occurrence timestamp.
- Have each summary retain what happened, how it was handled, and how it ended, since precedent search reads summaries first and needs the handling.
- Preserve contradictions explicitly — a summary should say that two reports disagreed, not pick one.
- Write each summary back with its period boundaries, its generation time, and its index.

### 5.5 Build the summary scheduler
*Requires: 5.4*

- Trigger summarization on a fixed timer at each day, month, and year boundary.
- Generate the lower level before the level above it depends on it, so a monthly summary never runs against a day that was never summarized.
- Make a repeated run for the same period produce no duplicate — overwrite the existing summary rather than appending a second one — so a restart during a boundary is harmless.
- Detect and fill a period the timer missed, for example because the system was down at the boundary, rather than leaving a permanent gap in the record.

### 5.6 Handle late-arriving events
*Requires: 5.5*

- Detect on write when an event's occurrence timestamp falls inside a period that has already been summarized. Only Telegram events can do this, since sensor events occur when they arrive.
- Regenerate the summary for the affected period from its events.
- Cascade the regeneration upward: the month containing that day, then the year containing that month, since both were built from a summary that has now changed.
- Do this asynchronously rather than in the event's own path, so a late report does not make the person who sent it wait for a year's summaries to rebuild.

### 5.7 Build the query interface
*Requires: 5.3, 2.8*

- Expose one entry point through which any agent queries history, so there is a single place where the "never from memory" rule is enforced.
- Answer only from stored events and summaries retrieved for the question. Never let the History Agent answer from what the model recalls of earlier conversation.
- Return the material the answer was built from alongside the answer, so a caller can see what was actually read.
- Accept filters on time range, classification, and area, since every caller needs some combination of the three.

### 5.8 Implement precedent search
*Requires: 5.7, 1.7*

- Take an event's classification, its area, and the current lookback window read from the settings store.
- Read the period summaries covering the window first, and use them to identify which periods contain candidate matches. Reading every raw event across a long window on every incoming event does not scale.
- Fetch only the raw events those summaries point to, and match on classification and area exactly.
- Return each match with how it was handled — which protocol ran, what the agents did — and how it ended.
- Mark clearly whether each match was resolved. Closure depends on this, and a match that was merely seen and never resolved must never read as a precedent.
- Return an empty result rather than a near match when nothing matches on both fields. A partial match is not a precedent.

### 5.9 Implement range-scoped retrieval
*Requires: 5.7, 5.4*

- For a question bounded to a period, find the narrowest summary level that covers it — a year where a year is asked for, months where several months are, days where a few days are.
- Assemble a span that crosses levels from the coarsest summaries that fit inside it, plus finer summaries at the edges.
- Fall back to raw events only for spans no summary covers, typically the current day.
- Return which sources were used, so an answer built entirely from a yearly summary is distinguishable from one built from raw events.

### 5.10 Verify summary fidelity
*Requires: 5.4, 2.12*

- Run the full pipeline over the seed dataset, producing every level of summary.
- Ask the period questions the raw events could answer, and confirm the summaries still answer them.
- Confirm each summary still carries how its events were handled and how they ended, by running precedent search against summarized periods and checking it finds what the raw events would have.
- Confirm two contradictory reports both survive into the summary rather than being reconciled into one account.
- Confirm a yearly summary, built from monthlies rather than raw events, has not degraded past usefulness across three levels of compression.

---

## 6. Main Agent Orchestration

### 6.1 Build the Main Agent's AI agent
*Requires: 3.5, 3.6, 1.3*

- Implement it as a core agent on the standard framework, loaded on every run, taking its model from the base configuration.
- Use it for every judgment the Main Agent makes — message intent, risk assessment, protocol selection, task writing, and success judgment — rather than building a separate agent per decision. One agent with focused prompts per call keeps the reasoning consistent and the cost visible in one place.
- Give it no tools of its own. It reasons over what it is handed; the specialists act.
- Write a distinct prompt per decision, each stating exactly what it is being asked and what shape the answer must take, since five different judgments cannot share one system prompt without blurring.

### 6.2 Implement clarification holds
*Requires: 5.2, 2.7, 1.9*

- Hold any event whose classification extraction could not resolve, and any event whose stated type is outside the registry, from either source.
- Write the hold to the database, not to memory, so a restart does not lose an event waiting on a person.
- Record what could not be resolved, in the terms the prompt will show a commander: the raw text and which field failed.
- Accept a resolution only from a commander, and only a classification drawn from the loaded registry. Reject free text — the registry is fixed for the run, and accepting a type outside it defeats the constraint everything downstream relies on.
- Record who resolved it and what they chose.
- Resume the flow at risk assessment, not at extraction. The other extracted fields are still valid and re-running extraction would discard the commander's decision.
- Take the held event out of the processing queue while it waits, so events behind it continue.

### 6.3 Implement risk assessment
*Requires: 6.1, 2.3, 1.7*

- Have the Main Agent assess a risk level for every event that reaches this step, from the event's classification, area, description, and severity.
- Read the risk threshold from the settings store at assessment time, and compare the assessed level against it to produce the high or low determination the rest of the flow branches on.
- Write both the assessed level and the reason given onto the event record. Three later behaviours — tie-breaking, precedent closure, and review — depend on this value, and none of them is reviewable without the reason.
- Treat the threshold as live: a commander who raises it mid-incident changes how the next event is handled, not how the current one already was.

### 6.4 Implement protocol selection
*Requires: 6.1, 6.3, 4.1, 4.2*

- Give the AI agent every loaded protocol with its full description as context, along with the event's extracted fields and raw text.
- Have it choose the protocol whose description fits, and return the reason. Selection is by description alone — there is no mapping from event type to protocol.
- When the event is high-risk and no single protocol clearly fits, select the most critical among the candidates and proceed. Waiting is the greater risk.
- When the event is low-risk and no single protocol clearly fits, do not choose. Return an ambiguous-selection signal with the candidates, and let the hold mechanism ask a commander which to run.
- Record the protocol chosen and the reason, or the ambiguity and its candidates.
- Apply the same rule to a commander's own request. A commander bypasses the approval flag, not ambiguity — there is no protocol to run yet, so there is nothing their authority could authorize.

### 6.5 Implement precedent lookup
*Requires: 6.4, 5.8, 1.7*

- Query precedent search with the event's classification, its area, and the current lookback window.
- Run this after a protocol has been selected and before any hold or any task is written. It is read-only, it changes nothing, and it may remove the need for both.
- Record which prior events matched and how each was handled, on the event record, whether or not they lead to closure.
- Pass the matches forward to closure evaluation and to task formulation, since both use them for different purposes.

### 6.6 Implement closure on precedent
*Requires: 6.5, 6.3*

- Close the event without running only when it is below the risk threshold.
- Close only against a precedent that was itself resolved. A prior event that was seen and never resolved is not evidence that anything works.
- Never close a high-risk event, however clear the match. The threshold is the guard against a single wrong precedent silently suppressing everything like it.
- Never close a human-activation event. A person asked for something, and answering with silence because a similar request was handled last week is the wrong response even when the precedent is sound.
- Record the closure with the precedent it relied on, so a later reviewer can see exactly what justified not acting.
- Notify commanders immediately on every closure. This is the one path where the system acts by not acting, and it is invisible unless announced.

### 6.7 Implement approval holds
*Requires: 6.6, 6.4, 2.7, 1.9*

- Hold before execution when the selected protocol carries the approval flag — whatever the risk, however clear the match, whoever sent it.
- Hold also when selection was ambiguous at low risk, so a commander chooses which protocol runs. Record which of the two reasons caused the hold; they ask different questions and the prompt must reflect that.
- Reach this step only after precedent lookup and closure evaluation, so no commander is asked to approve a run that precedent would have made unnecessary.
- Skip the approval flag when the event came from a commander's own request. Do not skip an ambiguous selection for the same commander — that hold asks which protocol to run, which their authority does not answer.
- Write the held run to the database with everything needed to resume it: the event, the selected protocol or the candidates, the assessed risk, and the reason for the hold.
- Accept an answer only from a commander, and validate the answering user's level at the moment they answer rather than when the hold was created.
- Resume execution from task formulation on approval. End the run as declined on rejection, writing that outcome to the event record.
- Record why it was held, who answered, and when.
- Take the held run out of the processing queue while it waits.
- Accept a third decision shape alongside "approved"/"rejected": a candidate protocol name, for the ambiguous no-clear-fit case (§6.4). This is additive only — the existing two values, and every existing approve/reject call site and its tests, are unchanged; only what the function accepts is widened.
- When the hold's reason is an ambiguous selection, validate the decision against exactly the candidates recorded on that hold, never against "approved"/"rejected" — those two values only answer a flagged protocol's yes/no question (this section's own second bullet). A candidate name outside the hold's own list is rejected clearly, the same way an out-of-registry classification is rejected for a clarification hold, never silently accepted.
- A valid candidate selection records that choice as the resolved selection and resumes through the same path approval already uses — not a second one — so what follows runs with a real selected protocol instead of nothing.

### 6.8 Implement task formulation
*Requires: 6.4, 6.5, 3.8, 1.2*

- Read the role and exposed tools of every agent the selected protocol names, from the registry.
- Include what precedent lookup returned in the context, so a task can say what was tried before and what came of it rather than asking each agent to rediscover it.
- Write a task for every participating agent in a single call, before any of them runs. One call sees all the roles at once and can divide the work between them coherently; a call per agent cannot.
- Produce, for each agent, task text specific to that agent's role — what this agent in particular should determine or do about this event.
- Emit the result as the step list the executor consumes: agent name, task text, and the tools that agent may use, taken from the protocol's approved list.
- Rewrite a single step's task on demand when that agent reports it unclear, using what the agent said was missing, and leave every other step's text untouched.
- Fail the formulation as a whole if the model cannot produce a task for a named agent, rather than sending an empty or generic task.

### 6.9 Build the Insights Agent
*Requires: 6.1, 5.7, 4.4, 1.3*

- Implement it as a core agent on the standard framework, loaded on every run, taking its model from the base configuration.
- Run it once, after every sub-agent in the protocol has finished, and before success judgment.
- Give it every step's task text and result from the current run. Both halves matter: without the tasks, it sees a list of answers detached from their questions and cannot tell an agent that failed from an agent that was asked the wrong thing.
- Give it comparable prior events from the history query interface, so its conclusion sets this run against what has happened before.
- Have it return one conclusion covering both the current run and the historical comparison, not two separate observations.
- Give it read-only tools only. It concludes; it does not act.

### 6.10 Implement success judgment
*Requires: 6.1, 6.9, 4.4*

- Judge whether the Insights Agent's conclusion holds, given the steps and their results — the insight is an input to be assessed, not an answer to be accepted.
- Compare the run's output against the protocol's declared success output, as a judgment of meaning rather than a string comparison.
- Return one of three verdicts: success, failure, or uncertain.
- On uncertain, mark the run, notify a commander, and do not retry. Retrying on uncertainty burns calls without new information, and treating uncertainty as success writes a wrong outcome into the permanent record that every later precedent search will read.
- When the judgment call itself fails — a model error, unparseable output — rerun only the judgment. Never rerun the agents, which have already acted and may have acted on the world.
- Write the verdict and the reasoning onto the event record.

### 6.11 Implement the new-event flow
*Requires: 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 5.1, 4.4*

- Write the event to history first, with its raw text, before any processing.
- Run extraction, and hold the event for clarification if the classification could not be resolved. Resume here when a commander resolves it.
- Assess risk, then select a protocol.
- Look up precedent, and close the event there if closure is permitted. Stop the flow on closure and notify commanders.
- Hold for approval if the flag or an ambiguous low-risk selection requires it. Resume here on approval; end as declined on rejection.
- Formulate the steps, execute them, run the Insights Agent, and judge success.
- Write the outcome back on every branch without exception: both hold states, closure on precedent, decline, and each of the three verdicts. An event that leaves this flow with no outcome recorded is indistinguishable from one still running.
- Rerun formulation if formulation itself fails, since no agent has executed at that point and nothing has touched the world.
- Make every resumption point restartable: an event held for clarification or approval must resume correctly after a process restart, from the database alone.

### 6.12 Implement the question flow
*Requires: 6.1, 3.8, 5.7, 3.4*

- Read the roles of every loaded agent and choose which ones the question needs, using the same reasoning that writes tasks in the event flow.
- Write each chosen agent a task the same way, so a question is answered by the same machinery as an event, with one restriction.
- Pass read-only tools only, whatever the question and whoever asked. Filter the protocol-free tool set through the side-effect marks and drop anything side-effecting. Answering a question must never cause an action in the field, and "check whether sensor twelve is faulty" is a sentence that can be implemented either way.
- Send questions about the past to the History Agent and questions about current state to the agents that can check it, allowing both in one question.
- Compose every agent's result into a single answer rather than returning a list of separate replies.
- Write nothing to the event record. A question is not an event.

### 6.13 Implement message intent classification
*Requires: 6.1, 6.11, 6.12, 4.2*

- Decide, for every message a person sends, which of three things it is: a question to answer, a report of something that happened, or a request for an action.
- Give the AI agent the loaded protocols as context for this decision, since "is this a request for action" is in practice "does this ask for something a protocol does".
- Route a question to the question flow and return the answer to the sender.
- Route a report into the new-event flow, letting extraction classify it normally against the registry.
- Convert a request into an event classified as human activation, record who asked, and enter the new-event flow at risk assessment — extraction has nothing to classify, because the classification is already known.
- Apply the approval flag normally for a viewer's request, and bypass it for a commander's. Never bypass a clarification hold or an ambiguous selection for either.
- Tell the sender which of the three the message was taken as. A person whose question was read as a request, or whose request was read as a question, must be able to see that immediately rather than inferring it from silence.

### 6.14 Hold the History reference
*Requires: 5.7*

- Give the Main Agent one persistent handle to the history query interface, created at startup and used by every flow — precedent lookup, the Insights Agent's comparison, and the question flow.
- Provide no second path into history from the orchestrator, so the "never from memory" rule has one enforcement point.

### 6.15 Enforce serial event processing
*Requires: 6.11, 2.9*

- Process events one at a time, in the order they arrived, with a single queue in front of the new-event flow.
- Queue anything arriving while another event is in flight, and preserve arrival order within the queue.
- Move an event that enters either hold state out of the queue entirely, so a run waiting on a commander does not block everything behind it. A held event is no longer in flight; it is waiting, and it re-enters processing when resolved.
- Ensure the queue survives nothing — it is in-memory and events in it are already written to the database, so a restart replays from the record rather than from the queue.
- Coordinate with the persistence layer's write queue rather than duplicating it. Serial processing is about ordering the flow; the write queue is about SQLite's locking, and they are not the same mechanism.

---

## 7. API Layer

### 7.1 Specify payloads
*Requires: 1.2, 2.3*

- Define the request and response shape of every endpoint, with field names, types, and which fields are optional, in one document that both the bot and any external system can be built against.
- Base every shape on the vocabulary document rather than restating it, so a field cannot mean one thing in the schema and another in the API.
- Define the acknowledgment shape once, since every submitting endpoint returns it.
- Define how a held or closed event appears in a status response, so a caller can tell "still running" from "waiting on a commander" from "closed without running".

### 7.2 Build the async job mechanism
*Requires: 7.1*

- Return an acknowledgment carrying a job ID immediately on every submission, before any processing begins. A protocol run involves several model calls and may wait on a person; nothing can hold a request open for it.
- Track each job's status through every state the flow can reach: queued, running, held for clarification, held for approval, closed on precedent, declined, and finished with a verdict.
- Provide result retrieval by job ID, returning the insight, the verdict, and the outcome once the run has ended.
- Define how a finished result reaches whoever submitted it — the bot pushes it into the chat; an external system polls or receives a callback — and implement one path, not an unspecified mixture.
- Keep job state in the database rather than memory, so a restart does not orphan every job in flight.

### 7.3 Implement `POST /Event`
*Requires: 7.1, 7.2, 6.11*

- Accept sensor events as free-form English text with the sensor's identity, and hand them to the new-event flow.
- Set the occurrence timestamp equal to the received timestamp and do not attempt to extract one, since a sensor reports in real time.
- Record the source as sensor, which is what later distinguishes the two ingestion paths in the record.
- Return the acknowledgment with the job ID.

### 7.4 Implement `POST /Msg`
*Requires: 7.1, 7.2, 6.13*

- Accept anything a person sends, as text with the sender's identity, and hand it to intent classification.
- Return the answer directly when the message was a question, since a question has no job to track.
- Return the acknowledgment with a job ID when the message became an event, and state in the response which of report or request it was taken as.
- Record the source as Telegram, which is what causes extraction to look for an occurrence timestamp.

### 7.5 Unify ingestion
*Requires: 7.3, 7.4*

- Have both paths converge on the same new-event flow the moment the text is known to be an event, with no branch downstream of that point.
- Keep exactly two differences: the recorded source, and whether the occurrence timestamp is extracted from the text or set to the received timestamp.
- Implement the flow once and call it from both endpoints, rather than implementing similar sequences in each and keeping them aligned by discipline.
- Verify the convergence with a test rather than by inspection, since divergence here appears slowly as one path gains a fix the other does not.

### 7.6 Implement `CRUD /Protocol`
*Requires: 7.1, 4.3*

- Serve reads from the protocol set currently loaded in memory, including each protocol's criticality and approval flag.
- Send every write — create, update, delete — to profile protocol editing, which changes the file on disk.
- Return on every write an explicit statement that the running system is unchanged and the edit applies from the next start. Do not return a body that resembles a successful state change, since the entire risk here is a person believing a protocol is live when it is not.
- Reject a write that fails validation against the loaded agents and tools, with the same message startup validation would give.

### 7.7 Implement `GET /SYSTEM`
*Requires: 7.1, 1.5, 1.7*

- Report the active profile name and what it loaded: the agent names, the protocol names with their flags, the event types, and the areas.
- Report runtime state: how many events are queued, how many are held in each state, and whether the summary scheduler last ran successfully.
- Report the three live settings and their current values.
- Report whether the profile file on disk now differs from what is running, so an operator can see there is a pending edit awaiting a restart. Compare a hash of the file taken at load against the file now.

### 7.8 Implement `PUT /SYSTEM`
*Requires: 7.7, 1.7*

- Accept changes to the retry count, the risk threshold, and the precedent lookback window, and to nothing else.
- Validate each value before applying it — a negative retry count or a zero-length lookback window is a configuration error, not an operator preference.
- Write the change to the JSON settings store before returning success, so a confirmation cannot outlive the change it confirms.
- Reject any other field with a message naming it and stating that it belongs to the profile and takes effect only on a restart. A silent ignore here looks exactly like a successful change.

### 7.9 Enforce authentication and authorization
*Requires: 7.3, 7.4, 7.6, 7.7, 7.8, 7.11, 1.9, 2.4*

- Authenticate every caller against the user table before any endpoint logic runs, including the sensor path, which authenticates as a system identity rather than bypassing authentication.
- Apply the permission model at every endpoint through the single shared check, and never through an inline level comparison.
- Restrict profile editing, hold resolution, approval, and settings changes to commander level.
- Expose no endpoint that creates, changes, or removes a user, and treat that absence as a requirement to be tested rather than a gap to be filled later.
- Reject an unknown identity outright rather than treating it as a viewer.

### 7.10 Define the error contract
*Requires: 7.9*

- Distinguish three classes: invalid input, agent or run failure, and internal error, so the bot can present each appropriately without parsing messages.
- Return one consistent shape across every endpoint — a class, a human-readable message, and where relevant the field or protocol at fault.
- Never leak an internal exception, a stack trace, or an engine-specific error into a response.
- Make a failed run distinguishable from a failed request. A run that exhausted its retries is a successful API interaction reporting an unsuccessful outcome.

### 7.11 Implement `POST /Approve/<event_id>` and `POST /Clarify/<event_id>`
*Requires: 7.1, 7.2, 6.2, 6.7, 2.13, 1.9*

- Accept a commander's answer to a pending hold, addressed by the event ID it was created against, and resume the flow from where it stopped.
- Look up the hold via `fetch_held_event`, keyed by event ID rather than the orchestrator's internal hold ID, so the API surface matches what the bot and any external caller actually have on hand.
- `POST /Clarify/<event_id>`: accept a classification drawn from the loaded event-type registry for a pending clarification hold. Reject any value outside the registry. On acceptance, resolve the hold synchronously and resume the flow at risk assessment — this continuation is itself a full run and must follow the same synchronous-prefix / queued-continuation split as §7.2, returning a job ID for `GET /Job/<event_id>` polling rather than blocking the request on it.
- `POST /Approve/<event_id>`: accept a decision of approve, deny, or — for the ambiguous-selection case (§6.4/§6.7) — a candidate protocol name, for a pending approval hold.
  - On approve, or on a candidate protocol name: resolve the hold synchronously, then resume execution from task formulation through protocol execution as a queued continuation, same as above — this is not free, it costs the same several-model-call run §7.2 exists to avoid blocking on, so return a job ID for `GET /Job/<event_id>` polling. A candidate name is passed straight through to the widened `answer_approval_hold` with no translation logic in this layer — the API stays a thin wrapper; §6.7 is the one place that knows what a candidate name means.
  - On deny: resolve the hold and end the run as declined entirely synchronously, in the same request — this path is genuinely final, has no continuation, and needs no queuing. Return the declined outcome directly in the response.
- Validate the answering identity's level at the moment they answer, through the same shared permission check every other endpoint uses — never inline. The orchestrator's own internal permission check inside `answer_clarification_hold`/`answer_approval_hold` still runs underneath as defense-in-depth for any direct, non-API caller — it is not a second inline check duplicating the API-level one.
- Reject an answer to a hold that does not exist, is not in a pending state, or was already resolved, naming who resolved it and when, using `fetch_held_event`'s resolved-state result — not a generic "not found."
- Return the outcome — a job ID for the queued approve/clarify path, or the declined outcome directly for the deny path — so the caller can confirm to the commander what happened next.

### 7.12 Close the bot-integration DTO gaps found in the Mission 8 audit
*Requires: 7.11, 7.10, 7.7, 7.6, 7.2*

Four small, concrete gaps between what `api/*` returns and what
`bot/api_client.py`'s `BotApiClient` DTOs expect — found auditing Mission 8
against this mission's own work, and fixed here rather than deferred to
whoever eventually builds a real `HttpApiClient`, since three of the four
were this mission's own shapes to correct. All four done:

- `"invalid_candidate"` added to `bot/api_client.py`'s `HoldAnswerStatus`
  Literal, matching the status `orchestrator.holds.answer_approval_hold`
  (§6.7) and `api/holds.py` (§7.11) already produce for a candidate name
  outside a hold's own list; `bot/approval.py`'s `_describe_outcome` now
  renders it (the API's own message, same as `invalid_classification`).
- The HTTP-response-to-`BotApiClient`-outcome mapping is written down in
  `docs/api_spec.md` (§7.10's error classes against each `BotApiClient`
  method's return DTO). No shared translation helper was added — nothing
  exists yet to call one, since the real `HttpApiClient` isn't built; the
  documentation alone is what this pass needed, per its own explicit
  allowance. Notably: `MessageSubmissionResult`, `ProtocolWriteResult`'s
  `401`/`403` case, and `SettingsWriteResult`'s `401`/`403` case have no
  DTO slot for an auth failure at all — a real client must raise there,
  not force it into the DTO.
- `GET /SYSTEM`'s protocol summary now reuses `api.protocols
  .protocol_to_dict` (the exact rendering `GET /Protocol` returns, i.e.
  `name`, `description`, `participating_agents`, `approved_tools`,
  `expected_success_output`, `criticality`, `approval_flag`) instead of
  the narrower `{name, approval_flag}` shape — option (a) from this
  subtask's original text, chosen because both endpoints already read
  the same `ProtocolSet`, so one shared rendering function costs nothing
  and keeps the two responses from ever drifting apart. `ProfileView`/
  `ProtocolView` are now satisfiable from `GET /SYSTEM` alone.
- `GET /Job/<event_id>`'s response now includes `steps_completed` and
  `failed_step_agent_name` when applicable — both were already fully
  derivable from `persistence.fetch_event`'s existing `steps` list, no
  schema change needed: a step that failed is persisted with
  `result_text=None` (`protocols.retry.StepOutcome`'s own failure shape,
  confirmed by reading it), and `protocols.executor.execute_steps` stops
  at the first failing step, so at most one persisted step ever has a
  `None` result and it's always the last one — that step's `agent_name`
  is `failed_step_agent_name`; every step with a non-`None` result,
  formatted and in order, is `steps_completed`.

---

## 8. Telegram Frontend

### 8.1 Register and configure the bot
*Requires: 1.5*

- Read the bot token from the environment variable the active profile names, already resolved at load time.
- Use the profile's port when talking to the API, so two deployments running at once each reach their own backend.
- Fail at startup if the token is absent or rejected, rather than starting a bot that silently receives nothing.
- Run one bot per deployment. Two processes polling one token compete for the same messages and each sees half of them.

### 8.2 Resolve users against the user table
*Requires: 8.1, 1.9, 2.4, 7.9*

- Look up every Telegram identity in the user table on every interaction, to find its permission level.
- Refuse an identity that is not in the table, with a message saying the user is not registered. Users exist only through the administration command, so an unknown sender is not a new user — they are someone who should not be here.
- Refuse an action above a user's level with a message saying so, naming what was refused. A silent no-op leaves a commander believing they approved something.
- Provide no command that adds, changes, or removes a user, and no command that reports the user list.

**Gap against this subtask, found in the Mission 8 deep audit and fixed:**
`bot/app.py`'s `_on_profile_command` and `_on_settings_command` called
`resolve_caller` for their `add`/`edit`/`remove`/`set` branches, but **not**
for their `view`/`diff` branches — `/profile view`, `/profile diff`, and
`/settings view` reached `profile_commands.view_profile`/
`profile_diff_status`/`settings_commands.view_settings` with no identity
lookup at all, so an unregistered Telegram user could read the running
profile and live settings, contradicting this subtask's first bullet
unconditionally ("every interaction," not "every write"). Fixed via a
shared `bot.app._resolve_caller_or_refuse` helper, now called by all four
branches (`view`, `diff`, `add`/`edit`/`remove`, `set`) — required level is
viewer, the lowest registered level, matching §8.7/§8.8's "allow viewers to
read" (a real permission level, not "anyone"). The regression tests
(`tests/test_bot_app.py`) exercise the real command handlers
(`_on_profile_command`/`_on_settings_command`) directly, against a
`FakeBotApiClient` with zero registered users, confirming the read is
refused — not `view_profile`/`profile_diff_status`/`get_settings_view` in
isolation, which is what the pre-existing test suite checked and could not
see this gap by construction. Also confirmed: a registered viewer can
still read (positive case), and the write branches' existing refusal is
unchanged (no regression).

### 8.3 Implement the single message entry point
*Requires: 8.1, 8.2, 7.4*

- Send everything a user types to the message endpoint. There is no separate command for reporting and no command for asking — intent classification decides, and a second entry point would let the user's framing override that judgment.
- Return the answer in the chat when the message was a question.
- Tell the sender when their message became an event and which of report or request it was taken as, in the same acknowledgment the message endpoint returns before any model call runs (§7.2). Whether it goes on to land on an approval hold is not yet known at that moment — it depends on risk assessment and protocol selection, which happen later, after the acknowledgment is already sent — so that outcome is delivered separately, once it is known: via the notification feed (§9, once built) or by polling the job (§7.2). Silence after a request is indistinguishable from the system having ignored it, which is what the immediate acknowledgment itself already rules out — it does not need to also carry an outcome nothing has decided yet.
- Reserve slash-commands for the operational actions — clarification, approval, profile, settings — so they never collide with free text a user is reporting.

### 8.4 Implement clarification prompts
*Requires: 8.2, 7.11*

- Push a held event to commanders immediately, showing its raw text and stating exactly what could not be resolved.
- Offer the loaded event types as the choices, presented as buttons rather than free text, since the registry is fixed for the run and only a type from it can be accepted.
- Send the commander's choice back to the held event, and confirm to them that the flow resumed.
- Handle two commanders answering the same hold: accept the first and tell the second it is already resolved and by whom.

### 8.5 Implement approval prompts
*Requires: 8.2, 7.11*

- Push a pending run to commanders immediately, with the event, the assessed risk and its reason, and why it is being held.
- Present the two hold reasons differently, because they ask different questions. A flagged protocol asks whether to run this protocol; an ambiguous selection asks which of these protocols to run, and must show the candidates.
- Send the answer back to the held run, and confirm to the commander what happened next — resumed or declined.
- Handle a second commander answering an already-answered hold by telling them it is resolved and by whom.
- Notify commanders separately of runs the judgment marked uncertain. That is not an approval request — nothing is waiting on it — and presenting it as one invites an answer that has nowhere to go.

**Gap against §8.4/§8.5, found in the Mission 8 deep audit and fixed — an ID
mismatch with `api/holds.py`:** `api/holds.py`'s `POST /Approve/<event_id>`
and `POST /Clarify/<event_id>` are deliberately keyed by event ID (§7.11's
own design — the API surface is meant to match what a caller already has on
hand). `bot/clarification.py` and `bot/approval.py` used to encode
`notice.hold_id` — not `notice.event_id` — into the Telegram
`callback_data` they build, and forward that same `hold_id` to
`BotApiClient.answer_clarification_hold`/`answer_approval_hold`; a real
HTTP-backed client implementing those methods would have had no `event_id`
in hand at the point it needed to call `POST /Clarify/<event_id>`/
`POST /Approve/<event_id>`. `api/holds.py`'s event-ID design stayed as-is;
the fix landed on the bot side: both modules now encode and forward
`event_id`, using the `event_id` field
`HeldClarificationNotice`/`HeldApprovalNotice` already carried alongside
`hold_id` — no new field was needed on either DTO. Updated alongside it:
`bot/api_client.py`'s `BotApiClient.answer_clarification_hold`/
`answer_approval_hold` abstract signatures (parameter renamed from
`hold_id` to `event_id`, docstrings corrected), `UnimplementedApiClient`'s
matching methods, `tests/bot_fakes.py`'s `FakeBotApiClient`, and
`tests/test_bot_clarification.py`/`tests/test_bot_approval.py` — both now
carry a dedicated test confirming the callback data encodes `event_id` and
never `hold_id`, using notices whose two IDs deliberately differ so a
regression back to `hold_id` fails loudly rather than passing by
coincidence.

### 8.6 Implement precedent-closure notifications
*Requires: 8.2, 6.6*

- Notify commanders immediately whenever an event is closed without running.
- Include the event, the precedent it matched, and how that precedent ended, so the closure can be judged rather than merely noticed.
- Send these immediately and individually rather than batching them into a digest.
- Make them clearly informational, requiring no response, so they are not mistaken for an approval request.

### 8.7 Implement profile commands
*Requires: 8.2, 7.6, 7.7*

- Provide a command to view the active profile: its agents, its protocols with each one's criticality and approval flag, its event types, and its areas.
- Provide commands to add, edit, and remove a protocol in the profile file, requiring the approval flag to be given explicitly.
- State on every write that nothing changed in the running system and the edit applies from the next start.
- Provide a command that reports whether the file on disk now differs from what is running, so a commander can see there is a pending restart.
- Restrict every write to commander level; allow viewers to read.

### 8.8 Implement settings commands
*Requires: 8.2, 7.8*

- Provide a command to view the retry count, the risk threshold, and the lookback window with their current values.
- Provide commands to change each, validating the value before sending it.
- Confirm on each change that it took effect at once and was written to the settings store — the opposite of the profile commands, and worth stating explicitly, since a commander using both will otherwise not know which is which.
- Restrict changes to commander level.

### 8.9 Deliver asynchronous results
*Requires: 8.3, 7.2*

- Acknowledge every submission immediately, so a person never waits on a silent chat while several model calls run.
- Deliver the result when the job finishes, with the insight, the verdict, and what was done.
- Deliver to whoever submitted it, in the chat where they submitted it.
- Reference the original message when delivering, since minutes may have passed and the person may have sent others meanwhile.

### 8.10 Format output for chat
*Requires: 8.3*

- Respect Telegram's message length limit, splitting longer output across messages at sensible boundaries rather than mid-sentence.
- Make the three unprompted message types — clarification request, approval request, closure notice — visually distinct from each other and from a delivered result, since all four arrive unbidden and two of them require action.
- Lead every unprompted message with what it is and whether it needs an answer.
- Keep results readable: the verdict first, then what was done, then the insight.

### 8.11 Deliver failure notifications
*Requires: 8.9, 4.6*

- Send retry-exhausted failures to whoever originated the event, naming the step that failed and the reason it exhausted.
- Include what did succeed before the failure, since a run that failed at the last step still produced findings.
- Distinguish a failed run from a declined one and from an uncertain verdict. All three end without a clean success and each calls for a different response from the person reading it.

---

## 9. Notification & Identity API

Found genuinely missing, not deferred, during the Mission 8 deep audit: three
`BotApiClient` operations Mission 8 was built against —
`poll_pending_notifications`, `list_commander_chat_ids`, and
`resolve_user` — have no corresponding endpoint anywhere in the API Layer
(§7). §7's own subtasks never named them; §8 assumed they would exist by
the time a real HTTP client replaced `UnimplementedApiClient`. This section
is that missing work. It sits here, between the Telegram Frontend (§8) and
Integration and Hardening (now §10), because §8's own commands and prompts
are the reason it exists and because the demonstration (§10) needs the bot
actually able to receive commander input before it can be exercised
end to end.

### 9.1 Implement the notification feed
*Requires: 7.2, 7.9, 6.2, 6.7, 6.6, 2.13*

- Serve one feed a caller polls for what has become newly relevant since
  they last checked: events newly held for clarification, events newly
  held for approval, runs newly closed on precedent, jobs newly finished
  (with a verdict), and jobs newly failed — the same six kinds
  `bot.api_client.BotNotificationKind` already names on the bot side.
- Scope what a caller sees to what their level permits, through the same
  shared permission check every other endpoint uses (§7.9) — a viewer
  polling this feed sees nothing a viewer isn't otherwise allowed to see;
  holds and approvals are commander-only information, matching §8.4/§8.5's
  own audience.
- Structure the feed so polling twice never redelivers the same
  notification — a caller-supplied position (a timestamp or an opaque
  cursor the previous response handed back) marks how far they've already
  read, the same shape a paging API would use. Deciding the exact
  position format is implementation work, not specified further here.
- Read entirely from state §5/§6 already persist (event outcomes, hold
  records via §2.13's `fetch_held_event`) — this endpoint observes and
  formats existing state, it does not introduce a new notion of what
  counts as "notification-worthy."

### 9.2 Implement the commander roster
*Requires: 7.9, 2.4, 1.9*

- Given an authenticated caller, return the routing information §8.4's
  "push to every commander" and §8.5/§8.6's identical requirement need to
  address every commander individually — read from the user table (§2.4)
  that already distinguishes commander from viewer (§1.9), no new storage.
- Whether a commander's Telegram identity alone is sufficient
  chat-routing information, or a separate chat ID needs to be captured
  somewhere first, is an open implementation question this subtask must
  resolve by reading how `bot/telegram_client.py` actually addresses a
  chat today, not by assuming the user table already has what's needed.

### 9.3 Implement `resolve_user`
*Requires: 7.9, 2.4*

- Given an identity, report whether it is registered and, if so, its
  permission level — authenticated the same way every other endpoint is
  (§7.9's shared check), so a caller must already be a legitimate,
  authenticated party to ask about a *different* identity, the same
  restraint every other endpoint already exercises over information it
  serves.
- This is what lets §8.2's "look up every Telegram identity... on every
  interaction" be implemented honestly against a real API for the first
  time — today nothing exposes this lookup at all, so `bot.users
  .resolve_caller` has no real endpoint to call.

### Forward-looking note (not in scope for this section)

Once §9.1–§9.3 exist, `bot/api_client.py`'s `UnimplementedApiClient` and
`bot/notifications.py`'s polling/dispatch logic will need updating to use
them for real — in particular, `run_notification_poll_loop` will need to
track and pass forward whatever position/cursor §9.1's feed defines, so a
restart of the bot process doesn't either miss notifications or replay
ones already delivered. Not specified further here, and not to be started
under this section — it is `bot/*`'s own future work once this API exists.

---

## 10. Integration and Hardening

### 10.1 Build the sensor simulator
*Requires: 7.3, 2.1, 2.2*

- Write a standalone program that emits synthetic sensor events as free-form English text to `POST /Event`, so the demonstration has a source of traffic and the tests have something to drive them.
- Take a target port as an argument, so it can drive any running deployment.
- Take an emission rate, and support a burst mode that sends many events at once, which is what exercises serial processing and SQLite write contention.
- Support emitting repeated events of the same classification and area, so precedent lookup has matches to find during a live run rather than only in fixtures.
- Support emitting text that no classification fits, to drive the clarification path live.
- Generate text that reads like a real sensor report rather than a template, since extraction is being tested along with everything else.

### 10.2 Run the end-to-end flow test
*Requires: 10.1, 7.3, 6.11, 5.1, 4.7*

- Drive one event from the simulator through every stage: extraction, risk assessment, protocol selection, precedent lookup, task formulation, execution, insights, judgment, and the history write.
- Confirm each stage wrote what it should to the event record — not merely that the run completed, but that the classification, the risk and its reason, the protocol and its reason, the precedent result, every step with its task text and result, the insight, and the verdict are all present.
- Confirm the trace ID connects every log record from ingestion to the final write.
- Run this first among the tests, since almost every later test assumes this path works.

### 10.3 Test profile loading and validation
*Requires: 1.6, 1.5*

- Start with a valid profile and confirm exactly its agents, protocols, event types, and areas loaded — plus the three core agents and the built-in human-activation type, and nothing else.
- Start with a protocol naming an agent the profile never constructed, and confirm the system refuses to start and names the protocol and the agent.
- Start with a protocol approving a tool no agent exposes, and confirm the same.
- Start with a protocol whose approval flag was never set, and confirm the same. This is the check most likely to be quietly relaxed later, and the one whose absence is most dangerous.
- Start with a missing environment variable, and confirm the failure names the variable.
- Start two profiles that differ only in the model passed to the same agent class, and confirm each routes its calls to its own model.

### 10.4 Test profile isolation
*Requires: 10.3, 2.9, 8.1*

- Run two profiles at once on separate ports with separate database files.
- Write events under each and confirm no event written under one appears in the other's history queries or precedent search.
- Add a user to one and confirm they are refused by the other.
- Confirm the two settings stores are independent — changing the risk threshold in one leaves the other unchanged.

### 10.5 Test user administration
*Requires: 1.10, 8.2*

- Run the administration command against an empty database, add the first commander, and confirm they can approve a run.
- Confirm an unknown Telegram identity is refused rather than treated as a viewer.
- Confirm a viewer refused an action receives a message saying so, not silence.
- Search the API and bot surfaces and confirm neither offers any path that creates, changes, or removes a user.

### 10.6 Test ingestion parity
*Requires: 10.2, 7.5*

- Submit the same report text through `POST /Event` and through Telegram.
- Confirm both produce an event handled identically through every stage — same classification, same protocol selection, same steps.
- Confirm the only differences are the recorded source and the occurrence timestamp, which the sensor path sets to the received time and the Telegram path extracts.
- Confirm the Telegram submission was routed as a report rather than a question or a request, since parity is only meaningful when intent classification agreed.

### 10.7 Test the clarification path
*Requires: 8.4, 6.2, 2.12*

- Submit text no classification fits, from both sources, and confirm both are held rather than classified into the nearest type.
- Submit a sensor event whose stated type is outside the registry, and confirm it is held rather than rejected or accepted.
- Resolve a hold with a type from the registry and confirm the flow resumes at risk assessment, with the other extracted fields intact.
- Attempt to resolve a hold with a type outside the registry and confirm it is refused.
- Restart the system mid-hold and confirm the held event is still there and still resolvable.
- Confirm a viewer cannot resolve a hold.
- Confirm events behind a held event continued processing while it waited.

### 10.8 Test protocol selection
*Requires: 4.7, 6.4*

- Send an event that clearly matches one protocol and confirm it is selected from its description alone, with a recorded reason.
- Send an event matching the two tie candidates at high risk, and confirm the more critical one is selected and the run proceeds without waiting.
- Send the same event at low risk and confirm no protocol is selected and a commander is asked which to run.
- Send an ambiguous request from a commander and confirm it is still held. The bypass covers the approval flag only; there is no protocol yet for their authority to authorize.

### 10.9 Test the approval flag
*Requires: 8.5, 6.7, 4.7*

- Send an event matching a flagged protocol at high risk with a clear match, and confirm it waits. This is the case most likely to be broken by a well-meaning optimization.
- Send an event matching an unflagged protocol and confirm it runs with no hold.
- Approve a held run and confirm it resumes at task formulation and completes.
- Reject a held run and confirm it ends as declined, with that outcome on the event record.
- Restart mid-hold and confirm the pending run survives and is still answerable.
- Confirm a viewer cannot approve.
- Confirm a second commander answering an already-answered hold is told it is resolved.

### 10.10 Test message intent and human activation
*Requires: 6.13, 8.3, 4.7*

- Send a question and confirm it is answered with no event created.
- Send a report and confirm it becomes an event classified by extraction against the registry.
- Send a viewer's request needing a flagged protocol and confirm it becomes a human-activation event and waits for approval.
- Send a viewer's request needing an unflagged protocol and confirm it runs without waiting.
- Send the same flagged request as a commander and confirm it runs directly.
- Confirm the sender was told which of the three their message was taken as, in every case.
- Confirm a human-activation event is never closed on precedent, even when an identical prior request was resolved inside the window.

### 10.11 Test precedent lookup and closure
*Requires: 6.5, 6.6, 2.12*

- Confirm a repeated low-risk event matches its precedent and closes without running.
- Confirm an identical event above the risk threshold runs its protocol despite the match.
- Confirm a match to a precedent that was never resolved does not close the event.
- Confirm a match falling outside the lookback window is ignored, and that widening the window through settings brings it back into scope.
- Confirm an event matching a flagged protocol, which then closes on precedent, never reaches a commander for approval — the ordering fix depends on this and it is invisible otherwise.
- Confirm commanders are notified on every closure, with the precedent included.

### 10.12 Test task formulation and reformulation
*Requires: 6.8, 3.9*

- Confirm every agent named by the protocol receives a task written for its own role, not a copy of the same text.
- Confirm what precedent lookup returned appears in the formulated tasks.
- Have an agent report its task unclear, and confirm only that step's task is rewritten while the others are untouched.
- Confirm a plain execution failure resends the same task text unchanged rather than rewriting it.
- Confirm both paths count against the same attempt limit and eventually exhaust rather than looping.

### 10.13 Run the protocol execution regression suite
*Requires: 4.7, 3.7, 6.10*

- Confirm the protocol's named agents run, in order, with the tools it approved.
- Confirm an agent attempting a tool outside the approved list is blocked and the attempt logged.
- Confirm the Insights Agent received every step's task text and result.
- Confirm all three verdicts are reachable, and that uncertain triggers a commander notification and no retry.
- Confirm a failure of the judgment call reruns only the judgment, leaving the executed steps untouched.

### 10.14 Test retry and idempotency
*Requires: 4.5, 4.6, 3.4, 3.11*

- Force a step to fail after the reference agent's side-effecting stub tool has already acted, and confirm the step is not replayed and the action is recorded once.
- Force a step using only read-only tools to fail, and confirm it is retried up to the limit.
- Confirm the attempt limit is read live, by changing it mid-test and seeing the new value take effect.
- Confirm exhaustion writes the failure with the offending step, keeps the successful steps' results, notifies the originator, and lets the next event proceed.

### 10.15 Test the question flow
*Requires: 6.12, 3.11, 3.4*

- Ask about the past and confirm the question reaches the History Agent and is answered from stored records.
- Ask about current state and confirm it reaches an agent that can check it.
- Ask something whose most natural handling would use the side-effecting stub tool, and confirm that tool is never passed and the stub records no action.
- Confirm the same restriction holds when the asker is a commander.
- Confirm an answer drawing on more than one agent is composed into a single reply.
- Confirm nothing was written to the event record.

### 10.16 Test history accuracy over time
*Requires: 5.10, 5.5, 5.6*

- Query across a simulated multi-month span and confirm the answers match what the raw events support.
- Query a range that spans summary levels and confirm it is assembled from the coarsest summaries that fit, with finer ones at the edges.
- Submit a late Telegram report whose occurrence time falls in a period already summarized, and confirm the day, month, and year summaries were all regenerated.
- Confirm a period the scheduler missed during downtime is filled on the next run rather than left as a gap.

### 10.17 Test profile editing and settings persistence
*Requires: 8.7, 8.8, 1.7*

- Add a protocol through the bot and confirm the running system's protocol set is unchanged and the response said so.
- Confirm `GET /SYSTEM` now reports the file on disk differs from what is running.
- Restart and confirm the added protocol is loaded and selectable.
- Change the risk threshold and confirm it takes effect on the next event immediately.
- Restart and confirm the changed threshold survived and overrode the profile's starting value.
- Attempt to change a profile-owned field through `PUT /SYSTEM` and confirm it is rejected with a message rather than ignored.

### 10.18 Test permission enforcement
*Requires: 7.9, 8.2*

- Attempt every restricted action from a viewer and confirm each is refused with a message: resolving a hold, approving a run, editing the profile, changing a setting.
- Attempt each from a commander and confirm each succeeds.
- Attempt each from an unregistered identity and confirm each is refused as unknown rather than as unauthorized.
- Run the whole matrix through both the API and the bot, since the two are separate surfaces over one check and only a test proves they share it.

### 10.19 Test serial processing under load
*Requires: 6.15, 2.9, 10.1*

- Drive a burst from the simulator and confirm events are processed one at a time in arrival order.
- Put an event into each hold state during the burst and confirm the events behind it continued.
- Confirm no SQLite lock errors occurred and no write was lost, with the event count in the database matching the count emitted.
- Confirm the Insights Agent running on every event did not create write contention with the summary scheduler.

### 10.20 Review cost and latency
*Requires: 10.2*

- Count model calls per event across every stage: intent, extraction, risk, selection, formulation, execution, insights, and judgment.
- Measure how much closure on precedent lowers the average, since it is the one path that skips most of the chain.
- Measure wall-clock latency from submission to result, separating model time from waiting time.
- Identify calls that could be merged or reused, starting with the two separate history reads per event — one in precedent lookup and one in the Insights Agent's comparison — which cover overlapping ground.

### 10.21 Set up deployment
*Requires: 10.2, 2.10, 10.4*

- Package the backend, the database, and the bot to run on localhost for the demonstration.
- Take the profile as a launch argument and every host, port, and path from that profile, so the identical build runs on a real server with no code change.
- Verify the package starts from nothing: an empty directory, migrations run, the administration command adds the first commander, and the system serves.
- Verify two deployments start side by side from the same build with two profiles.

### 10.22 Write operator documentation
*Requires: 10.21*

- Document writing a profile from scratch, including every name the loader expects.
- Document adding an agent, pointing at the reference agent as the working example.
- Document adding a protocol and setting its approval flag, stating plainly what the flag does.
- Document adding a user with the administration command.
- Document the three things that arrive unprompted — clarification requests, approval requests, closure notices — and what each expects from the reader.
- Document changing the three live settings, and which changes need a restart instead.
- Document reading the run logs: finding an event by trace ID and following it through every stage.

---

## Critical Path

1.2 → 1.4 → 1.5 → 2.7 → 2.9 → 2.3 → 3.1 → 3.5 → 3.8 → 3.11 → 4.1 → 4.4 → 4.7 → 6.1 → 6.3 → 6.4 → 6.5 → 6.7 → 6.8 → 6.9 → 6.10 → 6.11 → 7.3 → 10.1 → 10.2

The History System (section 5) runs in parallel with sections 3 and 4 once the persistence interface (2.7) exists, converging at precedent lookup (6.5) and the new-event flow (6.11).

Deferrable without blocking the demonstration: the task-mode seam (4.8), late-arriving event regeneration (5.6), and the bot's profile commands (8.7).

---

## Branch Grouping

Tasks are grouped by the files they touch, so a group can be developed on one branch and merged as a unit. Two groups that touch no file in common can run in parallel; the notes call out where that is not true.

The module names below follow the skeleton defined in 1.1.

| Branch | Tasks | Files touched | Notes |
|---|---|---|---|
| **B1 — Skeleton and base config** | 1.1, 1.3 | repo layout, `config/base`, import-graph check | Must merge first. Every other branch adds files inside the structure it creates. |
| **B2 — Vocabulary** | 1.2 | `docs/vocabulary` | Documentation only. Conflicts with nothing, blocks almost everything. Merge alongside B1. |
| **B3 — Profile subsystem** | 1.4, 1.5, 1.6, 1.7 | `profiles/spec`, `profiles/loader`, `profiles/validate`, `config/settings_store` | One branch: loading, validation, and the settings store all read the same profile structure and would conflict if split. |
| **B4 — Persistence core** | 2.3, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10 | `persistence/interface`, `persistence/sqlite`, `persistence/schema`, `persistence/migrations` | Schema, interface, backend, and indexes are one unit — every one of them edits the schema module. |
| **B5 — Users and permissions** | 1.9, 1.10, 2.4 | `auth/permissions`, `cli/user_admin`, user table in `persistence/schema` | Touches the schema module, so it merges **after** B4 or coordinates on that one file. |
| **B6 — Registries** | 2.1, 2.2 | `registries/event_types`, `registries/areas` | New files only. Parallel with B4; needs the profile structure from B3. |
| **B7 — Agent framework** | 3.1 – 3.10 | `agents/base`, `agents/descriptor`, `agents/adapter`, `agents/registry`, `agents/tools` | One branch. The unclear-task signal, timeouts, and permission enforcement all edit the base class and the invocation path. |
| **B8 — Reference agent** | 3.11, 3.12 | `agents/reference`, `docs/authoring_agents` | New files only. Merges after B7 but conflicts with nothing else. |
| **B9 — Protocol model** | 4.1, 4.2, 4.3 | `protocols/model`, `protocols/loader`, `protocols/editor` | The editor writes profile files, so coordinate with B3 on the profile format. |
| **B10 — Executor and retry** | 4.4, 4.5, 4.6, 4.8 | `protocols/executor`, `protocols/retry` | Parallel with B9 — different files, shared model definition only. |
| **B11 — Demonstration profile** | 4.7 | `profiles/demo` | One new file. Merges after B3, B8, and B9 define what it must contain. |
| **B12 — History write and extraction** | 5.1, 5.2 | `history/write`, `history/extraction` | Needs B4's interface and B6's registries. |
| **B13 — Summarization** | 5.3, 5.4, 5.5, 5.6 | `history/agent`, `history/summarize`, `history/scheduler` | Parallel with B14; both depend on B12's write path existing. |
| **B14 — History queries** | 5.7, 5.8, 5.9 | `history/query`, `history/precedent` | Parallel with B13. Precedent search reads summaries, so integration waits for both. |
| **B15 — History verification** | 5.10 | `tests/history` | After B13 and B14. |
| **B16 — Main Agent decisions** | 6.1, 6.3, 6.4, 6.8, 6.9, 6.10, 6.14 | `orchestrator/main_agent`, `orchestrator/selection`, `orchestrator/formulation`, `orchestrator/insights`, `orchestrator/judgment` | Each decision is its own module, but they share the Main Agent's construction, so one branch is simpler than five. |
| **B17 — Holds** | 6.2, 6.7 | `orchestrator/holds`, held-event storage in `persistence/interface` | Both hold states share one persistence shape and one resume mechanism. Touches the interface, so merges after B4. |
| **B18 — Precedent orchestration** | 6.5, 6.6 | `orchestrator/precedent` | Needs B14's precedent search. Parallel with B16 and B17. |
| **B19 — Flows** | 6.11, 6.12, 6.13, 6.15 | `orchestrator/flows`, `orchestrator/queue` | **The main conflict point.** All four edit the same flow module, and every branch above feeds into it. Merge B16, B17, and B18 before starting it. |
| **B20 — API** | 7.1 – 7.10 | `api/*` | One branch. The payload spec, the auth layer, and the error contract are edited by every endpoint. |
| **B21 — Bot** | 8.1 – 8.11 | `bot/*` | One branch, for the same reason. Depends on B20's endpoints. |
| **B25 — Notification & Identity API** | 9.1 – 9.3 | `api/*` | Real new scope discovered auditing B21 against B20, not deferred work — comparable in size to §7.11's own addition to B20. Touches the same files B20 already owns, so it merges after B20 and coordinates with it the same way B17 coordinates with B4 on `persistence/interface`. Implementing it requires reading the actual, current `persistence/interface`, `auth/permissions`, and `orchestrator/holds` code first — the shapes it needs (a cursor/position for the notification feed, whatever `bot/telegram_client.py` actually uses to address a chat) are not fully knowable from this document alone. |
| **B22 — Simulator** | 10.1 | `tools/simulator` | Standalone program. Parallel with everything once `POST /Event` exists. |
| **B23 — Test suites** | 10.2 – 10.20 | `tests/*` | One test file per task, so these parallelize freely among themselves. Each merges once the branch it tests has landed. |
| **B24 — Deployment and docs** | 10.21, 10.22 | packaging, `docs/operations` | Last. |

### Cross-cutting work

**1.8 (logging and tracing)** touches every module and will conflict with any branch open at the same time. Merge it early, on its own, immediately after B1 — before the subsystem branches begin. Adding it late means editing every file a second time.

**2.11 and 2.12 (conformance suite and seed dataset)** belong to B4's area but live in `tests/persistence` and `fixtures/`. They can be a separate branch merged straight after B4.

### Files more than one branch wants

- `persistence/interface` — B4 defines it; B5 adds user storage; B17 adds held-event storage. Merge in that order, or agree the full operation list up front in B4 and let the later branches only implement.
- `persistence/schema` — B4 and B5. Same resolution.
- `orchestrator/flows` — B19 owns it. No other branch should touch it.
- `profiles/spec` — B3 defines it; B9's editor writes against it; B11 is an instance of it. B3 must land first.

### Suggested parallel waves

1. **B1, B2**, then **1.8** alone.
2. **B3, B4** in parallel.
3. **B5, B6, B7, B10** in parallel, plus the conformance suite and fixtures.
4. **B8, B9, B12** in parallel.
5. **B11, B13, B14, B16** in parallel.
6. **B15, B17, B18** in parallel.
7. **B19**, alone.
8. **B20**, then **B21**, **B22**, and **B25** in parallel.
9. **B23** throughout, each suite following its branch; **B24** last.
