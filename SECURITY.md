# Security Policy

WHITE ROOM is a local-first AI workbench. The default assumption is that project memory, provider keys, and runtime data are private.

## Supported Security Model

- Run locally unless you have reviewed the deployment surface.
- Bind the development server to `127.0.0.1` by default.
- Store provider keys in environment variables, `.env`, or `secrets.local.json`.
- Do not commit `.env`, `secrets.local.json`, `data/`, or private project memory.
- Treat `projects/` as local runtime state unless you have created sanitized demo projects.
- Use approval gates for cloud calls, costly actions, and execution lanes.

## Secret Handling

WHITE ROOM uses a local secrets layer with this precedence:

1. Process environment
2. `.env`
3. `secrets.local.json`

The UI should only show key presence and fingerprints. Raw keys must not appear in HTML, logs, packets, screenshots, exports, or database dumps.

Before publishing:

```powershell
rg -n "sk-|gsk_|api_key|token|secret|password|C:\\Users|gmail|Downloads|Desktop" .
```

Review every match manually. Test fixtures may contain fake keys; real keys must not appear anywhere.

## Public Release Rules

Do not publish:

- `data/`
- `.env`
- `secrets.local.json`
- `secrets.enc`
- private `projects/*` memory
- raw screenshots containing private paths, emails, tokens, or conversations

Use [docs/PUBLIC_RELEASE.md](docs/PUBLIC_RELEASE.md) before pushing to a public GitHub repo.

## Reporting Issues

If you find a secret leak, unsafe default, or provider-call bug, open a private issue or contact the maintainer directly before posting exploit details publicly.

Include:

- affected file or route
- reproduction steps
- expected safe behavior
- whether any real key, local path, or private memory was exposed
