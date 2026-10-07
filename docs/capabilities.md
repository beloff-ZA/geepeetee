# BOUND Capability Trust Pipeline

BOUND treats new agent capability as untrusted input. A skill, agent package, MCP
integration or workflow must enter quarantine before it can become part of the
runtime.

## Trust path

```text
Quarantine -> SkillSpector -> BOUND evaluation -> human approval -> installed trust state
```

"Installed" in the capability registry does not execute a package. Package
acquisition, dependency installation and runtime activation remain separate
operator-controlled actions.

## Phase 1: NVIDIA SkillSpector

SkillSpector is an external security service/CLI. It is intentionally not
installed into BOUND's Python virtual environment because SkillSpector currently
requires Python 3.12+ and has its own dependency surface.

Install it in an isolated tool environment on the BOUND host. With `uv`:

```bash
uv tool install git+https://github.com/NVIDIA/skillspector.git
skillspector --help
```

If the command is not on the system service PATH, set the absolute executable
path in `/opt/bound/.env`:

```dotenv
BOUND_SKILLSPECTOR_BIN=/home/beloff/.local/bin/skillspector
BOUND_SKILLSPECTOR_TIMEOUT=240
BOUND_SKILLSPECTOR_SEMANTIC=false
BOUND_CAPABILITY_QUARANTINE_ROOT=/var/lib/bound/capabilities/quarantine
```

Then create the quarantine directory and keep it writable only by the BOUND
service account:

```bash
sudo mkdir -p /var/lib/bound/capabilities/quarantine
sudo chown beloff:beloff /var/lib/bound/capabilities/quarantine
chmod 750 /var/lib/bound/capabilities/quarantine
```

Static scanning is mandatory. Semantic scanning is optional and disabled by
default. If the scanner is missing, times out, fails to produce a valid report,
or reports incomplete analysis, the capability does not bypass quarantine.

GitHub HTTPS and raw GitHub HTTPS sources are accepted by default. Local sources
must resolve beneath the quarantine root. Other remote URL sources remain
disabled unless `BOUND_CAPABILITY_ALLOW_REMOTE_URLS=true` is deliberately set.

## New agents

The original BOUND roster is bootstrap-trusted so the existing system does not
disable itself on upgrade. Any new code-defined agent ID is fail-closed:

- it is inserted into runtime state disabled;
- its paused reason is `unscanned_agent_security_gate`;
- an operator cannot enable it until an approved or installed capability record
  exists for that agent ID.

This prevents a future catalog edit from silently creating another authority
surface.

## Capability evaluation

A capability that passes the security gate can be evaluated by BOUND's Security,
Reasoning, Alternative Solutions and Business Management specialists, followed
by Operator synthesis.

The evaluator receives only declared metadata and deterministic scan facts. Raw
untrusted skill content and scanner excerpts are not placed into the agent
prompt.

Evaluation is advisory. It does not approve or install the capability.

## FreeLLMAPI inference gateway

BOUND can use a local FreeLLMAPI instance as the preferred inference gateway
while retaining its direct provider integrations as fallback.

Recommended local configuration:

```dotenv
FREELLMAPI_BASE_URL=http://127.0.0.1:3001/v1
FREELLMAPI_API_KEY=<local unified key>
FREELLMAPI_MODEL=auto
BOUND_PROVIDER_ORDER=freellmapi,openai,gemini,groq,openrouter
```

FreeLLMAPI should remain bound to loopback unless it is separately protected.
BOUND owns agent identity, memory, tools, approvals and policy. FreeLLMAPI owns
model/provider routing.

## Adoption policy

Patterns from FreeLLMAPI and Awesome LLM Apps are tracked in the Capabilities
workspace as:

- **In BOUND**: architecture already adopted or represented in the current build.
- **Next candidates**: a strong fit, retained for implementation.
- **Retained for later**: plausible but requires a safer or more mature boundary.
- **Definitely not BOUND**: only functionality clearly outside BOUND's IT
  operations, consulting, infrastructure, research, documentation, automation
  or business-support mandate.

The policy is intentionally biased toward retention. Novelty is not a reason to
discard a capability.

## Deployment warning

The current BOUND HTTP API does not yet have application authentication. Do not
expose the API or capability intake endpoints directly to the public internet.
Use a trusted local/Tailscale path until authentication and authorization are in
place.
