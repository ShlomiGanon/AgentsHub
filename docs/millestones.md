# GTCA — Ground Truth Time Critical Agents
## One-Month R&D Task Breakdown

> **Purpose of this document**
>
> This document converts the supplied GTCA presentation into a structured R&D backlog.
> It does **not** make product, architecture, operational, security, integration, or deployment decisions that were not explicitly stated in the source material.
>
> Wherever a decision is still required, it is marked as:
>
> **[DECISION REQUIRED] Question: ...**
>
> The current source defines a one-month R&D effort for an agent-based environment intended to accelerate time-critical operations, build real-time situational awareness, analyze visual intelligence, coordinate with other units, support training/scenarios, and operate within strong safety and cyber-security boundaries.

---

# 1. Project Goals

## 1.1 Primary Goals

- Build a multi-agent environment for a local emergency / rapid-response squad.
- Provide a main agent that can communicate directly with the squad commander.
- Generate a current situational picture on demand.
- Generate recommendations based on available information.
- Create specialized agents for defined operational domains.
- Support training and scenario-based improvement.
- Use regional history as part of the system's knowledge.
- Reduce AI hallucinations and major operational mistakes.
- Reduce the cyber-security exposure created by AI agents.
- Provide a simple and accessible user interface.
- Keep the monthly operating cost for a small settlement at or below **3,000 ILS**.

## 1.2 MVP / R&D Success Definition

**[DECISION REQUIRED] Question: What must be working at the end of the one-month R&D period for the project to be considered successful?**

Possible dimensions that need explicit definition:
- Working demo only
- Functional prototype
- Pilot-ready system
- Production-ready subset
- Number of agents that must be operational
- Number of integrations that must be real vs. simulated
- Required response time
- Required reliability
- Required accuracy
- Required security controls
- Required user acceptance criteria

---

# 2. Scope Management

## 2.1 Confirm R&D Scope

### Tasks
- Create a written scope for the one-month R&D period.
- Separate:
  - Must-have capabilities
  - Nice-to-have capabilities
  - Deferred capabilities
- Define what is expected to be implemented versus simulated.
- Define which integrations are required during the R&D month.
- Define which capabilities are only represented by mock data.

### Deliverable
- Approved `R&D Scope Definition`.

### Acceptance Criteria
- Every major capability listed in the presentation is categorized as:
  - In scope
  - Out of scope
  - Simulated
  - Deferred

**[DECISION REQUIRED] Question: Is the one-month target a demo, working prototype, pilot-ready system, or production-ready system?**

**[DECISION REQUIRED] Question: Which capabilities must be real integrations during the first month, and which may be simulated?**

---

# 3. Overall Multi-Agent Architecture

## 3.1 Define Agent Topology

The source describes:
- A main / commanding agent
- A squad-status agent
- A visual agent
- A neighboring forces / police / MDA agent
- A regional-history agent
- An insights agent

### Tasks
- Define the responsibility of each agent.
- Define which agent can call which other agent.
- Define which agent owns the final answer to the commander.
- Define how agents share context.
- Define how agents publish updates.
- Define how conflicts between agent outputs are handled.
- Define timeouts for agent-to-agent requests.
- Define fallback behavior when an agent is unavailable.

### Deliverable
- Agent architecture document.
- Agent responsibility matrix.
- Agent-to-agent communication diagram.

### Acceptance Criteria
- Each agent has a clear responsibility boundary.
- No responsibility is assigned to more than one agent without an explicit conflict-resolution rule.
- Every agent input and output is documented.

**[DECISION REQUIRED] Question: Should all specialized agents communicate only through the Main Agent, or may agents communicate directly with one another?**

**[DECISION REQUIRED] Question: Which agent is the system of record for the current operational state?**

**[DECISION REQUIRED] Question: What should happen when two agents provide conflicting information?**

---

# 4. Main Situational-Awareness Agent

## 4.1 Main Agent Core

The Main Agent is intended to:
- Communicate directly with the rapid-response squad commander.
- Receive instructions.
- Produce a current situational picture.
- Produce recommendations.

### Tasks
- Implement commander-message intake.
- Parse commander requests.
- Classify request type.
- Query relevant specialized agents.
- Aggregate agent outputs.
- Produce a current situational picture.
- Produce recommendations.
- Label uncertain or incomplete information.
- Return the response through the selected user channel.
- Log all inputs, outputs, tool calls, and recommendations.

### Deliverable
- Working Main Agent prototype.

### Acceptance Criteria
- The commander can request a current situation report.
- The Main Agent retrieves data from relevant specialized agents.
- The response clearly distinguishes:
  - Confirmed facts
  - Unconfirmed information
  - Missing information
  - Recommendations

**[DECISION REQUIRED] Question: What exact fields must appear in the "current situational picture"?**

**[DECISION REQUIRED] Question: Should the Main Agent provide recommendations automatically, only when requested, or both?**

**[DECISION REQUIRED] Question: Is the Main Agent allowed to execute actions, or only recommend actions to the commander?**

**[DECISION REQUIRED] Question: What is the maximum acceptable response time for a situational picture?**

---

# 5. Human Authorization and Operational Boundaries

## 5.1 Human-in-the-Loop Policy

### Tasks
- Identify all actions that may change the real world.
- Categorize actions by risk level.
- Define whether each action is:
  - Read-only
  - Suggestion-only
  - Requires commander confirmation
  - Allowed automatically
- Add explicit authorization gates before restricted actions.
- Add audit logging for approvals and rejections.
- Add visible confirmation messages before executing approved actions.

### Examples requiring explicit policy
- Sending messages to squad members
- Contacting neighboring forces
- Contacting police
- Contacting MDA
- Dispatching / launching a drone
- Changing operational status
- Escalating an incident
- Creating an operational "target" or incident object

### Deliverable
- Human Authorization Matrix.

### Acceptance Criteria
- Every externally impactful action has a documented authorization rule.
- No restricted action can bypass the defined approval flow.

**[DECISION REQUIRED] Question: Which actions always require explicit commander approval?**

**[DECISION REQUIRED] Question: Are any actions allowed to run automatically without commander approval?**

**[DECISION REQUIRED] Question: What does "Build targets" mean in this system: incident records, locations, objects to monitor, operational tasks, or something else?**

---

# 6. Squad Status Agent

## 6.1 Squad Member State Model

The source defines an agent that knows the state of every member of the rapid-response squad.

### Tasks
- Define the squad member data model.
- Define valid squad member statuses.
- Define how each member updates status.
- Define how stale status is detected.
- Define how missing responses are represented.
- Define whether location is included.
- Define whether readiness / equipment state is included.
- Define the visibility rules for personal data.
- Store status history where permitted.

### Deliverable
- Squad Member State Model.
- Squad Status Agent prototype.

### Acceptance Criteria
- The system can return the current status of each squad member.
- Each status includes a last-updated timestamp.
- Stale or unknown information is clearly marked.

**[DECISION REQUIRED] Question: What statuses should be supported for a squad member?**

**[DECISION REQUIRED] Question: How does the system receive each member's status: WhatsApp/Telegram reply, app, manual entry, GPS, another system, or a combination?**

**[DECISION REQUIRED] Question: Should the system track live location?**

**[DECISION REQUIRED] Question: If location is tracked, what privacy and retention rules apply?**

**[DECISION REQUIRED] Question: Should the agent track equipment/readiness information for each member?**

---

# 7. Visual Agent — Cameras

The source defines a visual agent responsible for cameras and for reporting what each camera sees.

## 7.1 Camera Source Integration

### Tasks
- Inventory available camera sources.
- Define how camera feeds are accessed.
- Define authentication method.
- Define whether feeds are live, recorded, or simulated.
- Implement source registration.
- Implement source health monitoring.
- Store camera metadata.
- Provide current camera status to the Visual Agent.

### Deliverable
- Camera source integration layer.

**[DECISION REQUIRED] Question: During the one-month R&D period, will the system connect to real settlement cameras, recorded video, simulated camera feeds, or a combination?**

**[DECISION REQUIRED] Question: What camera systems / protocols / vendors must be supported?**

## 7.2 Camera Analysis

### Tasks
- Define what the system is expected to detect or describe.
- Extract relevant frames.
- Submit frames to the selected visual-analysis pipeline.
- Generate structured observations.
- Attach timestamps and camera identity.
- Assign confidence / uncertainty.
- Prevent unsupported conclusions from being represented as facts.
- Forward relevant observations to the Main Agent.

### Deliverable
- Visual observation pipeline.

### Acceptance Criteria
- The system can associate each observation with:
  - Camera
  - Timestamp
  - Observation text / structured result
  - Confidence or uncertainty indicator

**[DECISION REQUIRED] Question: What types of visual events must the R&D prototype recognize or describe?**

**[DECISION REQUIRED] Question: Is the required output free-text description, structured detections, alerts, or all of these?**

**[DECISION REQUIRED] Question: What minimum accuracy or validation threshold is required before visual output may be presented as confirmed?**

---

# 8. Visual Agent — Drones

The source states that the visual agent should also be able to dispatch drones to the incident area.

## 8.1 Drone Integration

### Tasks
- Identify the drone platform.
- Identify available API / SDK.
- Define supported drone commands.
- Define read-only telemetry.
- Define dispatch workflow.
- Define authorization gate.
- Define failure states.
- Define manual override.
- Log all drone-related requests and actions.

### Deliverable
- Drone integration design.
- Prototype integration or simulator adapter.

### Acceptance Criteria
- The system can demonstrate the approved drone workflow.
- No drone action can occur outside the defined authorization policy.

**[DECISION REQUIRED] Question: Is real drone control required in the one-month R&D period, or is a simulated/mock drone integration sufficient?**

**[DECISION REQUIRED] Question: What drone vendor/model/API must be supported?**

**[DECISION REQUIRED] Question: Which drone actions, if any, may the AI initiate?**

**[DECISION REQUIRED] Question: Must every drone launch / movement command require explicit human confirmation?**

**[DECISION REQUIRED] Question: What telemetry must the agent receive from the drone?**

---

# 9. Neighboring Forces / Police / MDA Agent

## 9.1 External Coordination Data

### Tasks
- Define external organizations represented in the system.
- Define available contact channels.
- Define whether real integrations exist.
- Store contact / unit metadata.
- Represent availability or status when provided.
- Prepare suggested outbound messages.
- Record outbound coordination activity.
- Return coordination state to the Main Agent.

### Deliverable
- External Forces Agent prototype.

**[DECISION REQUIRED] Question: Which external organizations must be included in the first-month R&D scope?**

**[DECISION REQUIRED] Question: Are there actual APIs or operational interfaces for police, MDA, or neighboring forces?**

**[DECISION REQUIRED] Question: If no API exists, should the agent only prepare a message/contact recommendation for a human to send?**

**[DECISION REQUIRED] Question: Is the system allowed to send messages directly to external organizations?**

---

# 10. Regional History Agent

The source defines a regional-history agent that makes historical data easy to use for model improvement and operational context.

## 10.1 Regional Data Inventory

### Tasks
- Inventory all available regional historical data.
- Categorize sources:
  - Past incidents
  - Break-ins
  - Known entry routes
  - Maps
  - Training exercises
  - Debriefs
  - Personnel issues
  - Gate / infrastructure issues
  - Documents
  - Messages
  - Structured databases
- Define ingestion format for each source.
- Define metadata requirements.
- Define timestamps and source attribution.
- Define access permissions.
- Define data retention requirements.

### Deliverable
- Regional Data Inventory.
- Ingestion specification.

**[DECISION REQUIRED] Question: What regional historical data already exists?**

**[DECISION REQUIRED] Question: Where is each source currently stored?**

**[DECISION REQUIRED] Question: Which sources are approved for use by the R&D system?**

## 10.2 Historical Knowledge Retrieval

### Tasks
- Index approved historical data.
- Allow query by event, date, location, issue type, or free-text question.
- Return relevant historical context.
- Include source attribution.
- Mark missing or low-confidence matches.
- Make historical context available to the Main Agent and Insights Agent.

### Deliverable
- Regional-history retrieval prototype.

**[DECISION REQUIRED] Question: Should the historical data be used through RAG / retrieval, prompt context, model fine-tuning, or another method?**

**[DECISION REQUIRED] Question: What information must be visible as a source/citation in agent answers?**

---

# 11. Insights Agent

The source gives example insights such as:
- An upcoming trip
- A settlement gate that has not been locked for a period of time
- A known infiltration route
- A history of break-ins
- Personnel problems

## 11.1 Insight Categories

### Tasks
- Define supported insight categories.
- Define required input sources per category.
- Define rule-based vs. AI-generated insights.
- Define severity levels.
- Define confidence levels.
- Define notification thresholds.
- Define whether insights are generated proactively or only on request.
- Include source attribution in each insight.

### Deliverable
- Insight taxonomy.
- Insights Agent prototype.

### Acceptance Criteria
- Every generated insight includes:
  - Insight type
  - Supporting data
  - Timestamp
  - Confidence / uncertainty
  - Recommended next step, if requested and approved

**[DECISION REQUIRED] Question: Should the Insights Agent operate proactively, only on commander request, or both?**

**[DECISION REQUIRED] Question: What exact insight categories are required for the R&D prototype?**

**[DECISION REQUIRED] Question: What sources provide upcoming events such as trips or planned activities?**

**[DECISION REQUIRED] Question: What source determines whether a settlement gate has remained unlocked?**

**[DECISION REQUIRED] Question: How should severity / urgency be classified?**

---

# 12. Messaging Interface

The source says the agents will be built on Telegram or WhatsApp.

## 12.1 Messaging Platform Integration

### Tasks
- Implement inbound message handling.
- Implement outbound message handling.
- Map a user to a system role.
- Support text commands / natural-language requests.
- Support structured quick actions if available.
- Support confirmation flows for sensitive actions.
- Handle message retries.
- Log message delivery status.
- Handle network or provider failures.

### Deliverable
- Working messaging interface.

**[DECISION REQUIRED] Question: Which platform is selected for the R&D prototype: WhatsApp, Telegram, or both?**

**[DECISION REQUIRED] Question: If WhatsApp is selected, which WhatsApp integration/provider should be used?**

**[DECISION REQUIRED] Question: If Telegram is selected, should the system use a bot, private group, individual chats, or another structure?**

**[DECISION REQUIRED] Question: Should all users interact in a shared channel or in private conversations with the agents?**

---

# 13. User Interface

The source requires an accessible and easy-to-use interface.

## 13.1 UX Requirements

### Tasks
- Identify user roles.
- Identify top user workflows.
- Minimize steps required during an incident.
- Define a one-button / one-command situation-picture workflow.
- Define clear confirmation flows.
- Make uncertainty visible.
- Make stale data visible.
- Make system errors understandable.
- Validate mobile usability.

### Deliverable
- User flow document.
- Low-fidelity UX specification.
- Working R&D interface.

**[DECISION REQUIRED] Question: Is WhatsApp/Telegram itself the full MVP user interface, or is a separate web/mobile dashboard required?**

**[DECISION REQUIRED] Question: Who are the user roles in the R&D version?**

**[DECISION REQUIRED] Question: What are the top 3–5 workflows that must be executable with minimal interaction?**

---

# 14. Area Briefing Capability

The source lists the ability to provide a briefing about the area.

## 14.1 Area Briefing Generator

### Tasks
- Define briefing sections.
- Pull current squad state.
- Pull current infrastructure / known issues.
- Pull relevant regional history.
- Pull upcoming events.
- Pull camera / sensor availability.
- Pull external-force contact/readiness information where available.
- Generate a concise operational briefing.
- Mark information that is outdated or unknown.

### Deliverable
- Area Briefing feature.

**[DECISION REQUIRED] Question: What exact sections must be included in an area briefing?**

**[DECISION REQUIRED] Question: Is the briefing intended for routine pre-shift use, incident use, training use, or all of these?**

**[DECISION REQUIRED] Question: What is the desired output format: chat message, dashboard view, PDF/report, or another format?**

---

# 15. Simulator

The source requires a simulator that "starts the story" / launches the scenario.

## 15.1 Scenario Engine

### Tasks
- Define a scenario format.
- Define scenario start conditions.
- Define timed event injection.
- Define simulated squad-member responses.
- Define simulated camera observations.
- Define simulated external-force responses.
- Define simulated infrastructure events.
- Support scenario pause / resume / stop.
- Record all events and system responses.
- Support deterministic replay where possible.

### Deliverable
- Scenario simulator prototype.

**[DECISION REQUIRED] Question: What components must the simulator simulate?**

Potential components requiring explicit selection:
- Text messages
- Squad member status
- Camera feeds
- Drone state
- Police / MDA / neighboring force status
- Gate / infrastructure state
- Location events
- Time-based event progression

**[DECISION REQUIRED] Question: Should scenarios be scripted, randomly generated, manually controlled by an instructor, or a combination?**

**[DECISION REQUIRED] Question: Does the simulator need a separate instructor/operator interface?**

---

# 16. Training and Scenario Data

The source says training exercises will generate data that is loaded to improve the model/system.

## 16.1 Training Data Capture

### Tasks
- Define what is recorded during each exercise.
- Record input messages.
- Record agent responses.
- Record recommendations.
- Record commander decisions.
- Record tool / integration calls.
- Record response times.
- Record errors.
- Record human corrections.
- Record final exercise outcome.
- Store scenario metadata.

### Deliverable
- Training log schema.
- Exercise data capture pipeline.

**[DECISION REQUIRED] Question: Which data points must be saved from every exercise?**

## 16.2 Post-Exercise Review

### Tasks
- Build an exercise review workflow.
- Compare expected versus actual system behavior.
- Mark hallucinations.
- Mark missed information.
- Mark unsafe recommendations.
- Capture commander/instructor feedback.
- Convert approved corrections into reusable training/evaluation data.

### Deliverable
- Post-exercise review workflow.

**[DECISION REQUIRED] Question: Who is authorized to label agent outputs as correct/incorrect?**

**[DECISION REQUIRED] Question: What is the process for approving exercise data before it is used to improve the system?**

## 16.3 Model / System Improvement Method

### Tasks
- Define how approved training data changes system behavior.
- Track versions of prompts, retrieval data, rules, models, and evaluations.
- Re-run historical scenarios after every significant system change.
- Compare results between versions.

### Deliverable
- Improvement and evaluation process.

**[DECISION REQUIRED] Question: Does "improve the model" mean fine-tuning, RAG/knowledge-base updates, prompt updates, rule updates, or a combination?**

---

# 17. AI Accuracy and Hallucination Controls

The source explicitly identifies AI hallucination as a core problem.

## 17.1 Grounding Strategy

### Tasks
- Require source-backed facts where possible.
- Separate facts from recommendations.
- Include timestamps.
- Include data freshness.
- Include confidence / uncertainty.
- Avoid silently filling missing information.
- Add "unknown" states.
- Add agent response validation.
- Add cross-checking for critical information.

### Deliverable
- Hallucination-control specification.

**[DECISION REQUIRED] Question: What types of information require confirmation from two independent sources before being treated as confirmed?**

**[DECISION REQUIRED] Question: What confidence threshold, if any, is required for automated visual or AI conclusions?**

## 17.2 Fallback Behavior

### Tasks
- Define behavior when data is incomplete.
- Define behavior when model output is malformed.
- Define behavior when specialized agents disagree.
- Define behavior when an external service is unavailable.
- Define behavior when the AI cannot answer reliably.

### Deliverable
- Failure and fallback policy.

**[DECISION REQUIRED] Question: When confidence is low, should the system ask the commander a question, provide an "unknown" result, escalate to a human operator, or follow another rule?**

---

# 18. Cyber-Security Boundaries

The source explicitly requires strong boundaries to avoid cyber attacks or major mistakes.

## 18.1 Agent Tool Isolation

### Tasks
- Define allowed tools for each agent.
- Deny all tools not explicitly approved.
- Use least-privilege permissions.
- Separate read permissions from write/action permissions.
- Protect credentials.
- Prevent agents from exposing secrets.
- Validate all tool inputs.
- Validate all tool outputs.

### Deliverable
- Agent Permission Matrix.

**[DECISION REQUIRED] Question: Is there an existing "safe agents environment" that this project must use? If yes, what is it?**

## 18.2 Prompt Injection / Untrusted Input Protection

### Tasks
- Treat external text, messages, documents, and visual content as untrusted.
- Prevent untrusted content from overriding system rules.
- Separate instructions from retrieved data.
- Restrict tool use based on explicit policy.
- Add suspicious-input logging.
- Add adversarial scenario tests.

### Deliverable
- Prompt-injection threat model and test set.

**[DECISION REQUIRED] Question: Which external data sources are considered untrusted?**

## 18.3 Authentication and Authorization

### Tasks
- Define user identity mechanism.
- Define user roles.
- Define agent permissions by role.
- Restrict sensitive operations.
- Add session and access logging.

### Deliverable
- Authentication / authorization design.

**[DECISION REQUIRED] Question: Is authentication required in the first-month R&D prototype?**

**[DECISION REQUIRED] Question: What user roles and permissions are required?**

---

# 19. Auditability

## 19.1 Full Decision Trace

### Tasks
- Log user request.
- Log agent reasoning inputs at the system level without exposing private internal model chain-of-thought.
- Log source data used.
- Log tool calls.
- Log external system responses.
- Log recommendations.
- Log human approvals.
- Log final actions.
- Record timestamps.

### Deliverable
- Audit log schema and viewer/export mechanism.

### Acceptance Criteria
- A completed incident/training session can be reconstructed from system logs.

**[DECISION REQUIRED] Question: How long must audit logs be retained?**

**[DECISION REQUIRED] Question: Who may access the logs?**

---

# 20. Data Architecture

## 20.1 Core Data Models

### Required model categories
- User
- Squad member
- Role
- Incident / event
- Situation report
- Camera
- Visual observation
- Drone
- External force
- Regional-history item
- Insight
- Scenario
- Training run
- Feedback
- Agent output
- Approval
- Audit event

### Tasks
- Define schema for each entity.
- Define relationships.
- Define timestamps.
- Define source/provenance fields.
- Define confidence fields.
- Define retention rules.
- Define access controls.

### Deliverable
- Data model / ERD.

**[DECISION REQUIRED] Question: Which database technology, if any, has already been selected?**

**[DECISION REQUIRED] Question: Which data must persist after an incident ends?**

---

# 21. AI / LLM Selection

## 21.1 Model Requirements

### Tasks
- Define language requirements.
- Define Hebrew support requirements.
- Define vision support requirements.
- Define latency requirements.
- Define context-size requirements.
- Define tool/function calling requirements.
- Define privacy/security requirements.
- Define monthly budget constraints.
- Define fallback-model strategy if needed.

### Deliverable
- Model selection criteria.

**[DECISION REQUIRED] Question: Has an AI/LLM provider already been selected?**

**[DECISION REQUIRED] Question: Is use of cloud-hosted AI permitted for all project data?**

**[DECISION REQUIRED] Question: Is an on-premise/local model required for any data or capability?**

---

# 22. Backend Technology

## 22.1 Backend Foundation

### Tasks
- Set up application repository.
- Set up configuration management.
- Set up secret management.
- Set up API layer.
- Set up agent orchestration layer.
- Set up persistence layer.
- Set up background jobs/events if required.
- Set up logging.
- Set up automated tests.
- Set up deployment process.

### Deliverable
- Running backend foundation.

**[DECISION REQUIRED] Question: Has a backend programming language/framework already been selected?**

**[DECISION REQUIRED] Question: Are there existing company infrastructure standards that this project must follow?**

---

# 23. Deployment Architecture

## 23.1 Environment Design

### Tasks
- Define development environment.
- Define test/simulation environment.
- Define demo/pilot environment.
- Define secrets handling.
- Define network boundaries.
- Define backup strategy.
- Define monitoring.
- Define update procedure.

### Deliverable
- Deployment architecture.

**[DECISION REQUIRED] Question: Should the system run in public cloud, private cloud, on-premise, or a hybrid architecture?**

**[DECISION REQUIRED] Question: Are there restrictions on where settlement / squad / camera data may be stored?**

---

# 24. Cost Constraint

The source requires a maximum operating cost of **3,000 ILS per month for a small settlement**.

## 24.1 Cost Model

### Tasks
- Identify recurring AI/LLM cost.
- Identify messaging cost.
- Identify hosting cost.
- Identify database cost.
- Identify storage cost.
- Identify logging/monitoring cost.
- Identify camera/video processing cost.
- Identify drone integration cost.
- Identify third-party API cost.
- Define usage assumptions.
- Add cost alerts.
- Estimate low / expected / high usage.

### Deliverable
- Monthly Cost Model.

### Acceptance Criteria
- The expected monthly recurring cost does not exceed the approved interpretation of the 3,000 ILS limit.

**[DECISION REQUIRED] Question: What costs are included in the 3,000 ILS monthly limit?**

**[DECISION REQUIRED] Question: How is a "small settlement" defined for cost calculation purposes?**

Required parameters to define:
- Number of users
- Number of squad members
- Number of cameras
- Number of incidents per month
- Number of AI requests
- Amount of video analyzed
- Number of training exercises
- Messaging volume
- Data retention volume

---

# 25. Monitoring and Reliability

## 25.1 Service Monitoring

### Tasks
- Monitor agent availability.
- Monitor external integrations.
- Monitor message delivery.
- Monitor camera availability.
- Monitor model/API errors.
- Monitor latency.
- Monitor cost.
- Monitor simulator health.
- Generate system-health alerts.

### Deliverable
- R&D monitoring dashboard or equivalent status view.

**[DECISION REQUIRED] Question: What uptime/reliability target is expected during the R&D prototype?**

**[DECISION REQUIRED] Question: Who should receive technical system-health alerts?**

---

# 26. Evaluation Framework

## 26.1 Functional Evaluation

### Tasks
- Define test scenarios per agent.
- Define expected inputs and outputs.
- Test correct routing between agents.
- Test missing data.
- Test stale data.
- Test conflicting data.
- Test unavailable services.
- Test approval requirements.

### Deliverable
- Functional test suite.

## 26.2 AI Quality Evaluation

### Tasks
- Create a fixed evaluation dataset.
- Measure factual correctness.
- Measure unsupported claims.
- Measure missing critical information.
- Measure response usefulness.
- Measure response latency.
- Measure source attribution quality.
- Track regressions between versions.

### Deliverable
- AI evaluation report.

**[DECISION REQUIRED] Question: What accuracy / hallucination metric is required for the R&D to be accepted?**

**[DECISION REQUIRED] Question: Who defines the expected "correct" answer for each operational scenario?**

---

# 27. Scenario Library

## 27.1 Initial Training Scenarios

### Tasks
- Define scenario template.
- Build initial scenario set.
- Define expected system behavior.
- Define expected commander decision points.
- Define expected agent observations.
- Define expected final situation report.
- Define expected safety behavior.

### Deliverable
- Initial scenario library.

**[DECISION REQUIRED] Question: How many scenarios must be prepared during the one-month R&D period?**

**[DECISION REQUIRED] Question: Will subject-matter experts provide the scenarios, or is scenario creation part of the R&D team's responsibility?**

**[DECISION REQUIRED] Question: Which scenario categories are mandatory?**

---

# 28. Incident Lifecycle

## 28.1 Define Incident States

### Tasks
- Define incident creation.
- Define incident update.
- Define active incident state.
- Define escalation.
- Define resolution.
- Define closure.
- Define post-incident review.
- Define archiving.

### Deliverable
- Incident lifecycle specification.

**[DECISION REQUIRED] Question: What incident states should exist in the system?**

**[DECISION REQUIRED] Question: Who is authorized to open, update, escalate, and close an incident?**

---

# 29. Situational Picture Output

## 29.1 Situation Report Schema

### Candidate categories to explicitly approve
- Incident summary
- Time
- Location
- Confirmed facts
- Unconfirmed reports
- Squad availability
- Squad member status
- Camera observations
- Drone state
- External forces status
- Regional historical context
- Current risks
- Insights
- Missing information
- Recommendations
- Required commander decisions

### Tasks
- Approve the report schema.
- Define concise and expanded versions.
- Define formatting for messaging platform.
- Define freshness timestamp.
- Define automatic update behavior.

### Deliverable
- Situation Report template.

**[DECISION REQUIRED] Question: Which of the candidate fields above must be included?**

**[DECISION REQUIRED] Question: Should the system support both a short "one-screen" report and an expanded report?**

**[DECISION REQUIRED] Question: Should situation reports update automatically when important data changes?**

---

# 30. Notifications and Alerts

## 30.1 Alert Rules

### Tasks
- Define alert types.
- Define severity.
- Define intended recipients.
- Define deduplication.
- Define acknowledgement.
- Define escalation if ignored.
- Define quiet/routine periods if relevant.

### Deliverable
- Alert policy.

**[DECISION REQUIRED] Question: Which events should proactively trigger alerts?**

**[DECISION REQUIRED] Question: Who receives each type of alert?**

**[DECISION REQUIRED] Question: Should alerts escalate automatically if they are not acknowledged?**

---

# 31. Privacy and Data Retention

## 31.1 Privacy Requirements

### Tasks
- Identify personal data.
- Identify sensitive operational data.
- Minimize unnecessary collection.
- Define retention per data type.
- Define deletion process.
- Define access controls.
- Define export/reporting rules.

### Deliverable
- Data privacy and retention specification.

**[DECISION REQUIRED] Question: What personal data may be stored about squad members?**

**[DECISION REQUIRED] Question: How long may location, communications, camera-derived observations, and training logs be retained?**

---

# 32. Error Handling

## 32.1 User-Facing Failures

### Tasks
- Define clear error messages.
- Distinguish:
  - No data
  - Stale data
  - Integration failure
  - Model failure
  - Authorization failure
  - Service unavailable
- Provide a safe fallback.
- Avoid presenting fallback guesses as facts.

### Deliverable
- Error handling specification.

**[DECISION REQUIRED] Question: Which failures require immediate notification to the commander?**

---

# 33. One-Month R&D Planning

The source states a one-month R&D plan but does not assign individual capabilities to specific weeks.

## 33.1 Work Breakdown Planning

### Tasks
- Estimate each epic.
- Identify dependencies.
- Assign owners.
- Identify critical path.
- Identify work that can run in parallel.
- Define weekly demonstrations.
- Define end-of-month acceptance review.

### Deliverable
- One-month implementation plan.

**[DECISION REQUIRED] Question: Should the month be explicitly divided into Week 1 / Week 2 / Week 3 / Week 4 milestones?**

**[DECISION REQUIRED] Question: How many people are assigned to the project and what are their roles?**

**[DECISION REQUIRED] Question: Are there fixed dates for demo, pilot, or review meetings?**

---

# 34. Suggested Dependency Order

This section does **not** select technologies or operational policies. It only expresses logical dependency between work items.

1. Confirm scope and success criteria.
2. Confirm user roles and authorization boundaries.
3. Define agent responsibilities.
4. Define shared data models.
5. Define messaging/UI channel.
6. Build core Main Agent orchestration.
7. Build Squad Status Agent.
8. Build Regional History Agent.
9. Build Simulator.
10. Add Visual Agent with simulated or approved real sources.
11. Add External Forces Agent.
12. Add Insights Agent.
13. Add area briefing.
14. Add safety / cyber-security controls throughout implementation.
15. Run training scenarios.
16. Collect feedback.
17. Evaluate accuracy, safety, latency, and cost.
18. Prepare final R&D demonstration and gap list.

**[DECISION REQUIRED] Question: Is there any mandatory integration or capability that must be implemented earlier than this dependency order suggests?**

---

# 35. Cross-Cutting Definition of Done

For each implemented agent or capability, the following should be explicitly reviewed.

## Functional
- Inputs are defined.
- Outputs are defined.
- Failure behavior is defined.
- Dependencies are defined.
- Logs are generated.

## AI Safety
- Unsupported facts are not silently invented.
- Missing data is clearly represented.
- Uncertainty is visible.
- Restricted actions require the approved authorization level.

## Security
- Least-privilege access is used.
- Secrets are not exposed to users or model output.
- Untrusted external content cannot freely control agent tools.
- Sensitive operations are auditable.

## UX
- The user understands what the system knows.
- The user understands what the system does not know.
- The user understands whether an output is a fact, estimate, or recommendation.
- High-impact actions require clear confirmation where policy requires it.

## Cost
- Recurring usage can be measured.
- Cost by major service can be reported.
- High-cost usage patterns can be detected.

**[DECISION REQUIRED] Question: Which of these Definition-of-Done criteria are mandatory for the one-month R&D prototype, and which are deferred to later phases?**

---

# 36. Master Open-Decision Register

This section consolidates the unresolved decisions that must be answered before or during implementation.

## Product / Scope
1. What is the expected maturity level at the end of one month?
2. What exactly defines R&D success?
3. What is in scope, simulated, deferred, or out of scope?
4. What does "Build targets" mean in GTCA?

## Agent Behavior
5. Can agents only recommend, or may they execute actions?
6. Which actions require commander approval?
7. Can specialized agents communicate directly with one another?
8. How are conflicting agent outputs resolved?

## Messaging / UI
9. WhatsApp, Telegram, or both?
10. Which messaging integration/provider?
11. Messaging-only UI or separate dashboard?
12. Who are the user roles?

## Squad Status
13. What squad-member statuses exist?
14. How are statuses updated?
15. Is live location tracked?
16. What readiness/equipment data is tracked?

## Cameras / Visual
17. Real cameras, recorded video, simulated video, or a combination?
18. Which camera systems must be supported?
19. What must visual AI detect/describe?
20. What accuracy/confidence requirements apply?

## Drones
21. Real drone control or simulation in month one?
22. Which drone platform/API?
23. Which actions can the AI initiate?
24. Which drone actions require human confirmation?

## External Forces
25. Which organizations are included?
26. Are there real APIs?
27. Can the system contact them automatically?

## Regional History
28. What historical data exists?
29. Where is it stored?
30. What data is approved for use?
31. RAG, fine-tuning, prompts, rules, or another improvement method?

## Insights
32. Proactive or on-demand insights?
33. Which insight categories are mandatory?
34. What data sources feed each insight?
35. How is urgency/severity determined?

## Simulator / Training
36. What does the simulator need to simulate?
37. How are scenarios controlled?
38. How many initial scenarios?
39. Who authors and approves them?
40. What training data is stored?
41. Who labels outputs as correct/incorrect?
42. How is approved exercise data used to improve the system?

## Safety / Security
43. Is there an existing safe-agent environment?
44. Which data sources are untrusted?
45. Is authentication required in R&D?
46. What roles/permissions are required?
47. What data requires multi-source confirmation?

## Technology
48. Which LLM/provider is approved?
49. Is cloud AI allowed?
50. Is a local model required?
51. Which backend stack is required?
52. Which database is required?
53. Cloud, on-premise, private cloud, or hybrid?

## Cost
54. What is included in the 3,000 ILS monthly limit?
55. What defines a small settlement?
56. What usage assumptions should be used for cost modeling?

## Operations / Reliability
57. What response-time target is required?
58. What reliability target is required?
59. Which failures require immediate notification?
60. Who receives technical alerts?

## Data / Privacy
61. What personal data may be stored?
62. What are retention periods?
63. Who may access audit logs?

## Timeline / Team
64. Should the month be divided into weekly milestones?
65. How many people are on the project?
66. What are their roles?
67. Are there fixed demo/review dates?

---

# 37. Final R&D Deliverables Checklist

- [ ] Approved R&D scope
- [ ] Defined success criteria
- [ ] Agent architecture
- [ ] Agent responsibility matrix
- [ ] Human authorization matrix
- [ ] Main Agent prototype
- [ ] Squad Status Agent prototype
- [ ] Visual Agent prototype
- [ ] Camera integration or simulator
- [ ] Drone integration or simulator
- [ ] External Forces Agent prototype
- [ ] Regional History Agent prototype
- [ ] Insights Agent prototype
- [ ] Messaging interface
- [ ] Accessible user interface
- [ ] Area briefing capability
- [ ] Scenario simulator
- [ ] Training-data capture
- [ ] Post-exercise review workflow
- [ ] Hallucination-control rules
- [ ] Cyber-security boundaries
- [ ] Authentication/authorization implementation if required
- [ ] Audit logging
- [ ] Data model
- [ ] AI/LLM configuration
- [ ] Deployment environment
- [ ] Monitoring
- [ ] Cost model
- [ ] Functional tests
- [ ] AI-quality evaluation
- [ ] Initial scenario library
- [ ] End-of-month demonstration
- [ ] Open issues / Phase 2 backlog

---

# 38. Notes

- Any item marked **[DECISION REQUIRED]** must be explicitly answered by the project owner, commander, product owner, technical lead, or another authorized stakeholder before the implementation team treats it as a requirement.
- A decision should be recorded with:
  - Question
  - Selected answer
  - Decision owner
  - Decision date
  - Reason / constraint
  - Affected tasks
- Undefined decisions should remain `TBD`; they should not be silently inferred by the development team.
