# Public Release Guide

WHITE ROOM can be open sourced, but the working local repo contains runtime memory. Do not publish it blindly.

## Public Positioning

Use this description:

> WHITE ROOM is a local-first AI workbench that helps frequent AI users build production-grade projects with lower cost, better structure, and stronger privacy. It organizes work into project memory, task packets, provider routes, approvals, and handoffs so each step can use the cheapest capable model.

Avoid positioning it as:

- a generic chatbot
- a wrapper around one private provider
- a promise of unlimited free AI
- a finished SaaS

## What To Publish

Publish:

- source code
- templates
- tests
- fake/demo fixtures
- public docs
- screenshots without private data

Do not publish:

- `data/`
- `.env`
- `secrets.local.json`
- `secrets.enc`
- private `projects/*` memory
- personal screenshots
- provider keys
- local machine paths
- personal email addresses

## Pre-Publish Checklist

```powershell
git status --short
python -m pytest tests/test_phase15_secrets.py tests/test_secret_leak.py -q
rg -n "sk-|gsk_|C:\\Users|gmail|Downloads|Desktop|api_key|password|token|secret" .
```

Review every match. Fake test keys are acceptable only when clearly fake and used in tests.

## Suggested Public Repo Shape

```text
adapters/
bench/
cli/
core/
docs/
templates/
tests/
web/
.env.example
.gitignore
CONTRIBUTING.md
LICENSE
README.md
ROADMAP.md
SECURITY.md
pyproject.toml
requirements.txt
```

Keep `projects/` out of the initial public snapshot unless it contains a sanitized demo project.

## Release Narrative

The first public release should say:

- Alpha skeleton, not finished SaaS.
- Working local cockpit.
- Local project memory.
- Provider routing and approval gates.
- Secrets are local and fingerprinted.
- Users can extend it for their own providers and workflows.

## Proof To Show

Good proof artifacts:

- screenshot of cockpit with fake/demo project
- provider settings with fake fingerprints
- route decision panel
- task packet flow
- mermaid architecture diagram
- tests passing
- short demo GIF or video

Bad proof artifacts:

- private project names
- real provider dashboards
- account limit screenshots
- real API key fingerprints if tied to your account
- personal job hunt or interview content
