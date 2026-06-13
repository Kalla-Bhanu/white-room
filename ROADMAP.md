# Roadmap

WHITE ROOM is currently an alpha skeleton with a working local cockpit and live provider lanes. The roadmap is intentionally practical: make it reliable, safe, and easy to extend locally.

## Now

- Polish public documentation and release hygiene.
- Keep private runtime state out of public snapshots.
- Stabilize Groq and OpenAI-compatible provider error handling.
- Improve model compatibility rules for chat, audio, guard, and reasoning models.
- Add clearer user-facing states for approval required, sync required, and remote rejection.

## Next

- Demo project mode with fake providers and deterministic responses.
- First-run setup wizard.
- Public screenshots and short demo video.
- Cost budget profiles by project.
- Automatic prompt compression for large local files.
- Task-specific model policies.
- Security policy packs for redaction, cloud-egress review, and provider allow/deny rules.
- Audit export for route decisions, approvals, provider calls, and sanitized handoffs.
- Provider capability matrix.
- Stronger import/export for sanitized project memory.

## Later

- Plugin interface for custom providers.
- Team-safe local sharing mode.
- Optional encrypted secrets backend.
- Deployment hardening guide for reverse proxies, auth, TLS, and network exposure.
- More benchmark fixtures for project planning, code review, debugging, research, and summarization.
- Agent lane scheduler for multi-step work.
- Release packages for Windows, macOS, and Linux.

## Not Goals

- Selling unlimited AI.
- Hiding provider costs or limits.
- Sending all project files to every model.
- Replacing careful human review for production changes.
