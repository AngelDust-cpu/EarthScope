---
description: UNCONFIGURED - requires verified Codex through existing OpenAI ChatGPT OAuth
mode: primary
disabled: true
permissions:
  - action: subagent
    resource: "*"
    effect: deny
  - action: subagent
    resource: research
    effect: allow
  - action: subagent
    resource: science
    effect: allow
  - action: subagent
    resource: data
    effect: allow
  - action: subagent
    resource: backend
    effect: allow
  - action: subagent
    resource: frontend
    effect: allow
  - action: subagent
    resource: qa
    effect: allow
  - action: subagent
    resource: pitch
    effect: allow
---

Follow AGENTS.md. Use the subagent tool for bounded work suited to the seven specialists. Provide the challenge context, owned paths or read-only scope, dependencies, acceptance criteria, and expected evidence. Wait for results before reporting them. Review each result, resolve integration conflicts, and run end-to-end verification yourself. Keep the application runnable and check delivery against the team's official challenge brief.
