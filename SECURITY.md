# Security Policy

WHITE ROOM is a local-first AI workbench. The default assumption is that project memory, provider keys, and runtime data are private.

For the full threat model and security assurance story, read [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).

## Supported Security Model

- Run locally unless you have reviewed the deployment surface.
- Bind the development server to `127.0.0.1` by default.
- Store provider keys in environment variables, `.env`, or `secrets.local.json`.
- Do not commit `.env`, `secrets.local.json`, `data/`, or private project memory.
- Treat `projects/` as local runtime state unless you have created sanitized demo projects.
- Use approval gates for cloud calls, costly actions, and execution lanes.
- Prefer local or manual lanes when a task does not require a cloud model.
- Send scoped task packets to providers instead of full project folders or long private threads.

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

## Cloud Provider Risk

Cloud AI providers are outside the local trust boundary. A cloud call can expose prompts, attached files, metadata, and usage patterns to that provider. WHITE ROOM reduces this risk by:

- keeping durable project memory local
- requiring explicit provider configuration
- showing key presence instead of raw values
- rejecting dashboard URLs where an API base URL is required
- routing by task size, mode, risk, cost, and capability
- using approval gates for live provider calls
- keeping local/model/manual lanes available when a cloud call is unnecessary

These controls reduce exposure; they do not make third-party providers private.

## Public Release Rules

Do not publish:

- `data/`
- `.env`
- `secrets.local.json`
- `secrets.enc`
- private `projects/*` memory
- raw screenshots containing private paths, emails, tokens, or conversations

Use [docs/PUBLIC_RELEASE.md](docs/PUBLIC_RELEASE.md) before pushing to a public GitHub repo.

Screenshots and tour assets must be captured from sanitized demo state. Do not publish real account dashboards, API key fingerprints tied to active accounts, private local paths, private project names, or personal conversations.

## Reporting Issues

If you find a secret leak, unsafe default, or provider-call bug, open a private issue or contact the maintainer directly before posting exploit details publicly.

Include:

- affected file or route
- reproduction steps
- expected safe behavior
- whether any real key, local path, or private memory was exposed
