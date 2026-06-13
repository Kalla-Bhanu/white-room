# Contributing

WHITE ROOM is an alpha-stage local-first AI workbench. Contributions should preserve the core promise: lower cost, clearer project structure, and stronger privacy.

## Principles

- Keep local-first behavior by default.
- Never render or log raw provider keys.
- Prefer explicit routing decisions over invisible model switching.
- Add approval gates before costly or risky live calls.
- Make incomplete capabilities visible as alpha, demo, or planned work.
- Keep provider-specific code behind adapters.
- Add focused tests for routing, secrets, settings, and provider behavior.

## Development Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Run the app:

```powershell
python -m uvicorn web.server:app --host 127.0.0.1 --port 8765
```

Run tests:

```powershell
python -m pytest -q
```

## Pull Request Checklist

- No real secrets, local private paths, or private conversations.
- Tests added or updated for behavior changes.
- Provider errors distinguish local approval, auth, rate limit, model mismatch, and remote bad request.
- Public docs updated if the user workflow changes.
- Screenshots are sanitized.

## Commit Style

Use short, behavior-oriented commit messages:

```text
Add provider URL validation
Fix Groq model selection
Document public release workflow
```
