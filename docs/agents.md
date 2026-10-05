# BOUND Agent Framework

BOUND agents are analytical workers, not independent administrators.

## Non-negotiable execution boundary

Agents cannot directly execute tools.

An agent may:
1. observe evidence;
2. analyse;
3. recommend;
4. if explicitly permitted by its definition, create an action proposal.

An action proposal is only a proposal. The deterministic agent gate evaluates
whether the proposal is even eligible. Any real tool execution must still pass
through the normal BOUND tool registry, environment policy, argument binding,
audit logging and human approval rules.

Write-capable tool approvals are single-use and are consumed immediately before
execution. A failed action does not restore the approval.

Agent tool allowlists are deny-by-default. An empty allowlist means no tools.

## Oversight

Sentinel is the evidence and consistency overseer. By default
BOUND_SENTINEL_MODE=always, so successful specialist runs receive an
independent review.

Sentinel is not a truth oracle. Its job is to identify unsupported certainty,
contradictions, missing evidence, unsafe recommendations and claims whose
provenance is unclear.

Deterministic checks also run before Sentinel and flag:
- facts with no evidence references;
- references that were never supplied;
- high-confidence unsupported claims;
- write recommendations that do not explicitly require human approval;
- factual output produced with no evidence packet.

## Initial agents

### Operator
Incident-command style coordinator. Synthesizes specialist findings and proposes
the safest next step. It may propose actions but currently has an empty tool
allowlist, so no tool can be actioned through it.

### Sentinel
Suspicious senior auditor. Challenges evidence, confidence and causal claims.
Observe-only.

### Hardware
Veteran bench technician. Focuses on power, physical interfaces, thermals,
firmware, storage, compute, networking hardware and measurable device state.

### Software
Pedantic senior engineer. Focuses on code, services, operating systems,
databases, APIs, dependencies, configuration, logs, reproduction and rollback.

### Reasoning
Skeptical logician. Generates competing explanations, challenges causal claims
and searches for disconfirming evidence.

### Problem Solver
Pragmatic field engineer. Decomposes messy problems and chooses the cheapest,
safest experiment that most reduces uncertainty.

### Security
Change-control security engineer. Reviews privilege, authentication, secrets,
exposure, blast radius and rollback. Observe-only and evidence-required.

### Evidence
Forensic note-taker. Tracks provenance, conflicts, unsupported claims and what
additional evidence would resolve uncertainty.

## Specialist panel order

The panel selector is deterministic rather than model-driven.

Base panel:
1. Reasoning
2. Problem Solver

Then, when relevant:
3. Hardware
4. Software
5. Security, only when evidence exists and the task has security/change impact
6. Evidence, when an evidence packet exists

Operator then synthesizes the panel, and Sentinel reviews the Operator output.

No task is executed by the panel.

## Runtime modes

Agent definitions support:
- interactive
- on-demand
- scheduled
- event-driven

The initial release stores runtime state but does not start autonomous scheduled
execution. Scheduling should only be enabled after per-agent budgets, rate
limits, failure backoff, tool scopes and operator controls are implemented.

## API

- GET /agents
- POST /agents/{agent_id}/run
- POST /agents/panel/run
- POST /agents/{agent_id}/enabled
- GET /agents/runs/recent
- GET /agents/concerns
- POST /agents/actions/propose

POST /agents/actions/propose does not execute anything.
