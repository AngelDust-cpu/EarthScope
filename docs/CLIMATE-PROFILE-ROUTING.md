# Climate profile model routing evidence — 2026-11-15

Active configuration preserved from MODEL-ROUTING.md and `.opencode/agents`. Live `Invoke-Specialist.ps1 -List` and `opencode models --standalone` inspected in this worktree before dispatch. Catalog availability alone is not treated as successful inference.

| Role | Exact model | Session | Result | OpenCode cost estimate |
|---|---|---|---|---|
| Science | nvidia/nvidia/nemotron-3-ultra-550b-a55b | ses_e22e9225fffeJkWDqCAMiKczNa | Completed read-only science architecture review | 0 USD |
| Data | opencode/glm-5.3-flash-free | ses_e22e5c0b8ffeqIH062b7LZsR1L | Completed read-only daily-cache/provenance review | 0 USD |

Backend and Frontend implementation calls are in progress and will be recorded after their actual final results. QA runs after implementation. Codex owns integration locally. No new provider, account or paid tier was configured. Cost fields are OpenCode estimates and do not measure ChatGPT allowance usage or prove zero provider charges.

Science review corrections retained by integrator: the required fetch start is min(display start minus one year, baseline start), clamped to 1981. Baseline completeness is an app policy. Do not claim seasonal differences are tested. No significance conclusion when the primary extremes statistic is null.

| Frontend | openai/gpt-6.1-sol#medium | ses_e22e97374ffeCF0bThFvkzpjPm | Completed six frontend files and 7 client tests. Agent could not find npm. Integrator used existing Node/npm and verified production build. | 0 USD |

Frontend probe session ses_e22e8fbccffeyDa61ccYTGc2Vu returned FRONTEND-PROFILE-LIVE. Wrapper did not measure this separate probe cost. Session metadata reported 0, which is an estimate. Integrator made compact summary and anomaly/seasonal overview visible after the initial frontend result.

| Backend | opencode/deepseek-v4.1-flash-free | ses_e22e9b560ffeGuU8Hqybo79HOI | Probe succeeded; wrote four backend files. Wrapper stopped with Free usage limit reached (exit 1). Codex completed integration, corrected four erroneous test expectations and finished verification. | 0 USD |
| QA | opencode/inkling-2-small-free | ses_e22e39714ffefaNdKSeBxYFd0D | Completed static review. Findings addressed by Codex, with final browser/test evidence recorded separately. | 0 USD |

No model was silently substituted. Backend session metadata later marked the session succeeded, while the actual wrapper returned exit 1 with a usage limit error. This report uses the actual wrapper outcome as the completion evidence.
