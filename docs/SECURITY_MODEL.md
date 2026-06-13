# Security Model

WHITE ROOM is designed around a simple security premise: the safest AI workflow is the one that does not send everything to the cloud.

This project does not claim to be immune to compromise. It reduces common AI-workbench risks by keeping memory local, narrowing what gets sent to providers, making live calls explicit, and treating provider keys as local secrets rather than UI data.

![WHITE ROOM local trust boundary](assets/trust-boundary.svg)

## Why This Is Security Work

Modern AI workflows create a new security surface. Sensitive data can leave an organization through prompts, pasted logs, attached files, generated tool calls, provider keys, screenshots, and persistent chat histories. The risk is not only "which model answered the question"; it is where context went, who can observe it, how much was sent, and whether the user had a reviewable reason for sending it.

WHITE ROOM is security work because it gives that surface structure:

- **Data minimization:** turn large project context into smaller task packets.
- **Boundary control:** keep project memory, task state, secrets, and route history local by default.
- **Explicit egress:** treat cloud providers as controlled exits from the local boundary.
- **Human approval:** gate live provider calls and execution lanes before sensitive or costly actions.
- **Auditability:** store route decisions, handoffs, usage estimates, and provider health locally.
- **Least-capable-lane routing:** prefer local or cheaper lanes when high-capability cloud models are not required.
- **Release hygiene:** separate private runtime state from public source snapshots.

This does not make AI usage risk-free. It changes the default shape of the workflow from "paste everything into a chat box" to "classify the task, attach the minimum useful context, route it deliberately, and record what happened."

## Security Problems It Helps Negate

| AI security problem | Common failure mode | WHITE ROOM mitigation |
| --- | --- | --- |
| Shadow AI | Users spread sensitive work across unmanaged tools because official workflows are too slow or expensive. | Provides a local workbench with local, manual, cloud, and custom gateway lanes. |
| Prompt/context leakage | Full files, logs, plans, and private notes get copied into remote chats. | Uses local project memory and scoped task packets to reduce what leaves the machine. |
| Secret sprawl | API keys appear in forms, screenshots, logs, browser history, or committed files. | Stores keys locally and renders only missing/active/fingerprint state. |
| Unreviewed cloud egress | Agents silently call remote providers or expensive models. | Routes by policy and keeps live/costly calls behind approval gates. |
| Weak incident reconstruction | After an AI-assisted change, nobody knows which model saw what or why. | Records route decisions, handoffs, health state, and usage estimates locally. |
| Provider lock-in | One hosted tool becomes the only workflow, even when local models would be enough. | Supports local runners, manual lanes, custom gateways, and provider adapters. |
| Public-source leakage | Builders accidentally publish local DBs, project memory, paths, or secrets. | Public snapshot rules, `.gitignore`, release guide, and leak tests target those failures. |

## Threats WHITE ROOM Is Built To Reduce

| Threat | Why it matters | WHITE ROOM control |
| --- | --- | --- |
| Accidental cloud data exposure | Chat tools often send entire files, plans, logs, or private notes to remote models. | Project memory stays local; task packets attach only scoped context. |
| Secret leakage in UI or logs | Provider keys can leak through HTML, screenshots, exports, logs, or database dumps. | Settings show presence and fingerprints only; secret values are stored outside git-tracked runtime files. |
| Expensive or unsafe live calls | A model/router can silently use a paid or external provider. | Live lanes require keys, status checks, route policy, and approval gates. |
| Dashboard URL confusion | Users may paste provider dashboard URLs instead of API base URLs, causing bad routing or key misuse. | Settings normalize/reject dashboard URLs and show the derived models probe URL. |
| Context sprawl | Long chats mix architecture, secrets, tasks, and debugging into one reusable prompt blob. | Memory, tasks, routes, handoffs, and packets are separate local artifacts. |
| Provider outage or rate limit failure | Agent flows can fail open or repeatedly hit unavailable providers. | Health checks, cooldown state, fallback lanes, and explicit unavailable states are tracked. |
| Public repo leakage | Local runtime state can contain private paths, emails, project names, and secrets. | Public release script excludes `data/`, `projects/`, `.env`, and local secrets; release scans are documented. |

## Security Boundaries

### Inside The Local Boundary

- FastAPI app bound to `127.0.0.1` for local development.
- SQLite runtime database in `data/`.
- Project brain files, task packets, handoffs, and artifacts in `projects/`.
- Provider secrets in process env, `.env`, or `secrets.local.json`.
- Route decisions, usage estimates, endpoint status, and approvals.

### Outside The Local Boundary

- Remote model APIs.
- Provider dashboards.
- Browser tabs not controlled by the app.
- Manual copy/paste LLM sessions.
- Published GitHub snapshots.

WHITE ROOM assumes anything outside the local boundary may observe the data intentionally sent to it. The router and approval model exist to make that transfer smaller, more deliberate, and easier to audit.

## Key Handling

Secrets are read in this order:

1. Process environment
2. `.env`
3. `secrets.local.json`

The UI must never render raw key values. It may render:

- key missing / key active state
- short fingerprint
- base URL
- model probe URL
- health result

Raw keys must not appear in:

- HTML responses
- logs
- generated packets
- exported project memory
- screenshots
- SQLite dumps intended for publication

## Cloud Safety

Cloud providers are useful, but they are not a private local boundary. WHITE ROOM treats cloud lanes as controlled exits:

- A key must be present.
- The endpoint must be configured as an API base URL.
- Health/model sync state must be visible.
- Route policy chooses the lane by mode, risk, cost, and capability.
- Approval gates protect live provider calls.
- Usage values are labeled as estimates, not bills.
- Removing a key should immediately disable or gate the lane.

## What WHITE ROOM Does Not Solve

- It does not make a third-party cloud model private.
- It does not prevent a user from manually pasting sensitive data into a provider.
- It does not replace host security, OS account protection, or network hardening.
- It does not make public deployment safe without review.
- It does not guarantee provider pricing, rate limits, or data retention policies.

## Operator Checklist

Before using real projects:

- Bind the app to `127.0.0.1` unless you have reviewed the deployment surface.
- Keep `.env`, `secrets.local.json`, `data/`, and `projects/` out of public commits.
- Use local models for drafts, summaries, extraction, and low-risk turns.
- Use cloud lanes only for scoped prompts that need them.
- Review route decisions and approval gates before live provider calls.
- Run the secret leak tests and release scan before publishing.

## Evidence In This Repo

- `tests/test_phase15_secrets.py` checks secret storage and redaction expectations.
- `tests/test_secret_leak.py` sweeps rendered HTML, packets, logs, fixtures, and database-like content for raw secrets.
- `tests/test_settings_providers.py` checks key save/remove behavior, base URL validation, fingerprints, and provider gating.
- `docs/PUBLIC_RELEASE.md` documents the public snapshot hygiene process.
- `scripts/create_public_snapshot.ps1` builds a publishable snapshot without private runtime folders.
