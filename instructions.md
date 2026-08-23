# AI Developer Guidelines — Field-Report Multi-Agent System

This document defines the mandatory engineering standards, architectural boundaries, and coding styles for any AI model or automated agent generating, refactoring, or reviewing code in this repository. 

Adherence to these rules is mandatory. Violations will break pipeline consistency and architectural decoupling.

---

## 1. Architectural & Subsystem Rules

1. **Strict Modular Boundaries**
   - The codebase is structured into isolated subsystems: `persistence`, `agents`, `protocols`, `history`, `orchestrator`, `api`, `bot`, `profiles`, and `tools`.
   - Never import a module from another subsystem unless it is that subsystem's designated public entry point (e.g., calling `persistence.interface`, never a direct SQLite implementation module).

2. **The "Never from Memory" Rule**
   - Nothing important lives in a model's memory. Every operational event, action, decision, and step must be persisted to the database via the persistence layer interface. Historical questions must be answered from the historical record, never from conversational recall.
   - *Exception: Raw AI interaction logs (prompts/responses) are ephemeral debug data and are exempt from this rule (see section 3.1).*

3. **Database Engine Agnosticism**
   - All interactions with storage must go exclusively through the defined persistence interface. No subsystem above the persistence layer may write raw SQL strings, use engine-specific exceptions, or reference SQLite directly.

4. **Stateless Profiles & Live Settings**
   - Profiles are immutable during runtime. Only three settings are live: retry count, risk threshold, and precedent lookback window. All changes to live settings must be persisted to the JSON settings store immediately.

---

## 2. Code Style & Formatting Standards

### 2.1 Allman Braces Style (Mandatory for Block Brackets)
When writing control flow blocks, functions, and classes, use the **Allman style** (BSD style), where the opening brace is placed on its own line, indented to the same column as the statement that precedes it.

### 2.2 Readability & Whitespace (Line Breaks)
- **Generous vertical whitespace:** Always separate logical blocks, variable preparation, execution steps, and return statements with explicit blank lines. 
- Avoid dense, unbroken blocks of code. Every distinct phase of a function (e.g., validation, execution, logging, error handling) must be visually isolated by at least one empty line.

### 2.3 Minimalist Comments
- **Code should be self-documenting.** 
- Do **not** write redundant, noisy, or obvious comments.
- Comments are strictly reserved for explaining *why* a non-obvious architectural decision was made, or documenting complex public interfaces.

---

## 3. Logging, Error Handling & Safety Standards

1. **AI Model Interaction Logging (DEBUG_FLAG)**
   - **Console Only, NO Database Persistence:** Raw AI interactions—meaning the exact prompt/context sent to the model and the raw text response received from it—are strictly ephemeral debugging data. **They MUST NOT be saved to the database.**
   - Every interaction with an AI model (including the Main Agent, History Agent, and Sub-agents) must include this logging mechanism.
   - The console output must be strictly controlled by a `DEBUG_FLAG` defined in the system `CONFIG`.
   - When `DEBUG_FLAG` is enabled, print to the console/screen exactly what the agent/sub-agent sent (prompts, payload, context) and exactly what it received. When disabled, nothing is printed.
   - *(Note: While the raw prompts/responses are dropped, the final extracted decisions, operational actions, and parsed outcomes must still be persisted to the database according to Rule 1.2).*

2. **Explicit Error Translation**
   - Catch low-level exceptions at boundaries (e.g., database driver errors) and translate them into domain-specific interface exceptions before bubbling them up.

3. **No Silent Failures**
   - Missing required configurations (such as environment secrets or profile attributes) must fail fast and loud at startup, naming the missing variable or component explicitly.

4. **Idempotency & Side-Effects**
   - Respect side-effect marks on tools. Never retry a step containing a non-idempotent side-effecting tool that has already executed.

---

## 4. Autonomous Action Restrictions (Critical Decisions)

1. **Mandatory Approval for Critical Changes**
   - The AI model is **strictly prohibited** from making autonomous critical decisions regarding core code behavior, business logic modification, architectural patterns, or data structures.
   - Whenever a design choice, ambiguity, or multiple implementation paths arise that affect system behavior, the model **must stop, present the options clearly to the user, and explicitly ask for confirmation/approval** before writing or modifying any code.

---

## 5. Directory & Package Structure Restrictions

1. **Allowed Top-Level Packages and Directories**
   - The AI model is strictly limited to creating files and modules *only* within the pre-approved subsystem packages and standard directories defined by the work plan:
     * `persistence/` (Database interface, SQLite backend, schema, migrations)
     * `agents/` (Agent framework, base class, descriptors, registry, tools)
     * `protocols/` (Protocol model, loader, editor, executor, retry)
     * `history/` (History write, extraction, agent, summarizer, scheduler, queries)
     * `orchestrator/` (Main Agent, selection, formulation, insights, judgment, flows, queues, holds)
     * `api/` (API layer and endpoints)
     * `bot/` (Telegram frontend)
     * `profiles/` (Deployment profiles and loader specs)
     * `tools/` (Shared helpers, simulator)
     * `config/` (Base configuration and live settings store)
     * `cli/` (Command-line tools like user admin)
     * `tests/` (Test suites and conformance suites)
     * `docs/` (System documentation and vocabulary)
     * `fixtures/` (Seed datasets and test fixtures)

2. **Prohibition of Unauthorized Directories**
   - Creating any new top-level directory or package outside of the listed permitted directories is **strictly forbidden** without explicit user approval.
