# NASA Space Apps development

Keep work inside this workspace. Preserve existing work. Ask the user before deleting important files, changing credentials, enabling paid services, pushing, deploying publicly, or submitting an entry. Keep secrets out of prompts, logs, files, and evidence. Inspect downloaded scripts before running them.

## Workflow

1. **Discovery.** Inspect the workspace, configuration, tools, and live model catalog. Verify a usable provider with a small request. Use the official 2026 challenge page once the team selects a challenge.
2. **Planning.** Extract requirements and define one demonstrable MVP. Confirm data access, methods, and constraints before choosing a minimal stack. Describe interfaces and give each task an owner, dependencies, inputs, outputs, error cases, and acceptance checks.
3. **Implementation.** Delegate independent work with explicit file ownership. Wait for dependencies and avoid overlapping edits. Integrate in small, runnable increments.
4. **Verification.** Run relevant tests and check interfaces, edge cases, source provenance, scientific calculations, secret exposure, and documented limitations. Fix blocking issues before optional work.
5. **Delivery.** Verify the demo from the documented run steps and prepare the README, architecture and data source notes, pitch, and submission draft. Check the result against the selected challenge.

The team selects the challenge. Until then, limit work to the development environment and challenge discovery. Design for the selected challenge once confirmed.

## Routing

When running in Codex CLI, use Codex for architecture and integration and dispatch OpenCode specialists from the repository root with `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\Invoke-Specialist.ps1 -Role qa -Task 'Your scoped task'`. Replace `qa` with the relevant role. Use `-List` to inspect role-to-model mappings without inference. Report the role, exact model ID, session result, and OpenCode cost estimate for each call. Assign disjoint paths when delegating implementation in parallel. The OpenCode UI orchestrator requires its own verified ChatGPT OAuth Codex route and remains disabled while that route is unavailable.

Use the current project's `opencode models` catalog and provider status to choose exact model IDs. A catalog entry still needs a successful request. Prefer Codex through the existing OpenAI ChatGPT OAuth connection for architecture and integration, DeepSeek V4.1 Flash for Backend and Data only (at most two team agents), Gemini Flash for research, GPT-OSS 120B for scientific verification, Qwen or verified ERNIE for frontend, Inkling 2 Small for review, and Particle-1 for documentation. Consider other capable catalog models after a successful probe, not diversity alone. Never silently substitute GPT-OSS 20B for 120B or another model for Codex. If no suitable verified route exists, record UNCONFIGURED in the routing report and disable that agent; do not invoke it or leave it to inherit the primary model. UNCONFIGURED is a status, not a model ID. Do not connect new paid APIs or raise provider tiers. Existing-provider usage can have nonzero cost and must be reported honestly. See MODEL-ROUTING.md for current evidence and limitations.

Only report an agent's work after its actual result arrives. Record changed files, tests and outcomes, blockers, and the next action. Cite primary sources with access dates, license and access restrictions when known. Mark observation, simulation, and mock data explicitly and preserve units and uncertainty.
